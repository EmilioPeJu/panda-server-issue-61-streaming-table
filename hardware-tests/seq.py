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
    parser.add_argument('host')
    args = parser.parse_args()

    if args.nblocks < 1:
        raise ValueError('nblocks must be greater than 0')

    if args.repeats != 1 and args.nblocks != 1:
        raise ValueError('repeats and nblocks cannot be used together')

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


def handle_seq(args, allblocks, block_indexes, event):
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
        content, expected = allblocks[block_indexes[i]]
        line += len(content) // 4
        result = client.put_table(
            f'{seq_name}.TABLE', content, streaming=(args.nblocks > 1),
            last=(i == args.nblocks - 1))
        t2 = time.time()
        event.set()
        print(f'seq {i}: took {t2 - t1:.3f}s to push table, '
              f'line {line} , expected first value {expected[0]}')
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
    nvalues = 0
    nblock = 0
    t1 = time.time()
    # We receive a 32-bit word from BITSx for each line in a table
    for data in client.collect(nbytes=args.lines_per_block * 4):
        t2 = time.time()
        checker_q.put((nblock, data))
        t3 = time.time()
        print(
                f'pcap {nblock}: took {t2 - t1:.3f}s + {t3 - t2:.3f}s = '
                f'{t3 - t1:.3f} ({len(data) // 4} lines)')
        nvalues += len(data) // 4
        nblock += 1
        t1 = t2

    print(f'pcap: received {nvalues} values')

    for _ in range(args.checker_threads):
        # Signal the checker processes to stop
        checker_q.put((None, None))

    expected_lines = args.lines_per_block * args.nblocks * args.repeats
    assert expected_lines == nvalues, \
            f'pcap: expected {expected_lines} values, got {nvalues}'
    client.close()


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


def checker(args, allblocks, block_indexes, checker_q, offsets):
    while True:
        nblock, data = checker_q.get()
        if nblock is None:
            break

        adata = np.frombuffer(data, dtype=np.uint32)
        _, expected = allblocks[block_indexes[nblock]]
        print(f'checker {nblock}: Checking block ', end='')
        print(f'line {nblock * args.lines_per_block}, ', end='')
        print(f'expected start {expected[0]}')
        assert len(adata) == len(expected)
        for word, expected_val in zip(adata, expected):
            val = 0
            for bit_i in range(6):
                if (1 << offsets[bit_i]) & word:
                    val |= 1 << bit_i

            assert val == expected_val, \
                f'checker: expects {expected_val}, got {val}'


def generate_content(args):
    result = []
    ticks = math.floor(args.clock_period_us * 1e-6 * args.fpga_freq)
    out_ticks = ticks // 2
    for i in range(64):
        val = i
        content = np.zeros((args.lines_per_block * 4,), dtype=np.uint32)
        expected = []
        rand = val
        for j in range(args.lines_per_block):
            w1 = 0x20001 | (val << 20)
            w2 = 0
            w3 = out_ticks
            w4 = 0
            content[j*4 + 0] = w1
            content[j*4 + 1] = w2
            content[j*4 + 2] = w3
            content[j*4 + 3] = w4
            expected.append(val)
            rand = (rand * 1103515245 + 12345) & 0x7fffffff
            val = rand & 0x3f

        result.append((content, expected))

    return result


def print_stats(args):
    bw = 16 * 1e6 / (args.clock_period_us * 1024**2)
    print(f'Lines per block: {args.lines_per_block}')
    print(f'Number of blocks: {args.nblocks}')
    print(f'Total lines: {args.lines_per_block * args.nblocks * args.repeats}')
    print(f'Clock period: {args.clock_period_us} us')
    print(f'Bandwidth: {bw:.3f} MiB/s')
    print(f'Total size: {args.lines_per_block * args.nblocks * 16 / 1024**2:.3f} MiB')


def main():
    args = parse_args()
    allblocks = generate_content(args)
    block_indexes = np.array(
        [random.randint(0, 63) for _ in range(args.nblocks)], dtype=np.uint8)
    print_stats(args)
    client = PandaClient(args.host)
    client.connect()
    configure_layout(client)
    seq_bits, seq_offsets = get_seq_offsets(client)
    checker_q = multiprocessing.Queue(16)
    produced = multiprocessing.Event()
    procs = []
    procs.append(
        multiprocessing.Process(target=handle_seq, args=(args,
                                                         allblocks,
                                                         block_indexes,
                                                         produced)))
    procs.append(
        multiprocessing.Process(target=handle_pcap, args=(args,
                                                          checker_q,
                                                          seq_bits)))

    for _ in range(args.checker_threads):
        procs.append(
            multiprocessing.Process(target=checker, args=(args,
                                                          allblocks,
                                                          block_indexes,
                                                          checker_q,
                                                          seq_offsets)))

    for proc in procs:
        proc.start()

    # Wait for handle_seq to have a table
    produced.wait()
    time.sleep(0.5)
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
