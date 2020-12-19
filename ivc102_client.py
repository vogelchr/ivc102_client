#!/usr/bin/env ./venv/bin/python
import argparse
import logging
import json
from logging import debug, info, warning, error
from pathlib import Path

import asyncio
import aiohttp

async def client_ctrl_task(ws, args) :
    await asyncio.sleep(1.0)
    await ws.send_json({'cmd': 'start'})
    while args.number_of_samples > 0 :
        info('Number of samples remaining: %s', args.number_of_samples)
        await asyncio.sleep(0.2)
        await ws.send_json({'cmd': 'status'})
    await ws.send_json({'cmd': 'stop'})
    await asyncio.sleep(1.0)
    await ws.close(code=aiohttp.WSCloseCode.GOING_AWAY, message='Bye!')


async def client_ws_task(args):

    of = None
    if args.output :
        of = open(args.output, 'wt')

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect('http://127.0.0.1:8080/ws') as ws:
            loop.create_task(client_ctrl_task(ws, args))
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        payload = json.loads(msg.data)
                    except Exception as exc:
                        error('Cannot json-decode answer!')
                        break

                    cmd = payload.get('cmd', None)

                    if cmd == 'fetch' :
                        data = payload.get('data', None)
                        if of :
                            for arr in data :
                                print(' '.join(f'{v}' for v in arr), file=of)
                        if args.number_of_samples > 0 :
                            args.number_of_samples -= min(args.number_of_samples, len(data))

                        debug('Got %d samples.', len(data))
                    elif cmd == 'status' :
                        nsamples = payload.get('nsamples', None)
                        if type(nsamples) == int :
                            await ws.send_json({'cmd': 'fetch', 'nsamples': nsamples})
                    else :
                        debug('Received answer: %s', repr(payload))


                elif msg.type == aiohttp.WSMsgType.ERROR:
                    error('Received websocket error: %s', repr(msg))
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSE :
                    error('Received close message: %d', repr(msg))

            # if we closed ourselves, it will be None
            if ws.close_code is not None :
                info('Websocket closed, close code is: %s', aiohttp.WSCloseCode(ws.close_code))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-q', '--quiet', action='store_true',
                        help='Less logging.')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='More logging.')
    parser.add_argument('-o', '--output', type=Path,
                        help='Output file.')
    parser.add_argument('-N', '--number-of-samples', type=int, default=10000,
                        help='Total number of samples to take (approximately) [%(default)d]')
    args = parser.parse_args()

    default_level = logging.INFO
    if args.quiet:
        default_level = logging.WARNING
    if args.verbose:
        default_level = logging.DEBUG

    logging.basicConfig(level=default_level, format='%(asctime)s %(message)s')

    loop = asyncio.get_event_loop()
    loop.run_until_complete(client_ws_task(args))
