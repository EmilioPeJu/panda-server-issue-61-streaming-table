#!/usr/bin/env python
import argparse
import logging
import math
import multiprocessing
import numpy as np
import os
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
    parser.add_argument('--max-blocks-queued', type=int, default=7)
    parser.add_argument('--checker-threads', type=int, default=1)
    parser.add_argument(
        '--producer-threads', type=int, default=1,
        help='Number of threads to use for creating the buffers data')
    parser.add_argument('host')
    args = parser.parse_args()

    if args.nblocks < 1:
        raise ValueError('nblocks must be greater than 0')

    if args.repeats != 1 and args.nblocks != 1:
        raise ValueError('repeats and nblocks cannot be used together')

    if args.nblocks % args.producer_threads != 0:
        raise ValueError(
            'nblocks must be divisible by number of producer threads')

    return args


def configure_layout(client):
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
    client.PCAP.TRIG.DELAY.put(2)
    client.PCAP.TRIG_EDGE.put('Rising')
    client.PCAP.GATE.put('ONE')
    client.PCAP.GATE.DELAY.put(0)
    client.PCAP.SHIFT_SUM.put(0)
    client.PCAP.TS_TRIG.CAPTURE.put('No')


def handle_seq(args, buffer_q, expect_q, event):
    client = PandaClient(args.host)
    client.connect()
    seq_name = client.get_first_instance_name('SEQ')
    seq = client[seq_name]
    clock_name = client.get_first_instance_name('CLOCK')
    seq.REPEATS.put(args.repeats)
    ticks = math.floor(args.clock_period_us * 1e-6 * args.fpga_freq)
    client[clock_name].PERIOD.RAW.put(ticks)
    streaming = args.nblocks > 1
    line = 0
    for i in range(args.nblocks):
        t1 = time.time()
        content, expected = buffer_q.get()
        expect_q.put(expected)
        t2 = time.time()
        print(f'seq: pushing table {i} line {line} expected start {expected[0]}')
        line += len(content) // 4
        result = client.put_table(
            f'{seq_name}.TABLE', content, streaming=(args.nblocks > 1),
            last=(i == args.nblocks - 1))
        t3 = time.time()
        event.set()
        print(f'seq {i}: time waiting {t2 - t1:.3f} ', end='')
        print(f'time sending {t3 - t2:.3f}')
        assert result.startswith(b'OK'), f'seq: error putting table: {result}'
        while streaming and (seq.TABLE.QUEUED_LINES.get() >=
                             args.max_blocks_queued * args.lines_per_block):
            time.sleep(0.1)

    client.close()


def handle_pcap(args, checker_q, bits_word_num):
    client = PandaClient(args.host)
    client.connect()
    client.disable_captures()
    client.PCAP[f'BITS{bits_word_num}'].CAPTURE.put('Value')
    client.arm()
    checked = 0
    nblock = 0
    # We receive a 32-bit word from BITSx for each line in a table
    for data in client.collect(nbytes=args.lines_per_block * 4):
        print(f'pcap {nblock}: block with {len(data) // 4} lines')
        checker_q.put((nblock, data))
        checked += len(data) // 4
        nblock += 1

    print(f'Checked {checked} values')

    for _ in range(args.checker_threads):
        # Signal the checker processes to stop
        checker_q.put((None, None))

    expected_lines = args.lines_per_block * args.nblocks * args.repeats
    assert expected_lines == checked, \
            f'pcap: expected {expected_lines} values, got {checked}'
    client.close()


def data_producer(args, buffer_q):
    n = args.nblocks // args.producer_threads
    ticks = math.floor(args.clock_period_us * 1e-6 * args.fpga_freq)
    out_ticks = ticks // 2
    for i in range(n):
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

        print(f'producer {os.getpid()}: local block {i} ', end='')
        print(f'with start value {expected[0]} was added')
        buffer_q.put((content, expected))


def get_seq_offsets(client):
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
    return word_num, offsets


def checker(args, checker_q, expect_q, offsets, lock):
    while True:
        lock.acquire()
        nblock, data = checker_q.get()
        if nblock is None:
            lock.release()
            break

        expected = expect_q.get()
        lock.release()
        adata = np.frombuffer(data, dtype=np.uint32)
        print(f'checker {nblock}: Checking block ', end='')
        print(f'line {nblock * args.lines_per_block} starting with {expected[0]}')
        assert len(adata) == len(expected)
        for word, expected_val in zip(adata, expected):
            val = 0
            for bit_i in range(6):
                if (1 << offsets[bit_i]) & word:
                    val |= 1 << bit_i

            assert val == expected_val, \
                f'checker: expects {expected_val}, got {val}'


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
    configure_layout(client)
    seq_bits, seq_offsets = get_seq_offsets(client)
    expect_q = multiprocessing.Queue(16)
    buffer_q = multiprocessing.Queue(16)
    checker_q = multiprocessing.Queue(16)
    produced = multiprocessing.Event()
    clock = multiprocessing.Lock()
    procs = []
    procs.append(
        multiprocessing.Process(target=handle_seq, args=(args,
                                                         buffer_q,
                                                         expect_q,
                                                         produced)))
    procs.append(
        multiprocessing.Process(target=handle_pcap, args=(args,
                                                          checker_q,
                                                          seq_bits)))

    for _ in range(args.checker_threads):
        procs.append(
            multiprocessing.Process(target=checker, args=(args, checker_q,
                                                          expect_q,
                                                          seq_offsets,
                                                          clock)))

    for _ in range(args.producer_threads):
        procs.append(
            multiprocessing.Process(target=data_producer, args=(args,
                                                                buffer_q)))
    for proc in procs:
        proc.start()

    # Wait for handle_seq to have a table
    produced.wait()
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
