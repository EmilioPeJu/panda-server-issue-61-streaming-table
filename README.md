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
- [ ] Req 9: the server should expose some field to indicate the progress on
  consumption of the data, current proposal `<block>.TABLE.QUEUED_LINES?`
  indicates how many lines are in the queue plus the ones been currently used
  in the FPGA.
- [ ] Req 10: the server should expose the current mode, e.g.
  `<block>.TABLE.MODE`, it could have one of the following values:
  INIT (it needs initialization), ONE_BUFFER (one buffer mode), STREAMING
  (streaming mode), STREAMING_LAST (the last buffer of the stream was queued).
- [ ] Req 11: DMA overrun and underrun should be detected and shown in the
  HEALTH register, next attempts to push table data should error.

## Design

## Implementation notes

## Testing
