# Pandablocks-server issue 61: Support a streaming table

## Requirements
- [x] Req 1: There should be 2 modes

  ONE BUFFER mode: only one table is sent and it is possible to repeat

  STREAMING mode: more than one table can be pushed.

- [x] Req 2: In streaming mode, while an instance is using a table, we should be
able to push the next table or tables.
- [x] Req 3: The sequencer block should be able to run at 1MHz, this means the DMA
  and the socket to send the table data should be able to sustain at least
  16 MB/s.
- [x] Opt 4: In one buffer mode, small tables (say < 4K entries) can run at 1
entry per tick.
- [x] Req 5: In one buffer mode, we should be able to reuse the last buffer sent
  without requiring to reset and restart the DMA engine.
- [x] Req 6: In the server interface, entering the one buffer mode is done by
  sending a table with `<` character, e.g.

```
  PGEN1.TABLE<
 1 
 2
 3
 ```

- [ ] Req 7: In the server interface, entering the streaming mode is done by
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

- [ ] Req 8: the append mode is fully removed (at least in long tables).
- [x] Req 9: the server should expose some field to indicate the progress on
  consumption of the data, current proposal `<block>.TABLE.QUEUED_LINES?`
  indicates how many lines are in the queue plus the ones been currently used
  in the FPGA.
- [ ] Req 10: the server should expose the current mode, e.g.
  `<block>.TABLE.MODE`, it could have one of the following values:
  INIT (it needs initialization), ONE_BUFFER (one buffer mode), STREAMING
  (streaming mode), STREAMING_LAST (the last buffer of the stream was queued).
- [x] Req 11: DMA overrun and underrun should be detected and shown in the
  HEALTH register, following attempts to push table data should error.

## Design
### FPGA
![](seq-structure.drawio.png)
- The instance in the FPGA will interrupt the CPU on two events: when it just
became ready to accept a new table in streaming mode or when it has used all
the buffers so the driver can free resources.
- `wrapping_mode` and `loop_one_buffer` are signals to implement an optimization
to allow reusing the fifo as a table, this is to do with "Opt 4" point in
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

## Testing
- The cocotb timing tests were extracted from `cocotb` branch in
  PandABlocks-FPGA, this was to speed up the dev-test cycle.
- Development tests were added in the folder `dev-tests`
- Scripts to facilitate hardware testing are in `hardware-tests`
It is important to note that tables are pushed using base64 encoding to reduce
bandwidth required, similarly, pcap is used to validate data using unframed raw
mode.
- Test sending 2048 buffers:
```bash
./hardware-tests/pgen.py --lines-per-block 800000 --clock-period-us 0.4 --nblocks 2048 192.168.0.1
```
The test worked successfully, sending a total of 6.25GB of table data.

- Test sending 5096 4MB buffers:
```bash
./hardware-tests/pgen.py --lines-per-block 1048576 --start-number 0 --clock-period-us 0.4 --nblocks 5096 192.168.0.1
```
The test worked successfully, sending ~20.3GB of table data.

- I detected memory leaks after long use. I investigated further and found that
  if there is an error (e.g. DMA underrun) and I keep sending buffers, those
  buffers were not freed, I found the bug in the driver and fixed it.

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
