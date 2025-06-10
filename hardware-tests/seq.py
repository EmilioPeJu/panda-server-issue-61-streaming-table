#!/usr/bin/env python
import argparse
import logging
import math
import multiprocessing
import numpy as np
import random
import time

from panda import PandaClient

log = logging.getLogger(__name__)
layout = \
'''SEQ1.ENABLE=ZERO
SEQ1.REPEATS=1
SEQ1.PRESCALE=0
SEQ1.BITA=CLOCK1.OUT
SEQ1.BITB=ZERO
SEQ1.BITC=ZERO
SEQ1.POSA=ZERO
SEQ1.POSB=ZERO
SEQ1.POSC=ZERO
SEQ1.TABLE<

CLOCK1.ENABLE=SEQ1.ACTIVE
CLOCK1.ENABLE.DELAY=0
CLOCK1.PERIOD.UNITS=s
CLOCK1.WIDTH.UNITS=s
CLOCK1.WIDTH.RAW=1
PCAP.ENABLE=SEQ1.ACTIVE
PCAP.ENABLE.DELAY=1
PCAP.TRIG=CLOCK1.OUT
PCAP.TRIG.DELAY=1
PCAP.TRIG_EDGE=Rising
PCAP.GATE=ONE
PCAP.GATE.DELAY=0
PCAP.SHIFT_SUM=0
PCAP.TS_TRIG.CAPTURE=No
*METADATA.LAYOUT<
{"PCAP": {"x": 323.71144278606965, "y": -25.624997912354722},
"CLOCK1": {"x": -86.64676616915409, "y": 160.87126664498544},
"SEQ": {"x": 80.84577114427856, "y": -223.0074627624815}}

'''


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


def handle_seq(args, q):
    client = PandaClient(args.host)
    client.connect()
    seq_name = client.get_first_instance_name('SEQ')
    seq = client[seq_name]
    clock_name = client.get_first_instance_name('CLOCK')
    # state is clearing the table
    layout_adapted = \
        layout.replace('SEQ1', seq_name).replace('CLOCK1', clock_name)
    print(layout_adapted)
    client.load_state(layout_adapted)
    bw = 4 / (args.clock_period_us * 1e-6) / 1024**2
    seq.REPEATS.put(args.repeats)
    ticks = math.floor(args.clock_period_us * 1e-6 * args.fpga_freq)
    out_ticks = ticks // 2
    client[clock_name].PERIOD.RAW.put(ticks)
    print(f'Lines per block {args.lines_per_block}')
    print(f'Number of blocks {args.nblocks}')
    print(f'Clock period {args.clock_period_us} us')
    print(f'Bandwidth {bw:.3f} MB/s')
    print(f'Total size {args.lines_per_block * args.nblocks * 16 / 1024**2:.3f} MB')
    vals_history = []
    for i in range(args.nblocks):
        t1 = time.time()
        content = np.zeros((args.lines_per_block * 4,), dtype=np.uint32)
        for j in range(args.lines_per_block):
            val = random.randint(0, 63)
            w1 = 0x20001 | (val << 20)
            w2 = 0
            w3 = out_ticks
            w4 = 0
            content[j*4 + 0] = w1
            content[j*4 + 1] = w2
            content[j*4 + 2] = w3
            content[j*4 + 3] = w4
            vals_history.append(val)
            q.put(val)

        print(f'Pushing table {i}')
        result = client.put_table(
            f'{seq_name}.TABLE', content, streaming=(args.nblocks > 1),
            last=(i == args.nblocks - 1))
        t2 = time.time()
        print(f'time to push table {i}: {t2 - t1}')
        assert result.startswith(b'OK'), f'Error putting table: {result}'
        while seq.TABLE.QUEUED_LINES.get() > 2 * args.lines_per_block:
            time.sleep(0.1)

    client.close()


def handle_pcap(args, q):
    client = PandaClient(args.host)
    client.connect()
    client.disable_captures()
    seq_name = client.get_first_instance_name('SEQ')
    seq = client[seq_name]
    bit_a_offset = seq.OUTA.OFFSET.get()
    bit_f_offset = seq.OUTF.OFFSET.get()
    bits_num = bit_a_offset // 32
    assert bits_num == bit_f_offset // 32, \
        "Cant't capture all out bits in one word"
    client.PCAP[f'BITS{bits_num}'].CAPTURE.put('Value')
    client.arm()
    t1 = time.time()
    NOTIFY_PERIOD = 3.0
    checked = 0
    for data in client.collect():
        adata = np.frombuffer(data, dtype=np.uint32)
        for i in range(len(adata)):
            val = (adata[i] >> bit_a_offset) & 0x3f
            expected = q.get()
            checked += 1
            assert expected == val, \
                f'Value mismatch: expected {expected}, got {val}'
            t2 = time.time()
            if t2 - t1 > NOTIFY_PERIOD:
                t1 = t2
                print(f'Checked {checked} values, last one was {val}')

    print(f'Checked {checked} values')
    client.close()


def main():
    args = parse_args()
    q = multiprocessing.Queue()
    procs = []
    procs.append(
        multiprocessing.Process(target=handle_seq, args=(args, q)))
    procs.append(
        multiprocessing.Process(target=handle_pcap, args=(args, q)))
    for proc in procs:
        proc.start()
    time.sleep(1)
    client = PandaClient(args.host)
    client.connect()
    seq_name = client.get_first_instance_name('SEQ')
    seq = client[seq_name]
    seq.ENABLE.put('ZERO')
    print('Enabling SEQ')
    seq.ENABLE.put('ONE')
    for proc in procs:
        proc.join()

    while seq.ACTIVE.get():
        time.sleep(0.5)

    client.close()


if __name__ == '__main__':
    main()
