# Pandablocks-server issue 61: Support a streaming table

DONE! This has been completed in the following PRs:
- [PandABlocks-FPGA/pull/229](https://github.com/PandABlocks/PandABlocks-FPGA/pull/229)
- [PandABlocks-server/pull/63](https://github.com/PandABlocks/PandABlocks-server/pull/63)

## Requirements
- [x] Req 1: There should be 2 modes

  FIXED mode: only one table is sent and it is possible to repeat

  STREAMING mode: more than one table can be pushed.

- [x] Req 2: In streaming mode, while an instance is using a table, we should be
able to push the next table or tables.
- [x] Req 3: The sequencer block should be able to run at 1MHz, this means the DMA
  and the socket to send the table data should be able to sustain at least
  16 MB/s (approx 15.26MiB/s).
- [x] Opt 4: In fixed mode, small tables (say < 4K entries) can run at 1
entry per tick.
- [x] Req 5: In fixed mode, we should be able to reuse the last buffer sent
  without requiring to reset and restart the DMA engine.
- [x] Req 6: In the server interface, entering the fixed mode is done by
  sending a table with `<` character, e.g.

```
  PGEN1.TABLE<
 1 
 2
 3
 ```

- [x] Req 7: In the server interface, entering the streaming mode is done by
sending a table with `<<` characters and sending the last table with `<<|`
characters, e.g.
```
PGEN1.TABLE<<
1
2

PGEN1.TABLE<<|
3
4

```

- [x] Req 8: the append mode is fully removed (at least in long tables).
- [x] Req 9: the server should expose some field to indicate the progress on
  consumption of the data, current proposal `<block>.TABLE.QUEUED_LINES?`
  indicates how many lines are in the queue plus the ones been currently used
  in the FPGA.
- [x] Req 10: the server should expose the current mode, e.g.
  `<block>.TABLE.MODE`, it could have one of the following values:
  INIT (it needs initialization), FIXED (fixed mode), STREAMING
  (streaming mode), STREAMING_LAST (the last buffer of the stream was queued).
- [x] Req 11: DMA overrun and underrun should be detected and shown in the
  HEALTH register, following attempts to push table data should error.
- [x] Req 12: Reading a streaming table from the server should return an empty
  table.
- [x] Req 13: In streaming mode, a explicit table reset is required to change
  to a different mode. This is to ensure a script pushing the tables is aware of
  the end condition.
- [x] Req 14: Transition from fixed mode to fixed mode (with different data) or
  to streaming mode is allowed without requiring an explicit reset (the server
  will be doing the table reset automatically under the hood).
- [x] Req 15:  `<block>.TABLE>QUEUED_LINES` must be included in the change set
  (to get notified by `*CHANGES?`).

## Design
### FPGA
![](seq-structure.drawio.png)
- The instance in the FPGA will interrupt the CPU on two events: when it just
became ready to accept a new table in streaming mode or when it has used all
the buffers so the driver can free resources.
- `wrapping_mode` and `loop_one_buffer` are signals to implement an optimization
to allow reusing the fifo as a table, this is to do with point "Opt 4" in
requirements.

### Server
- The driver now has to handle a new interrupt and do the required processing,
on ready condition, it should check the queue to push the next table, on
completion condition, it should free all buffers.
- Pushing the data to the driver is done via a new ioctl command, this is
  because the write syscall doesn't allow passing flags which we need to
  indicate if it is the last buffer.

## Implementation notes
- MA suggested that the part handling the DMA shouldn't know the REPEATS, even
  if that means spending more block ram.
- MA suggested to preallocate DMA buffers instead of doing it on demand, even if
  that means having a smaller maximum number of buffers allocated.
- How many buffers? and what's the best size? I used the maximum possible size
  using the page allocator 4MB, unfortunately, some targets like ZedBoard don't
  have too many of those buffers available, so I set the number of buffers to 8
  per instance, which would allow having 2 seconds worth of data at maximum
  speed (see Req 3).
- Once a streaming run finishes, in order to restart the instance, you need to
  clear the table, the reason why this is not done automatically (or on disable)
  is that the script doing the table pushing should be aware of the end condition
  (either by error or gracefully).
- Sequence of events while sending a table:
  - User sends a table to the server with `<`, `<<` or `<<|` depending on the
    desired mode.
  - Server pushes it to the driver.
  - If hardware block is not busy, driver pushes it to hardware, otherwise, it
    is queued.
  - If there is a ready interrupt, the driver will push the buffer from the
    queue.
  - If there is a completion interrupt, the driver will free the buffers left.
- After implementing all the requirements I found I got timing failed while
  building PandABox-no_fmc, at first I found the critical paths were in the
  blocks I was working with, but after doing some optimizations, the critical
  paths were not in those blocks anymore and timing was still failing,
  mainly because of congestion (overused of the FPGA)... so I decided to reduce
  the number of instances for the following blocks: SEQ=2, PGEN=1, SYNC=1 ...
  and after that, timing was finally (barely) passing. However, CI showed some
  other applications failing and I had to fully remove PGEN.
- TC suggested to put back SYNC=2 and it seems to be working fine.

## Testing
- The cocotb timing tests were extracted from `cocotb` branch in
  PandABlocks-FPGA, this was to speed up the dev-test cycle.
- Development tests were added in the folder `dev-tests`
- Scripts to facilitate hardware testing are in `hardware-tests`
It is important to note that tables are pushed using base64 encoding to reduce
bandwidth required, similarly, pcap is used to validate data using unframed raw
mode.

- ZedBoard: PGEN test sending 5096x4MB buffers at 2.5MHz per line:
```bash
./hardware-tests/pgen.py --lines-per-block 1048576 --start-number 0 --clock-period-us 0.4 --nblocks 5096 192.168.0.1
Lines per block 1048576
Number of blocks 5096
Clock period 0.4 us
Bandwidth 9.537 MB/s
Total size 20384.000 MB
Pushing table 0 from 0 to 1048575
time to push table 0: 0.11306047439575195
Pushing table 1 from 1048576 to 2097151
time to push table 1: 0.2505371570587158
...
Checked 5343543296 lines
```
The test worked successfully, sending ~20.3GB of table data.

- I detected memory leaks after long use. I investigated further and found that
  if there is an error (e.g. DMA underrun) and I keep sending buffers, those
  buffers were not freed, I found the bug in the driver and fixed it.

- I found a mistake, I was setting the clock period in seconds, but the server
  thinks the FPGA is clocked at 125MHz, which is wrong as I under-clocked to
  50MHz for my tests. This means the test above was triggering 40% slower than 
  I thought.

  I changed the script to accept a fpga-freq parameter so that I can set the
  clock period using ticks instead. I did a quick test to find the fastest
  triggering, and it's 0.25us, I expect when I repeat the test with pandabox, it
  will be faster, because it will have a faster NIC and also because it is
  not under-clocked.

- I got timing errors in my last build for Pandabox after implementing all the
  requirements. I will analyze the reports in Vivado to optimize timing.

- ZedBoard: SEQ test sending 2048x4MB buffers at 1.1MHz and using 2 threads to
  generate table data.
```bash
 ./hardware-tests/seq.py --lines-per-block 262144 --clock-period-us 0.9 --threads 2 --nblocks 2048 192.168.0.1 --fpga-freq 50000000
Lines per block: 262144
Number of blocks: 2048
Total lines: 536870912
Clock period: 0.9 us
Bandwidth: 16.954 MiB/s
Total size: 8192.000 MiB
Seq out bits offsets [10, 11, 12, 13, 14, 15] from BITS0
seq: time to push table 0: 0.466
seq: time to push table 1: 0.257
seq: time to push table 2: 0.241
seq: time to push table 3: 0.249
...
Checked 536870912 values
```
- After moving rootfs to use yocto, I repeated the test and can now go faster,
  not sure why. Here is an example run:

```bash
 ./hardware-tests/seq.py --lines-per-block 262144 --clock-period-us 0.7 --threads 2 --nblocks 256 192.168.0.1 --fpga-freq 50000000
Lines per block: 262144
Number of blocks: 256
Total lines: 67108864
Clock period: 0.7 us
Bandwidth: 21.798 MiB/s
Total size: 1024.000 MiB
Seq out bits offsets [10, 11, 12, 13, 14, 15] from BITS0
seq: pushing table 0
seq: time to push table 0: 0.470
seq: pushing table 1
seq: time to push table 1: 0.249
seq: pushing table 2
seq: time to push table 2: 0.236
seq: pushing table 3
seq: time to push table 3: 0.247
seq: pushing table 4
seq: time to push table 4: 0.229
seq: pushing table 5
Enabling SEQ
seq: time to push table 5: 0.237
seq: pushing table 6
seq: time to push table 6: 0.174
...
Checked 67108864 values
```
- First test in a pandabox (ts-mo-panda-01) results in DMA underruns, for some
  reason the network bandwidth was limited to around 12.5MB/s which later was
  found to be because of having an un-managed switch with some internal
  fast-ethernet links upstream.

- Tests were run in a different pandablox (ts-mo-panda-03), and again the first
  tests resulted in DMA underruns, this time, the reason is that the CPU (of the
  workstation) is not powerful enough to create the table data, push it and
  check the pcap data in time. I decided to parallelise the table data creation
  (producer threads) and the pcap data checking (checker threads), I set the
  number of threads to use all the cores and the tests improved but still were
  not successful.

- I decided to reduce moving of data around threads, and pre-generate a set of
  64 pseudo-random blocks, so instead of random generating the full block we
  select a random block of the 64 choices each time a new table is needed.
  This allowed to reduce resources used and allocating more CPU to the checker
  threads. Finally, a test at 1MHz trigger period worked:
```bash
./hardware-tests/seq.py --lines-per-block 262144 --clock-period-us 1  --nblocks 1024 --checker-threads 8 --fpga-freq 125000000 172.23.252.203
Lines per block: 262144
Number of blocks: 1024
Total lines: 268435456
Clock period: 1.0 us
Bandwidth: 15.259 MiB/s
Total size: 4096.000 MiB
...
seq 1023: Took 0.174s to pushed table, line 268435456, expected first value 52
...
pcap 1023: block with 262144 lines
pcap: received 268435456 values
...
checker 1023: Checking block line 268173312, expected start 52
```
This meets the requirements, it still is possible to trigger it faster, with a
0.8us trigger period, the SEQ block still works, but PCAP falls behind until a
buffer overrun occurs and can't check the following blocks.
- One long test:
```bash
./hardware-tests/seq.py --lines-per-block 262144 --clock-period-us 0.9  --nblocks 65536 --checker-threads 8 --fpga-freq 125000000 172.23.252.203
Lines per block: 262144
Number of blocks: 65536
Total lines: 17179869184
Clock period: 0.9 us
Bandwidth: 16.954 MiB/s
Total size: 262144.000 MiB
Seq out bits offsets [7, 9, 11, 13, 15, 17] from BITS2
seq 0: took 0.191s to push table, line 262144 , expected first value 29
...
seq 65535: took 0.157s to push table, line 17179869184 , expected first value 38
```
The sequencer works fully till the end but pcap and checker thread are not able
to catch up and stops checking after 662 blocks.
- The checker code was improved (thanks MA for the suggestions) and I was able
  to reduce checker threads to 2, here is a run in PandABox at 0.75us trigger
  period:

```bash
$ ./hardware-tests/seq.py --lines-per-block 262144 --clock-period-us 0.75 --checker-threads 2 --nblocks 256 ts-mo-panda-03 --fpga-freq 125000000
Lines per block: 262144
Number of blocks: 256
Total lines: 67108864
Clock period: 0.75 us
Bandwidth: 20.345 MiB/s
Total size: 1024.000 MiB
Seq out bits offsets [7, 9, 11, 13, 15, 17] from BITS2
seq 0: took 0.261s to push table, line 0 , expected first value 48
seq 1: took 0.254s to push table, line 262144 , expected first value 59
seq 2: took 0.240s to push table, line 524288 , expected first value 7
Enabling SEQ
seq 3: took 0.192s to push table, line 786432 , expected first value 26
pcap 0: took 0.950s + 0.000s = 0.950 (262144 lines)
checker 0: Checking block line 0, expected start 48
...
pcap 255: took 0.126s + 0.000s = 0.126 (262144 lines)
pcap: received 67108864 values
checker 255: Checking block line 66846720, expected start 22
```

## Performance analysis
### Perf report
- `perf` was built and manually copied to the target, to do this, I added the
  following target to rootfs:
```
perf:
	$(EXPORTS) KBUILD_OUTPUT=$(KERNEL_BUILD) $(MAKE) -j 12 -C $(KERNEL_SRC)/tools/perf
```
Then manually copied the result from the built directory.
- Some compiler options were added, the following is an excerpt from the server
  mafile:

```
ifdef DEBUG
CFLAGS += -O0 -g -fomit-frame-pointer
endif
```
Then I built using: `make DEBUG=1`
- Perf was run while I was doing the streaming tests:
```bash
perf record -F 999 -a -g --call-graph dwarf -- sleep 10
perf script > /tmp/out.perf
```
- The flamegraph was generated with the following commands:
```bash
scp panda:/tmp/out.perf .
perl src/FlameGraph/stackcollapse-perf.pl out.perf > out.folded
perl src/FlameGraph/flamegraph.pl out.folded > perf-flamegraph.svg
```
- Flamegraph 1: while pushing 10 buffers
![](perf-flamegraph-1.svg)
It turned out, the bottleneck was ethernet, for some reason, I can only push
around 12MB/s to the ZedBoard over the ethernet link, I will re-try the same
test on a Pandabox (which shouldn't have that limitation).

### ILA report
- A system integrated logic analyser was added to verify the AXI transactions.
- Observations:
From end of last burst to start of next burst it takes 22 cycles.
Given that there is an arbiter, this number depends on the number of dma
instances, in this specific case, there were 2 of them.

If we consider maximum bursts, this provides an utilization of 92%, at 125MHz,
the maximum bandwidth would be around 460MB/s.
Considering that in practice, we can push the ethernet link to around 60MB/s,
this test confirms that the AXI will not be the bottleneck.

## Tricks
### Poor man's network bandwidth monitoring
For receive traffic(for the process with pid=$PID:
```bash
while true; do
  b1=$(tail -n1 /proc/$PID/net/dev | tr -s ' ' | cut -d' ' -f 3)
  sleep 0.5
  b2=$(tail -n1 /proc/$PID/net/dev | tr -s ' ' | cut -d' ' -f 3)
  echo $(( ( $b2 - $b1 ) / 1024 * 2 ))KiB
done
```
