#!/usr/bin/python
import numpy as np
import matplotlib.pyplot as plt
import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('txtfile', nargs='+',
                    type=Path,
                    help='ascii-files to plot')
args = parser.parse_args()

adc_to_volt = 76.294e-6

fig, axes = plt.subplots(nrows=2, sharex=True, 
        gridspec_kw={'height_ratios':[2,1]})
ax1, ax2 = axes.flat

ax2.set_xlabel('Sample #')
ax1.set_ylabel('ADC Voltage [V]')
ax2.set_ylabel('ADC Status')
ax2.set_yticks([0,1,2,5,6,7])
ax2.set_yticklabels(['Rst','Hold','Int','Rst','Hold','Int'])

for k, txtfile in enumerate(args.txtfile) :
    tstamp, status1, adc1, status2, adc2 = np.loadtxt(txtfile, dtype='i', unpack=True)
    adc1_v = adc1 * adc_to_volt
    adc2_v = adc2 * adc_to_volt
    if k == 0 :
        label1 = f'{txtfile.name} Ch1'
        label2 = f'{txtfile.name} Ch2'
    else :
        label1 = txtfile.name
        label2 = None
    ax1.plot(adc1_v, f'-C{k}', label=label1)
    ax1.plot(adc2_v, f'--C{k}', label=label2)
    ax2.step(status1, f'-C{k}')
    ax2.step(status2+5, f'--C{k}')

ax1.legend(fontsize='small')
plt.show()
