# ARB-fw Patch Strategy A — finalisiert (offline, 2026-05-02)

## Goal

Heal the ARB-MCU busy-spin at words 0x04d5↔0x04d6 by adding a bounded-iteration
escape via a free file-register counter. The original code spins forever on
`file[0x0e] bit 0` (HW_BUSY); after this patch, the spin terminates after
at most 256 iterations regardless of HW state.

## Final patches (4 slot modifications)

```
addr     orig    →  patched      mnemonic                            comment
─────────────────────────────────────────────────────────────────────────────
0x04d6   2cd5    →  2c04         GOTO 0x0404                         redirect spin
0x0404   bfff    →  0ff0         INCFSZ 0x70, F                      counter, skip on wrap
0x0405   bfff    →  2cd5         GOTO 0x04d5                         loop back to spin
0x0406   bfff    →  6cd7         GOTO 0x04d7                         escape to original continue
```

All 4 slots verified parity-correct (popcount mod 4 → P0, P1) by feeding
through `analysis/pic16_disasm.py` after the patch.

## Encoded slot bytes (LE, ready for lgw_mem_wb)

```
0x04d6:  04 2c
0x0404:  f0 0f
0x0405:  d5 2c
0x0406:  d7 6c
```

## Why this works

Original control flow:

```
0x04d5: BTFSC 0x0e, 0    ; if bit clear: skip next (HW ready)
0x04d6: GOTO  0x04d5     ; if bit set: spin (UNBOUNDED)
0x04d7: MOVLW 0x25       ; HW ready → continue
```

Patched control flow:

```
0x04d5: BTFSC 0x0e, 0    ; (unchanged) HW ready check
0x04d6: GOTO  0x0404     ; (patched) jump to escape handler
0x04d7: MOVLW 0x25       ; (unchanged) continue path
...
0x0404: INCFSZ 0x70, F   ; counter++; skip GOTO on wrap (overflow)
0x0405: GOTO  0x04d5     ; back to spin (counter not zero)
0x0406: GOTO  0x04d7     ; escape: jump to original continue
```

Counter at file `0x70` increments each spin iteration. Wraps from 0xFF
to 0x00 every 256 iterations, INCFSZ skips the GOTO at 0x0405 → falls
through to 0x0406 → GOTO 0x04d7 → original continue path resumes.

**Worst case stuck-recovery**: 256 iterations × ~3 cycles = ~768 cycles.
At 32 MHz core clock = **~24 µs**. Fast enough that healthy operation
sees no perceptible delay (channelizer's typical busy-time is ≪ 24 µs);
if HW is genuinely stuck, MCU exits in 24 µs and downstream code may
re-init the channelizer naturally.

**Counter does NOT need initialization**. Wraps every 256 increments
regardless of starting value. After every escape, counter is left at
some value < 256 from wrap; next stuck event triggers escape after at
most 256 more increments.

## Validation done offline

1. **Parity-correctness**: each new slot encoded with `slot = word14 |
   (popcount(word14) & 1) << 14 | ((popcount(word14) >> 1) & 1) << 15`.
   Disassembler reads back parity column "0" / "1" matching expected.
2. **Original slots verified**: 0x04d6 was indeed `GOTO 0x04d5` (0x2cd5),
   0x0404-0x0406 were all 0xBFFF filler (`ADDLW 0xFF` ineffective code).
3. **Page-locality**: all 4 patched addresses + jump targets are within
   the same 2KB code page (Page 0, addresses 0x000-0x7FF). No PCLATH
   manipulation needed, standard 11-bit GOTO encoding suffices.
4. **Counter register choice**: file 0x70 — never appears as direct
   operand in any of 4096 ARB instructions (byte-ops, bit-ops, INDF).
   In Bank 0 (ARB never enters Bank 1 via STATUS bit 5/6 BSF).
5. **Escape-handler location**: 0x0404-0x0406 are the last 3 slots of
   the 994-slot filler run 0x0025-0x0406, immediately before real code
   resumes at 0x0407. Any control-flow that *accidentally* fell into
   0x0404 from neighboring address would already be undefined behavior
   — our handler at least produces a deterministic GOTO chain.

## Status of original 4 Vorarbeiten

1. ✅ **PIC16 Parity-Algorithm cracked** — docs/14
2. ✅ **Free file-register identified** (0x70) — this doc
3. ⏭️ **Channelizer-Reset-Bit aus PIC16-Sicht** — **OBSOLET** for Strategy A.
   Strategy A bounds the spin without resetting the channelizer; the
   channelizer-reset path remains via external `modem_cycle` if needed.
4. ⬜ **Test-Deploy via reversible_patch + Persistent-Hook** — next step.

## Test-Deploy plan (Vorarbeit 4)

1. **Stop pkt-fwd + watchdog timer** (avoid SPI race + watchdog escalation
   during MCU halt).
2. **Apply patch via `reversible_patch`** — built-in halt + lgw_mem_wb +
   verify + auto-restore-on-mismatch:
   ```
   sudo /home/pi/sx1302_hal/util_dump_status/reversible_patch \
     arb 0x04d6 04 2c
   sudo /home/pi/sx1302_hal/util_dump_status/reversible_patch \
     arb 0x0404 f0 0f
   sudo /home/pi/sx1302_hal/util_dump_status/reversible_patch \
     arb 0x0405 d5 2c
   sudo /home/pi/sx1302_hal/util_dump_status/reversible_patch \
     arb 0x0406 d7 6c
   ```
   (or extend reversible_patch to accept multiple patches in one halt-cycle)
3. **Verify in-place**: read back via probe_mem, compare bytes,
   confirm PARITY_ERROR=0 in dump_status.
4. **Resume MCU + restart pkt-fwd + watchdog**.
5. **Observe 24h**: stuck-frequency expected to drop to 0 if Strategy A
   is correct. Compare against baseline ~1/7h MTBF.
6. **A/B comparison**: revert via `reversible_patch` (auto-restore from
   saved bytes), observe stuck rate, then re-apply to confirm.

## Persistence (after 24h healthy + 1 stuck-free observation)

ARB-fw is reloaded from blob each time `lora-pkt-fwd` starts (via
`lgw_start()` calling `sx1302_load_firmware_arb`). So our patch lives
only until the next daemon restart. To make it permanent:

**Option A**: hook into `reset_lgw.sh start` ExecStartPre — after the
GPIO-pulse but before pkt-fwd starts, run a `reversible_patch` apply
sequence. Requires synchronization: pkt-fwd's HAL `lgw_start()` itself
calls `sx1302_load_firmware_arb` and writes the unpatched blob.

**Option B**: patch the on-disk blob `/opt/lora-dashboard/pi/firmware/arb_fw.bin`
once. Then HAL loads patched blob into MCU directly. Simpler, but
loses the patch if blob is replaced from an upstream update (which is
unlikely since sx1302_hal is dead since 2021).

**Recommended**: Option B initially (single file edit, bypass-able by
re-rsyncing the original blob from upstream). After ≥1 week proven, fold
into a self-maintained vendor-blob with a clear marker in the repo.

## Risk register

- **Counter starts at random RAM value**: handled by INCFSZ wrap behavior.
  No init needed. First entry might overflow as early as 1 iteration if
  RAM happens to hold 0xFF; that's fine because we only need at-least-one
  escape pathway.
- **Escape handler executes BEFORE BTFSC re-check**: by design. We exit
  the spin without rechecking HW. If HW is genuinely ready by escape time,
  no harm. If HW is stuck (the case we're trying to handle), original
  continue path may run with a spurious channelizer state — must be
  validated empirically that downstream code copes (likely OK since
  `MOVLW 0x25` and following channel processing operates on its own
  state, channelizer-stuck doesn't block channel scheduling).
- **PARITY_ERROR**: should remain 0 since all 4 patches are parity-
  correct per `popcount mod 4` formula. Confirm in step 3 of test-deploy.
- **Patch persistence**: pkt-fwd restart re-loads original blob,
  loses patch. Mitigation: use Option B (patch on-disk blob).
