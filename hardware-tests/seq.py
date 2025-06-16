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


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--lines-per-block', type=int, default=16384)
    parser.add_argument('--clock-period-us', type=float, default=0.4)
    parser.add_argument('--nblocks', type=int, default=1)
    parser.add_argument('--fpga-freq', type=int, default=125000000)
    parser.add_argument(
        '--threads', type=int, default=1,
        help='Number of threads to use for creating the buffers data')
    parser.add_argument('host')
    args = parser.parse_args()

    if args.nblocks < 1:
        raise ValueError('nblocks must be greater than 0')

    if args.repeats != 1 and args.nblocks != 1:
        raise ValueError('repeats and nblocks cannot be used together')

    if args.nblocks % args.threads != 0:
        raise ValueError('nblocks must be divisible by threads')

    return args


def configure_layout(args, client):
    seq_name = client.get_first_instance_name('SEQ')
    seq = client[seq_name]
    clock_name = client.get_first_instance_name('CLOCK')
    clock = client[clock_name]
    seq.ENABLE.put('ZERO')
    seq.REPEATS.put(1)
    seq.PRESCALE.put(0)
    seq.BITA.put(f'{clock_name}.OUT')
    seq.BITB.put('ZERO')
    seq.BITC.put('ZERO')
    seq.POSA.put('ZERO')
    seq.POSB.put('ZERO')
    seq.POSC.put('ZERO')
    client.put_table(f'{seq_name}.TABLE', np.arange(0))
    clock.ENABLE.put(f'{seq_name}.ACTIVE')
    clock.ENABLE.DELAY.put(0)
    clock.PERIOD.UNITS.put('s')
    clock.WIDTH.UNITS.put('s')
    clock.WIDTH.RAW=1
    client.PCAP.ENABLE.put(f'{seq_name}.ACTIVE')
    client.PCAP.ENABLE.DELAY.put(1)
    client.PCAP.TRIG.put(f'{clock_name}.OUT')
    client.PCAP.TRIG.DELAY.put(1)
    client.PCAP.TRIG_EDGE.put('Rising')
    client.PCAP.GATE.put('ONE')
    client.PCAP.GATE.DELAY.put(0)
    client.PCAP.SHIFT_SUM.put(0)
    client.PCAP.TS_TRIG.CAPTURE.put('No')


def handle_seq(args, q, event):
    client = PandaClient(args.host)
    client.connect()
    seq_name = client.get_first_instance_name('SEQ')
    seq = client[seq_name]
    clock_name = client.get_first_instance_name('CLOCK')
    seq.REPEATS.put(args.repeats)
    ticks = math.floor(args.clock_period_us * 1e-6 * args.fpga_freq)
    client[clock_name].PERIOD.RAW.put(ticks)
    streaming = args.nblocks > 1
    for i in range(args.nblocks):
        t1 = time.time()
        content = q.get()
        print(f'seq: pushing table {i}')
        result = client.put_table(
            f'{seq_name}.TABLE', content, streaming=(args.nblocks > 1),
            last=(i == args.nblocks - 1))
        t2 = time.time()
        event.set()
        print(f'seq: time to push table {i}: {t2 - t1:.3f}')
        assert result.startswith(b'OK'), f'seq: error putting table: {result}'
        while streaming and seq.TABLE.QUEUED_LINES.get() > 3 * args.lines_per_block:
            time.sleep(0.1)

    client.close()


def handle_pcap(args, q):
    client = PandaClient(args.host)
    client.connect()
    client.disable_captures()
    seq_name = client.get_first_instance_name('SEQ')
    seq = client[seq_name]
    offsets = [
        seq.OUTA.OFFSET.get(),
        seq.OUTB.OFFSET.get(),
        seq.OUTC.OFFSET.get(),
        seq.OUTD.OFFSET.get(),
        seq.OUTE.OFFSET.get(),
        seq.OUTF.OFFSET.get()
    ]
    word_num = int(seq.OUTA.CAPTURE_WORD.get()[-1])
    for out in ('OUTB', 'OUTC', 'OUTD', 'OUTE', 'OUTF'):
        assert int(seq[out].CAPTURE_WORD.get()[-1]) == word_num, \
            "pcap: cant't capture all out bits in one word"

    print(f'Seq out bits offsets {offsets} from BITS{word_num}')
    client.PCAP[f'BITS{word_num}'].CAPTURE.put('Value')
    client.arm()
    t1 = time.time()
    NOTIFY_PERIOD = 3.0
    checked = 0
    acc = []
    expected = q.get()
    nblock = 1
    for data in client.collect():
        adata = np.frombuffer(data, dtype=np.uint32)
        for i in range(len(adata)):
            word = adata[i]
            val = 0
            for bit_i in range(6):
                if (1 << offsets[bit_i]) & word:
                    val |= 1 << bit_i

            acc.append(val)
            if len(acc) >= len(expected):
                if expected != acc:
                    for j in range(len(expected)):
                        assert expected[j] == acc[j], \
                            f'pcap: entry {checked + j} expects {expected[j]}, got {acc[j]}'

                checked += len(expected)
                acc = []
                if nblock < args.nblocks:
                    expected = q.get()
                    nblock += 1

                t2 = time.time()
                if t2 - t1 > NOTIFY_PERIOD:
                    t1 = t2
                    print(f'pcap: checked {checked} values, last one was {val}')

    print(f'Checked {checked} values')
    expected_lines = args.lines_per_block * args.nblocks * args.repeats
    assert expected_lines == checked, \
            "pcap: expected {expected_lines} values, got {checked}"
    client.close()


def data_producer(args, buffer_q, expect_q, lock):
    n = args.nblocks // args.threads
    ticks = math.floor(args.clock_period_us * 1e-6 * args.fpga_freq)
    out_ticks = ticks // 2
    for _ in range(n):
        content = np.zeros((args.lines_per_block * 4,), dtype=np.uint32)
        expected = []
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
            expected.append(val)

        lock.acquire()
        buffer_q.put(content)
        expect_q.put(expected)
        lock.release()


def main():
    args = parse_args()
    bw = 16 * 1e6 / (args.clock_period_us * 1024**2)
    print(f'Lines per block: {args.lines_per_block}')
    print(f'Number of blocks: {args.nblocks}')
    print(f'Total lines: {args.lines_per_block * args.nblocks * args.repeats}')
    print(f'Clock period: {args.clock_period_us} us')
    print(f'Bandwidth: {bw:.3f} MiB/s')
    print(f'Total size: {args.lines_per_block * args.nblocks * 16 / 1024**2:.3f} MiB')
    client = PandaClient(args.host)
    client.connect()
    configure_layout(args, client)
    expect_q = multiprocessing.Queue(128)
    buffer_q = multiprocessing.Queue(128)
    produced = multiprocessing.Event()
    lock = multiprocessing.Lock()
    procs = []
    procs.append(
        multiprocessing.Process(target=handle_seq, args=(args, buffer_q,
                                                         produced)))
    procs.append(
        multiprocessing.Process(target=handle_pcap, args=(args, expect_q)))
    for _ in range(args.threads):
        procs.append(
            multiprocessing.Process(target=data_producer, args=(args,
                                                                buffer_q,
                                                                expect_q,
                                                                lock)))
    for proc in procs:
        proc.start()

    # Wait for handle_seq to have a table
    produced.wait()
    time.sleep(1)
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
