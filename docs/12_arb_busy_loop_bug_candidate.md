# The bug candidate: ARB MCU's untimed wait-for-HW-bit busy-loop

**Status: strong candidate identified through PIC16 disassembly. Not yet
falsified. The proposed mechanism explains every symptom of the stuck
state observed on 2026-04-29 between 15:51 and 16:54 UTC.**

## TL;DR

The ARB firmware contains an unbounded busy-loop waiting for hardware
status flag `file[0x0e] bit 0` to clear. If a hardware fault leaves that
bit set indefinitely, ARB spins forever and no LoRa packets are ever
forwarded to the host, even though AGC continues to run normally and the
daemon's TTN heartbeat keeps working. This matches the live-symptom
profile of the production stall exactly.

## Provenance — `[VERIFIED]`

The two-wave research, dual-disassembler verification (S1), and HAL
cross-reference (S2) gave us:

- ISA = PIC16 mid-range, gpdasm + custom Python disassembler 100% agree.
- ARB MCU SFR base on host SPI = `0x6080`. ARB_STATUS = `0x6081`.
- ARB MCU file register `0x1b` = ARB_STATUS, written with 0x01 at
  init done, with 0x00 (`clrf 0x1b`) at re-init points.
- ARB main loop entry = word `0x07ff` → `goto 0x045d`.

After the per-channel setup at `0x045d-0x04d3`, ARB enters this loop:

```
04d4: movwf 0x1b           ; ARB_STATUS = 0x01 (signal host: init done)
04d5: btfsc 0x0e, 0        ; if HW_BUSY (bit 0 set), do NOT skip
04d6: goto  0x04d5         ;   GOTO loop top  -- INFINITE if bit stays set
04d7: movlw 0x25           ; HW ready: continue with channel processing
...
04eb: call  0x04f1         ; process current channel
04ec: bcf   0x0a, 3
04ed: btfsc 0x0e, 0        ; HW_BUSY again?
04ee: goto  0x04d5         ;   restart from top of channel loop
04ef: incf  0x45, 1        ; next channel
04f0: goto  0x04e5         ; loop body
```

There is **no timeout counter**, **no escape**, **no fall-through path**
out of `0x04d5` other than `file[0x0e] bit 0` clearing.

## Why we believe `0x0e` is hardware-only — `[VERIFIED]`

A complete grep across both AGC and ARB firmware blobs (5,150 real
instructions total, excluding the `0xbfff` filler):

| Operation type | AGC fw on 0x0e | ARB fw on 0x0e |
|----------------|------------------|------------------|
| MOVWF | 0 | 0 |
| CLRF  | 0 | 0 |
| INCF/DECF/COMF | 0 | 0 |
| IORWF/ANDWF/XORWF/ADDWF/SUBWF | 0 | 0 |
| RLF/RRF/SWAPF | 0 | 0 |
| BSF/BCF | 0 | 0 |
| BTFSC/BTFSS (read) | 0 | 3 |
| MOVF/SUBLW with 0x0e | 0 | 0 |

Three reads in ARB:

```
04d5: btfsc 0x0e, 0       ; main-loop wait
04ed: btfsc 0x0e, 0       ; post-channel wait
04fb: btfss 0x0e, 1       ; another HW flag, bit 1
```

Zero writes. **The MCU never modifies file 0x0e**. It can only be
written by hardware: Cycleo mapped this file address to a HW status
register in the PIC16-compatible-core SFR space, like a memory-mapped
peripheral status.

For comparison, file `0x1b` (ARB_STATUS, host-readable) gets `clrf` and
`movwf` operations from the fw. File `0x0e` is fundamentally different:
read-only, hardware-driven.

## Hardware-flag interpretation — `[INFERRED]`

The Cycleo SX1302 architecture has an ARB MCU sitting between the AGC
MCU and the channel-decoder hardware. The ARB's job is to dispatch
incoming-packet decoding across the eight LoRa channels. A "HW busy"
flag is exactly what you'd expect to see in such a design: the
channelizer raises a busy line to tell ARB "I'm currently demodulating,
don't poke me", and ARB waits for it to drop before scheduling the
next channel.

This matches:
- The HAL has `sx1302_arb_set_debug_stats()` and 16 `ARB_DEBUG_STS_*`
  registers that report channel-by-channel demodulation activity.
- TTN forum thread #46001 describes RX-stall with healthy SPI link
  (= AGC visible, ARB stuck = exactly our symptom).

We do **NOT** yet have a Semtech-confirmed mapping of file `0x0e` to a
specific channelizer hardware register. This is `[INFERRED]` from the
behavior pattern, not from a datasheet citation.

## Why this matches the stall symptom — `[HYPOTHESIS]`

The 2026-04-29 15:51-16:54 UTC stall:

| Observed symptom | Untimed-busy-loop explanation |
|------------------|-------------------------------|
| 63 min RX-dead despite AmbaSat actively transmitting | ARB stuck → no channel processing → no packets to RX-buffer |
| AGC_STATUS = 0x14 sticky during stall | AGC main loop unaffected, continues normal flow |
| TTN heartbeat (PUSH_DATA/PULL_DATA) still ACK'd 100% | Daemon-side network thread independent of ARB |
| No HAL ERROR log entries | ARB doesn't print, AGC has nothing wrong to print |
| Cold-boot (GPIO18-low-2s) heals the chip | HAT-3.3V drop resets channelizer hardware → bit clears |
| Daemon restart alone does NOT heal | fw resumes, channelizer HW state preserved, bit still set |
| Stage 1.2 (in-place fw reload) test on 2026-04-29 18:02 worked | Reload re-initialized ARB MCU but the bug was no longer present (we couldn't catch it stuck) |

Every observation is consistent with this hypothesis. The hypothesis
also generates falsifiable predictions:

1. At the next real stall, the ARB program counter (PC) will be at
   `0x04d5` or `0x04ed`. Reading PC requires halting ARB MCU and
   reading file `0x02` = PCL plus PCLATH.
2. The ARB_DEBUG_STS_* registers via util_dump_status will be frozen
   (no advancing counters) during the stall.
3. The 16 hardware ARB_DEBUG_STS_* registers will indicate one of the
   eight channel demodulators in a "busy" state with no output.

The fixed forensics script (`hibbes/lora-dashboard` `1cbe7e9`)
captures all of this. Next stall will give us the data.

## Possible triggers for the HW bit getting stuck — `[HYPOTHESIS]`

What kind of fault would leave `file[0x0e] bit 0` set indefinitely?

- **LNA saturation from over-strong AmbaSat-1 signal at -21 to -36 dBm**
  (see `docs/09_ambasat_lna_saturation.md`). The channelizer demodulator
  may enter a state it cannot self-exit when the AGC is forced into a
  corner case it wasn't tested for at high RSSI.
- **PHY-layer header corruption** from LNA-saturation-induced spurious
  decodes (`coderate=0`, observed in HAL ERROR logs). The channelizer
  may start decoding what it thinks is a valid frame and never reach
  end-of-frame, leaving busy asserted.
- **Cosmic-ray / SEU on the channelizer hardware state** (one bit flip
  in an internal channel-state machine). This is the textbook
  "embedded device hangs occasionally for no logged reason" pattern.

## What we don't yet have — `[OPEN]`

- Semtech datasheet mapping of file `0x0e`. We infer it's a channelizer
  busy flag, but not officially confirmed.
- Demonstration that we can reproduce the stall on demand.
- Forensics from an actual stuck event with the new dump_mem +
  probe_mem captures.
- Confirmation of the fix idea by actual test.

## Patch design — `[HYPOTHESIS]`

Replace `BTFSC 0x0e, 0; GOTO 0x04d5` with a counter-decrement-and-fall-out
pattern. PIC16 lacks flexibility for in-place patches without affecting
surrounding code, but two strategies look viable:

### Strategy A: BTFSC → BTFSS semantic flip + reuse a free file as counter

Replace `BTFSC 0x0e, 0` with `BTFSS 0x0e, 0` (skip-if-set), then
have `0x04d6` test a counter file. Requires careful PCLATH and a
free file register (file 0x60 is `clrf 0x60` at 0x04d2 then unused
in the wait-loop, candidate).

### Strategy B: redirect on busy via existing fall-through

Change `0x04d6: GOTO 0x04d5` to `0x04d6: GOTO 0x04ca` (re-init point).
On stuck, ARB jumps to its `clrf 0x1b` → `clrf 0x60` → `MOVLW 0x01;
MOVWF 0x1b` cycle. This is "spin once, then reset and try again".
Risk: if the HW bit really is stuck (not transient), this just creates
a fast restart loop with no actual recovery. Mitigation: add a
timeout-decrement file in the re-init path so eventually ARB stops
trying and host can detect via ARB_STATUS.

Both strategies require careful PIC16-instruction encoding with the
right parity bits. Strategy A is cleaner; Strategy B is safer for a
first attempt.

## Verification commitments before applying any patch

We will NOT apply any patch without:

1. **Reproducing the stall** with an external trigger.
2. **Capturing PC** of the ARB MCU at stuck time, confirming it is
   `0x04d5` or `0x04ed`.
3. **Reading file 0x0e** at stuck time to confirm bit 0 is set.
4. **No-op patch first** (write same bytes back, verify chip still
   boots and processes packets — already done in `analysis/reversible_patch.c`).
5. **Symbolic trace** of the patched code path through all branches.
6. **DRY_RUN-equivalent first** (write the patch, immediately revert,
   verify no behavior change).

The `analysis/reversible_patch.c` tool already supports automated
rollback.

## What to do next

1. Wait for the next real stall, capture forensics with the upgraded
   pipeline. Verify ARB PC is in the loop region (`[OPEN]` step 1).
2. If confirmed, design the patch with both strategies A and B
   prepared, parity-bits computed, instruction encoding verified
   against gpdasm.
3. Apply patch, monitor 24h, compare RX-rate to baseline.
4. If no further stalls in 7 days, declare patch effective and merge.
