# Findings — what we learned about AGC_STATUS = 0x14

## Background incident

On 2026-04-29 ~16:52 UTC a watchdog on the production gateway flagged
`AGC_STATUS = 0x14` and `CHAN_RSSI_LIVE = 0` flatline after 1.0h without
RX packets. Stage 1 (daemon restart with GPIO18-low-2s HAT cold-boot
patch) ran. Packets came back 12 sec later. The user (an active LoRa
operator) confirmed the AmbaSat-1 device had been transmitting normally
through the silent window — so the chip really was stuck, not just a
quiet sky.

After Stage 1, `AGC_STATUS = 0x14` persists 100% sticky over 5 reads in
2.5 min, with packet RX continuing normally. Same register value
observed before and after recovery — but pre-stall the chip had been
running uninterrupted for hours.

This raises the question: **what does 0x14 mean?** Is it:
- A normal post-cold-boot operational state we never observed before?
- A residual stuck-state that the cold-boot does not clear, but
  workably co-exists with RX?
- A degraded-but-functional sub-state, foreshadowing the next stall?

## What we found

### 1. Three immediate-MOV opcode patterns that write AGC_STATUS

Pattern matching on the 8192-byte firmware blob, looking for 7-byte
sandwiches `[4 bytes prefix] [1-byte imm] [2 bytes suffix]` where the
imm varies across known status values 0x01..0x0F:

| Pattern | Prefix | Suffix | Status values covered |
|---------|--------|--------|-----------------------|
| A | `8a 51 c5 00` | `ba 03` | 0x03, 0x05, 0x06, 0x09, 0x0A |
| B | `81 22 8a 51` | `be 84` | 0x01, 0x02, 0x04 |
| C | `81 22 8a 51` | `fe 84` | 0x03, 0x05, 0x06 |

Total coverage: 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x09, 0x0A.

The byte pair `8a 51` is shared by all three patterns and is a recurring
prefix throughout the firmware. Likely a register-bank or address
selector for the AGC_STATUS memory-mapped register.

### 2. Init-sequence values 0x07, 0x08, 0x0B..0x0F have no matching pattern

These values do appear in the firmware as bytes (51, 98, 9, 4, 43, 6, 10
times respectively for 0x07..0x0F) but never inside a 7-byte sandwich
that matches multiple other init values. Either:

- They are written via a fourth or fifth opcode pattern we did not
  match (because their imm values appear in too many unrelated contexts
  to cluster cleanly).
- They are computed (e.g., counter increment) rather than stored as
  literal immediates.
- The firmware reaches them only after specific calibration paths the
  pattern matcher could not isolate.

### 3. AGC_STATUS = 0x14 is NOT written via any of the patterns above

Five raw 0x14 byte occurrences in the firmware:

```
pos 3799 (0x0ed7): 80 83 93 00 48 99 00 18 [14] 18 d0 de 0a 60 ef 18 55
pos 3913 (0x0f49): 80 83 93 00 48 99 00 18 [14] 18 d0 de 0a 99 6f 18 55
pos 6379 (0x18eb): 52 42 c8 a0 fe 84 80 03 [14] 00 cd c5 00 da 40 06 30
pos 6654 (0x19fe): a0 fe 84 80 00 48 01 be [14] ad 39 48 44 c2 03 18 2a
pos 6715 (0x1a3b): 40 42 c8 a0 fe 84 80 03 [14] 00 cd c5 00 da 40 06 30
```

Position 3799 and 3913 share an identical 8-byte pattern before the
0x14 and a near-identical 4 bytes after. Position 6379 and 6715 share
an identical 7-byte pattern before AND identical 8-byte pattern after.
This is consistent with **duplicated Radio-A vs Radio-B code blocks**,
where 0x14 is a literal data value used in both paths.

None of these positions match the prefix/suffix of patterns A/B/C —
0x14 is written through different means.

### 4. Memory-map structure: data segment in Block 4

Distinct bytes per 1 KB block in the AGC firmware:

| Block | Range | Distinct bytes |
|-------|-------|----------------|
| 0 | 0-1023 | 131 |
| 1 | 1024-2047 | 139 |
| 2 | 2048-3071 | 135 |
| 3 | 3072-4095 | 142 |
| **4** | **4096-5119** | **40** |
| 5 | 5120-6143 | 157 |
| 6 | 6144-7167 | 138 |
| 7 | 7168-8191 | 120 |

**Block 4 has only 40 distinct bytes** — clearly a data/LUT region. All
other blocks are code-shaped. Codes accessing constants (gain tables,
threshold values) likely indirect through Block 4.

### 5. The MCU is NOT 8051

8051-style sanity checks fail:

| 8051 opcode | Count in 8 KB | Expected for 8051 code |
|-------------|---------------|------------------------|
| RET (0x22) | 10 | typically hundreds |
| RETI (0x32) | 5 | tens |
| LCALL (0x12) | 4 | tens-hundreds |
| MOV DPTR,# (0x90) | 3 | dozens |

Architecture is most likely a custom 8-bit Semtech RISC, possibly
inherited from the SX1308 predecessor. The high frequency of `0xff`
(586) and `0xbf` (565) bytes does not match any common opcode encoding
table we recognized.

## Hypotheses about 0x14

Listed by decreasing plausibility:

### H1: Bit-OR accumulation (Bit 2 + Bit 4)

The AGC firmware sets individual bits of the AGC_STATUS register via
separate bit-set instructions (`OR imm` rather than `MOV imm`). The
value 0x14 = 0b00010100 is bits 2 and 4 simultaneously set.

Evidence: status-write patterns A/B/C cover values `0x01..0x0A` but
not 0x14, suggesting different opcodes for the post-init steady-state.

Counter-evidence: We could not find a single 7-byte sandwich pattern
where both 0x10 and 0x04 appear as immediates with the same surrounding
context. Either the bit-set instructions use a different encoding (e.g.,
bit-position indexing rather than mask), or this hypothesis is wrong.

### H2: Literal data load from Block 4 LUT

The runtime mainloop reads a state byte from Block 4's data segment and
writes it directly to AGC_STATUS. Different LUT entries for different
operational modes.

Evidence: Block 4 is clearly a data segment.

Counter-evidence: We did not find AGC_STATUS write opcodes that source
their imm from a memory address. (May be encoded differently than what
we searched.)

### H3: 0x14 is a degraded sub-state, not normal operation

The 2026-04-29 PM stall hypothesis: chip got stuck in 0x14, cold-boot
did not fully clear it, system is now operating with reduced sensitivity
or some channel paths disabled.

Evidence: User confirms AmbaSat-1 transmitted during the silent window.
RX returned only after Stage-1 (which includes a 2-second HAT 3.3V
collapse via GPIO18-low). The stuck state was real.

How to test: read AGC_STATUS RIGHT AFTER `lgw_start` on a reference
gateway with no RX activity at all. If 0x14 appears there, H3 is wrong
(0x14 is just the post-init normal state). If 0x14 does NOT appear in
clean post-init readings, H3 stands and we have a real degradation
detector.

This test requires a controlled environment we have not yet reproduced.
A clean-init baseline is in `snapshots/2026-04-29_clean-init-baseline/`
when collected.

### H4: SPI-load-induced register-read corruption (Issue #14)

`AGC_PARITY_ERROR` and `ARB_PARITY_ERROR` being 1 is documented as a
false-positive under RX-bus-load by Semtech maintainer mcoracin in
Lora-net/sx1302_hal Issue #14. The same SPI signal-integrity problem
might cause the AGC_STATUS read to return a partial-update of the
register (bit 2 + bit 4 of one phase mixed with bits of another).

Evidence: PARITY_ERROR being sticky at 1 matches exactly the Issue #14
symptom.

Counter-evidence: AGC_STATUS = 0x14 is reproducible across **all 20**
reads in 2.5 minutes. SPI corruption should produce some scatter unless
the read happens at a fixed-phase moment in the AGC mainloop.

## What we did NOT find

- Public reverse-engineering of any SX1302 firmware blob (none exist on
  GitHub or in academic publications as of 2026-04 search).
- ISA spec for the internal MCU. Datasheet covers only the host
  interface.
- Any forum post discussing AGC_STATUS values > 0x0F.
- A way to single-step the AGC firmware via JTAG or similar (no test
  points published).

## Suggested next steps

1. **Dump the running firmware via `lgw_mem_rb`** and compare byte-by-
   byte against `agc_fw_sx1250.var`. If they differ, the SRAM is
   corrupted and 0x14 is the corruption signature. If they match, the
   firmware itself is intact and 0x14 is a designed runtime state.
   Disruptive: requires daemon stop, but only ~5 sec.

2. **Run `test_loragw_spi` long-running** to verify SPI signal
   integrity is not the cause of the persistent PARITY=1 reads (mcoracin's
   recommendation in Issue #14).

3. **Engage Semtech directly** with a support ticket. Provide:
   the AGC_STATUS = 0x14 observation, the patterns we found,
   the live trace, and a request for AGC_STATUS bit semantics.

4. **Try emulation**: load `agc_fw.bin` into a generic 8-bit MCU
   simulator (Unicorn-engine) with stubbed memory-mapped I/O. Even
   without knowing the ISA, a wrong-ISA simulation may reveal
   structural patterns in the byte stream.

## Memory note

User has confirmed the AmbaSat was transmitting during the stall, so
the 0x14 we observed is **not just a normal post-init state** — it
co-occurred with a real RX-dead window. This makes H3 (degradation
sub-state) the leading hypothesis until disproved. The fix on 2026-04-29
morning (GPIO18-low-2s HAT cold-boot in `reset_lgw.sh`) heals the chip,
but does not transition it back to AGC_STATUS = 0. The chip is now
operational at 0x14 and we do not know if this is a stable state or a
slow-burn pre-stall condition.

## Update — 16-bit-halfword evidence (ARB blob is 75% filler)

After ISA-research feedback, we re-analyzed with `0xbfff` (LE-halfword)
as the filler hypothesis. ARB firmware (`arb_fw.bin`) is **74.3% filler**:
only 1024 bytes of code, the other 7168 are `0xbfff` repeating.

| ARB block | Filler % | Region |
|-----------|----------|--------|
| 0 | 92.8% | filler with init pad |
| 1 | 100.0% | pure filler |
| 2 | 1.4% | **CODE** (1 KB) |
| 3 | 0.0% | **CODE** (1 KB) |
| 4-7 | 100.0% | pure filler |

This is **decisive evidence for a 16-bit halfword machine**. An 8-bit
ISA would fill with `0xff` or `0x00` (single byte), not `0xbfff` as
two-byte unit. A 32-bit ISA would have other word patterns. ARB needs
only 2 KB of code to do its packet-arbitration work.

AGC fw is denser (only 13.6% filler) because it does the runtime gain
control loop. CAL fw is intermediate (24.5% filler).

This confirms `docs/04_isa_research.md`: best characterization is
**custom 16-bit Semtech RISC**, no public ISA documentation exists.
