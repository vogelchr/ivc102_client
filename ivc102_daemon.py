#!/usr/bin/env ./venv/bin/python
import argparse
from pathlib import Path
import logging
import json
from logging import debug, info, warning, error
from typing import Optional

import serial
import asyncio
import serial_asyncio
import aiohttp

from aiohttp import web

status_to_int = {
    b'R': 0,
    b'H': 1,
    b'I': 2
}


def try_parse_line(line: bytes):
    """
    Try to parse one line of output from the IVC102 module.

    :param line: bytes
    :return: tuple(int(tstamp), str(status1), int(adc1), str(status2), int(adc2))

    #       0000000000111111111122222222
    #       0123456789012345678901234567
    line: b'000336 I 00019790 R 00000001'
    """

    arr = line.split()
    return int(arr[0]), status_to_int[bytes(arr[1])], int(arr[2]), status_to_int[bytes(arr[3])], int(arr[4])


class IVC102_Tty(asyncio.Protocol):
    transport = None
    recvbuf = None
    expect_data = False
    sent_stop = False
    sent_start = False
    txqueue = None
    rx_data_buf = None
    good_rx = 0
    bad_rx = 0
    drop_rx = 0

    def __init__(self, prog_args, *args, **kwargs):
        self.prog_args = prog_args
        self.txqueue = list()
        self.rx_data_buf = list()

        super().__init__(*args, **kwargs)

    # if I'm not super slow here, sometimes we miss a character on Receive,
    # e.g.
    # 2020-12-19 21:26:01,530 Sending command "b'ivc_etrig\n'".
    # Unknown command (ivetrig). PLease repeat

    async def tx_really_really_slow(self, buf):
        for i in range(len(buf)):
            self.transport.write(buf[i:i + 1])
            await asyncio.sleep(0.01)

    async def tx_timer_task(self):
        while True:
            if not self.txqueue:
                await asyncio.sleep(0.5)
                continue

            arr_or_bytes = self.txqueue.pop(0)
            t = 0.1
            if type(arr_or_bytes) == tuple:
                b, t = arr_or_bytes
            else :
                b = arr_or_bytes
            if type(b) == str :
                b = b.encode('ascii')

            b_str_info = b.decode('ascii').replace('\r','\\r').replace('\n', '\\n')
            info('Tx: \033[33;1m%s\033[0m', b_str_info)
            await self.tx_really_really_slow(b)
            await asyncio.sleep(t)

    def connection_made(self, transport: serial_asyncio.SerialTransport) -> None:
        self.transport = transport
        self.recvbuf = bytearray()
        self.expect_data = False
        self.sent_stop = False
        self.sent_start = False

        self.txqueue += [
            ('sys_rst\n', 3),  # reset, wait 3 seconds
            'sys_info\n',
            'ivc_csref\n', '0\n',
            'ivc_csgnd\n', '1\n',
            'adc_trgv\n', '4.00\n', # adc trigger voltage
            'ivc_dtrig\n', 't\n',  # disable integration time trigger
            'ivc_dtrig\n', 'c\n',  # disable comparators
            'ivc_etrig\n', 'v\n',  # enable voltage
            'ivc_nseq\n', '04 00 02 03 03 03 03 04 03 02 07 01 02\n', # sequence
        ]

        info('Serial connection to %s.', transport.serial.name)

        asyncio.create_task(self.tx_timer_task())

    def stop(self):
        debug('Stopping Measurement...')
        self.transport.write(b'\004')  # Ctrl-D, stop!
        self.sent_stop = True

    def start(self):
        if len(self.txqueue):
            raise RuntimeError('Cannot start while there are commands in tx queue!')

        debug('Starting Measurement...')
        self.transport.write(b'\003')  # Ctrl-C, start!
        self.sent_start = True

    def enqueue_command(self, commands):
        if self.expect_data or self.sent_start:
            raise RuntimeError('Can only enqueue commands while system is not measuring!')
        self.txqueue += commands

    def data_received(self, data: bytes) -> None:
        self.recvbuf += data

        while True:
            ix_cr = self.recvbuf.find(b'\r')
            if ix_cr == -1:
                break

            line = self.recvbuf[0:ix_cr]
            del self.recvbuf[0:ix_cr + 1]

            if line.endswith(b'\n'):
                del line[-1]
            if line.startswith(b'\n'):
                del line[0]

            try:
                line_str = line.decode('ascii')
            except UnicodeDecodeError as exc:
                line_str = repr(line)

            if args.verbose or not self.expect_data:
                line_str_info = line_str.replace('\r','\\r').replace('\n', '\\n')
                info('Rx: \033[32;1m%s\033[0m', line_str_info)

            if self.sent_stop and line_str.find('Stop Measurement') != -1:
                self.sent_stop = False
                self.expect_data = False

            try:
                tstamp, status1, adc1, status2, adc2 = try_parse_line(line)

                self.good_rx += 1
                self.rx_data_buf.append(
                    (tstamp, status1, adc1, status2, adc2)
                )

                if len(self.rx_data_buf) > 512:
                    self.drop_rx += len(self.rx_data_buf) - 512
                    del self.rx_data_buf[0:len(self.rx_data_buf) - 512]

                if not self.expect_data and not self.sent_stop:
                    warning('Received unexpected valid data, forcing stop!')
                    self.stop()

            except Exception as exc:
                if self.expect_data:
                    self.bad_rx += 1
                    warning('Cannot parse data: "%s" exception: %s', line_str, repr(exc))

            if self.sent_start and line_str.find('Start Measurement') != -1:
                self.expect_data = True
                self.sent_start = False
                self.rx_data_buf.clear()

        # make sure we don't accumulate too much data, this will for sure
        # indicate a stupid error
        if len(self.recvbuf) > 512:
            warning('Warning, length of recv buffer exceeded, dropping data.')
            del self.recvbuf[0:len(self.recvbuf) - 512]

    def connection_lost(self, exc: Optional[Exception]) -> None:
        warning('Serial connection lost: %s', repr(exc))


def handle_start(ivc102_proto, payload: dict):
    ivc102_proto.start()
    return 0, 'Yeah!'


def handle_stop(ivc102_proto, payload: dict):
    ivc102_proto.stop()


def handle_status(ivc102_proto: IVC102_Tty, payload: dict):
    return {
        'result': 0,
        'msg': 'Here\'s your status!',
        'good_rx': ivc102_proto.good_rx,
        'bad_rx': ivc102_proto.bad_rx,
        'expect_data': ivc102_proto.expect_data,
        'drop_px': ivc102_proto.drop_rx,
        'nsamples': len(ivc102_proto.rx_data_buf)
    }


def handle_fetch(ivc102_proto: IVC102_Tty, payload: dict):
    nsamples = payload.get('nsamples', None)
    if type(nsamples) is not int or nsamples < 0 or nsamples > len(ivc102_proto.rx_data_buf):
        return 666, 'Not the correct number of samples!'
    ret = {
        'result': 0,
        'msg': 'Here\'s your data!',
        'data': ivc102_proto.rx_data_buf[0:nsamples]
    }
    del ivc102_proto.rx_data_buf[0:nsamples]
    return ret


ws_cmd_handlers = {
    'start': handle_start,
    'stop': handle_stop,
    'status': handle_status,
    'fetch': handle_fetch
}


async def handle_ws(request, ivc102_proto):
    debug('Websocket opening...')
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    debug('Websocket opened.')

    async for msg in ws:
        if msg.type == aiohttp.WSMsgType.TEXT:
            try:
                payload = json.loads(msg.data)
            except Exception as exc:
                error('Websocket: Got exception %s during decode of possible json payload.', repr(exc))
                await ws.close(code=aiohttp.WSCloseCode.PROTOCOL_ERROR, message=b'Cannot decode json!')
                break

            if type(payload) is not dict:
                error('Websocket: got a non-dictionary payload.')
                await ws.close(code=aiohttp.WSCloseCode.PROTOCOL_ERROR, message=b'Json is not a dict.')
                break

            cmd = payload.get('cmd', None)
            if cmd == 'close':
                await ws.close(code=aiohttp.WSCloseCode.OK, message=b'Bye!')
                break

            if not cmd or cmd not in ws_cmd_handlers:
                error('Websocket: got invalid command %s', cmd)
                await ws.close(code=aiohttp.WSCloseCode.PROTOCOL_ERROR, message=b'Command is not defined.')
                break

            ret = ws_cmd_handlers[cmd](ivc102_proto, payload)
            if ret is None:
                ret = {'result': 0, 'msg': 'Maybe ok?'}
            elif type(ret) is int:
                ret = {'result': ret, 'msg': 'Maybe ok? Maybe not.'}
            elif type(ret) is tuple and len(ret) == 2 and type(ret[0]) is int and type(ret[1]) is str:
                ret = {'result': ret[0], 'msg': ret[1]}

            ret['cmd'] = cmd

            await ws.send_json(ret)

        elif msg.type == aiohttp.WSMsgType.ERROR:
            error('ws connection closed with exception %s' %
                  ws.exception())

    debug('Websocket closed.')
    return ws


if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument('-q', '--quiet', action='store_true',
                        help='Less logging.')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='More logging.')

    parser.add_argument('-t', '--tty', type=Path, default='/dev/ttyUSB0',
                        help='Tty to use [%(default)s]')
    parser.add_argument('-b', '--baud', type=int, default=3000000,
                        help='Baudrate to use [%(default)d]')
    args = parser.parse_args()

    default_level = logging.INFO
    if args.quiet:
        default_level = logging.WARNING
    if args.verbose:
        default_level = logging.DEBUG

    logging.basicConfig(level=default_level, format='%(asctime)s %(message)s')

    loop = asyncio.get_event_loop()
    ivc102_transport, ivc102_proto = loop.run_until_complete(
        serial_asyncio.create_serial_connection(
            loop,
            lambda: IVC102_Tty(args),
            args.tty.as_posix(), args.baud
        )
    )

    app = web.Application()
    app.add_routes([web.get('/ws', lambda req: handle_ws(req, ivc102_proto)),
                    ])
    web.run_app(app)
