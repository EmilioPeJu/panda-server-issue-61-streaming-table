#!/usr/bin/env python
import argparse
import logging
import multiprocessing
import numpy as np
import time

from common import Client

log = logging.getLogger(__name__)
PGEN_NAME = 'PGEN'
layout = \
'''PGEN1.ENABLE=ZERO
PGEN1.OUT.UNITS=
PGEN1.OUT.OFFSET=0
PGEN1.OUT.SCALE=1
PGEN1.ENABLE.DELAY=0
PGEN1.TRIG.DELAY=0
PGEN1.REPEATS=1
PGEN1.TRIG=CLOCK1.OUT
PGEN1.TABLE<

CLOCK1.ENABLE=PGEN1.ACTIVE
CLOCK1.ENABLE.DELAY=0
CLOCK1.PERIOD.UNITS=s
CLOCK1.WIDTH.UNITS=s
CLOCK1.PERIOD=1
CLOCK1.WIDTH=0
PCAP.ENABLE=PGEN1.ACTIVE
PCAP.ENABLE.DELAY=10
PCAP.TRIG=CLOCK1.OUT
PCAP.TRIG.DELAY=1
PCAP.TRIG_EDGE=Rising
PCAP.GATE=ONE
PCAP.GATE.DELAY=0
PCAP.SHIFT_SUM=0
PCAP.TS_TRIG.CAPTURE=No
PGEN1.OUT.CAPTURE=Value
*METADATA.LAYOUT<
{"PCAP": {"x": 261.52631578947376, "y": 51.70570087098238},
"CLOCK": {"x": -2.5, "y": 215.5},
"CLOCK1": {"x": -215.21052631578948, "y": 118.1004361068994},
"PGEN1": {"x": -18.473684210526244, "y": -20.906579509534367}}

'''.replace('PGEN1', PGEN_NAME)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--lines-per-block', type=int, default=16384)
    parser.add_argument('--clock-period-us', type=float, default=0.4)
    def nblocks_type(s):
        val = int(s)
        assert val > 0
        return val
    parser.add_argument('--nblocks', type=nblocks_type, default=1)
    parser.add_argument('host')
    return parser.parse_args()


def handle_pgen(args):
    client = Client(args.host)
    client.connect()
    client.load_state(layout)
    bw = 4 / (args.clock_period_us * 1e-6) / 1024**2
    client.PGEN.REPEATS.put(1)
    client.CLOCK1.PERIOD.put(args.clock_period_us * 1e-6)
    print(f'Lines per block {args.lines_per_block}')
    print(f'Number of blocks {args.nblocks}')
    print(f'Clock period {args.clock_period_us} us')
    print(f'Bandwidth {bw:.3f} MB/s')
    print(f'Total size {args.lines_per_block * args.nblocks * 4 / 1024**2:.3f} MB')
    block_start = 0
    block_stop = args.lines_per_block
    for i in range(args.nblocks):
        t1 = time.time()
        content = np.arange(block_start, block_stop, dtype=np.uint32)
        print(f'Pushing table {i} from {block_start} to {block_stop - 1}')
        block_start += args.lines_per_block
        block_stop += args.lines_per_block
        result = client.put_table(
            f'{PGEN_NAME}.TABLE', content, more=(i != args.nblocks - 1))
        t2 = time.time()
        print(f'time to push table {i}: {t2 - t1}')
        assert result.startswith(b'OK'), f'Error putting table: {result}'
        while client.PGEN.TABLE.QUEUED_LINES.get() > 4 * args.lines_per_block:
            time.sleep(0.1)

    client.close()


def handle_pcap(args):
    client = Client(args.host)
    client.connect()
    client.arm()
    i = 0
    last_time = 0
    PRINT_PERIOD = 3
    for data in client.collect():
        adata = np.frombuffer(data, dtype=np.uint32)
        for j in range(len(adata)):
            expected = (i + j) & 0xffffffff
            assert adata[j] == expected, \
                f'Entry {i + j} = {adata[j]}, expected {expected}'

        i += len(adata)
        current_time = time.time()
        if current_time - last_time > PRINT_PERIOD:
            last_time = current_time
            print(f'Checked a total of {i} lines, last entry {adata[-1]}')

    print(f'Checked {i} lines')
    client.close()


def main():
    args = parse_args()
    procs = []
    procs.append(
        multiprocessing.Process(target=handle_pgen, args=(args,)))
    procs.append(
        multiprocessing.Process(target=handle_pcap, args=(args,)))
    for proc in procs:
        proc.start()
    time.sleep(1)
    client = Client(args.host)
    client.connect()
    client.PGEN.ENABLE.put('ZERO')
    print('Enabling PGEN')
    client.PGEN.ENABLE.put('ONE')
    for proc in procs:
        proc.join()

    while client.PGEN.ACTIVE.get():
        time.sleep(0.5)

    val = client.PGEN.OUT.get()
    expected = args.lines_per_block * args.nblocks - 1
    print(f'PGEN OUT value: {val}')
    assert val == expected, '{val} != {expected}'
    client.close()

if __name__ == '__main__':
    main()
