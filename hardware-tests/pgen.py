#!/usr/bin/env python
import argparse
import logging
import math
import multiprocessing
import numpy as np
import time

from panda import PandaClient

log = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--lines-per-block', type=int, default=16384)
    parser.add_argument('--clock-period-us', type=float, default=0.4)
    parser.add_argument('--start-number', type=int, default=0)
    def nblocks_type(s):
        val = int(s)
        assert val > 0
        return val
    parser.add_argument('--nblocks', type=nblocks_type, default=1)
    parser.add_argument('--fpga-freq', type=int, default=125000000)
    parser.add_argument('host')
    args = parser.parse_args()
    if args.repeats != 1 and args.nblocks != 1:
        raise ValueError('repeats and nblocks cannot be used together')

    return args


def configure_layout(args, client):
    pgen_name = client.get_first_instance_name('PGEN')
    pgen = client[pgen_name]
    clock_name = client.get_first_instance_name('CLOCK')
    clock = client[clock_name]
    pgen.ENABLE.put('ZERO')
    pgen.OUT.UNITS.put('')
    pgen.OUT.OFFSET.put(0)
    pgen.OUT.SCALE.put(1)
    pgen.ENABLE.DELAY.put(0)
    pgen.TRIG.DELAY.put(0)
    pgen.REPEATS.put(1)
    pgen.TRIG.put(f'{clock_name}.OUT')
    client.put_table(f'{pgen_name}.TABLE', np.arange(0))
    clock.ENABLE.put(f'{pgen_name}.ACTIVE')
    clock.ENABLE.DELAY.put(0)
    clock.PERIOD.UNITS.put('s')
    clock.WIDTH.UNITS.put('s')
    clock.PERIOD.put(1)
    clock.WIDTH.put(0)
    client.PCAP.ENABLE.put(f'{pgen_name}.ACTIVE')
    client.PCAP.ENABLE.DELAY.put(10)
    client.PCAP.TRIG.put(f'{clock_name}.OUT')
    client.PCAP.TRIG.DELAY.put(1)
    client.PCAP.TRIG_EDGE.put('Rising')
    client.PCAP.GATE.put('ONE')
    client.PCAP.GATE.DELAY.put(0)
    client.PCAP.SHIFT_SUM.put(0)
    client.PCAP.TS_TRIG.CAPTURE.put('No')
    pgen.OUT.CAPTURE.put('Value')


def handle_pgen(args):
    client = PandaClient(args.host)
    client.connect()
    configure_layout(args, client)
    pgen_name = client.get_first_instance_name('PGEN')
    pgen = client[pgen_name]
    clock_name = client.get_first_instance_name('CLOCK')
    bw = 4 / (args.clock_period_us * 1e-6) / 1024**2
    pgen.REPEATS.put(args.repeats)
    ticks = math.floor(args.clock_period_us * 1e-6 * args.fpga_freq)
    client[clock_name].PERIOD.RAW.put(ticks)
    print(f'Lines per block {args.lines_per_block}')
    print(f'Number of blocks {args.nblocks}')
    print(f'Clock period {args.clock_period_us} us')
    print(f'Bandwidth {bw:.3f} MiB/s')
    print(f'Total size {args.lines_per_block * args.nblocks * 4 / 1024**2:.3f} MiB')
    block_start = args.start_number
    block_stop = args.start_number + args.lines_per_block
    streaming = args.nblocks > 1
    for i in range(args.nblocks):
        t1 = time.time()
        mblock_start = block_start & 0xffffffff
        mblock_stop = block_stop & 0xffffffff
        if mblock_start > mblock_stop:
            content1 = np.arange(mblock_start, 2**32, dtype=np.uint32)
            content2 = np.arange(0, mblock_stop, dtype=np.uint32)
            content = np.concatenate((content1, content2))
        else:
            content = np.arange(mblock_start, mblock_stop, dtype=np.uint32)
        print(f'Pushing table {i} from {block_start} to {block_stop - 1}')
        block_start += args.lines_per_block
        block_stop += args.lines_per_block
        result = client.put_table(
            f'{pgen_name}.TABLE', content, streaming=(args.nblocks > 1),
            last=(i == args.nblocks - 1))
        t2 = time.time()
        print(f'time to push table {i}: {t2 - t1}')
        assert result.startswith(b'OK'), f'Error putting table: {result}'
        while streaming and pgen.TABLE.QUEUED_LINES.get() > 2 * args.lines_per_block:
            time.sleep(0.1)

    client.close()


def handle_pcap(args):
    client = PandaClient(args.host)
    client.connect()
    client.disable_captures()
    pgen_name = client.get_first_instance_name('PGEN')
    pgen = client[pgen_name]
    pgen.OUT.CAPTURE.put('Value')
    client.arm()
    i = args.start_number
    last_time = 0
    PRINT_PERIOD = 3
    for data in client.collect():
        adata = np.frombuffer(data, dtype=np.uint32)
        for j in range(len(adata)):
            if args.repeats > 1:
                expected = (i + j) % args.lines_per_block
            else:
                expected = (i + j) & 0xffffffff
            assert adata[j] == expected, \
                f'Entry {i + j} = {adata[j]}, expected {expected}'

        i += len(adata)
        current_time = time.time()
        if current_time - last_time > PRINT_PERIOD:
            last_time = current_time
            print(f'Checked a total of {i} lines, last entry {adata[-1]}')

    print(f'Checked {i} lines')
    expected = args.lines_per_block * args.nblocks * args.repeats
    assert i == expected, \
        f'Expected {expected} lines, got {i}'
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
    client = PandaClient(args.host)
    client.connect()
    pgen_name = client.get_first_instance_name('PGEN')
    pgen = client[pgen_name]
    pgen.ENABLE.put('ZERO')
    print('Enabling PGEN')
    pgen.ENABLE.put('ONE')
    for proc in procs:
        proc.join()

    while pgen.ACTIVE.get():
        time.sleep(0.5)

    val = pgen.OUT.get()
    print(f'PGEN OUT value: {val}')
    client.close()


if __name__ == '__main__':
    main()
