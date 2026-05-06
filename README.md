# sx1302-arb-fix — A live-validated fix for the SX1302 ARB-MCU busy-spin RX-stall

> **Status:** Patch deployed on a single hobbyist gateway 2026-05-02,
> **3.5 days stuck-free at the time of release** (was 1–5 stalls / 24 h
> before). This is one Pi, one HAT, one location — reported as
> field-evidence, not as a final proof. Independent reproduction welcome.

> **A note on confidence:** I'm a teacher with a single test gateway, not
> a Semtech engineer. There is a real possibility that this whole
> investigation is solving a problem that doesn't exist for anyone else,
> or that there is a documented mitigation in an application note I
> haven't read. If you find such a note, please open an issue — I'll add
> a `RESOLVED:` block at the top of this README and we'll all move on.

## What is this?

The Semtech SX1302 LoRaWAN concentrator contains three internal PIC16 MCUs
running closed-source firmware (AGC, ARB, Calibration). Production gateways
based on this chip — including the Waveshare SX1302-868M HAT used here —
exhibit a recurring pathology: **silent RX-stalls** lasting from minutes to
hours during which the chip stops forwarding any uplink, but *appears* alive
to the host (no kernel error, ackr=100% to the LNS, healthy SPI). The user
sees their devices transmit, but nothing reaches The Things Stack.

After two months of forensic work on a single Pi+HAT setup, we identified an
**unbounded busy-spin in the ARB-MCU firmware** as one of the root causes.
This repository contains the analysis tooling, the patch strategy, and the
validation data — but **deliberately not** the disassembly of Semtech's
firmware itself.

## What is *not* in this repo

- ❌ The full disassembly of Semtech's `arb_fw.var` / `agc_fw.var` /
  `cal_fw.var`. These are derivative works of Semtech IP; we use them
  internally for analysis under the EU interoperability exception
  (Directive 2009/24/EC Art. 6) and the German equivalent (§ 69d UrhG),
  but we do not redistribute them.
- ❌ The patched `arb_fw.bin` binary. Apply the patch yourself on your own
  copy of the firmware (instructions below). You have the right to modify
  software you legitimately operate to keep your hardware functional.

## What is in this repo

```
analysis/               Tools (Python + C) — original work, BSD-3-Clause
docs/                   Methodology, bug location, patch strategy, validation
snapshots/              Forensic data captured from our gateway during stalls
```

### Tools (analysis/)

| File | Purpose |
|---|---|
| `pic16_disasm.py` | Independent PIC16 mid-range disassembler used to verify Microchip's `gpdasm` output instruction-by-instruction |
| `parity_fit.py` | Brute-forces the SX1302 internal parity check used by the ARB-MCU SRAM (PIC16 14-bit code, 2-bit parity per slot) |
| `free_reg_finder.py` | Identifies `file` registers in the PIC16 RAM bank that are referenced nowhere by the original firmware |
| `free_code_slots.py` | Identifies code-RAM slots that contain `RETLW 0x00` filler — i.e. addressable memory that the original firmware never executes |
| `patch_encoder.py` | Encodes 14-bit PIC16 instructions back to the SX1302's parity-checked SRAM format |
| `reload_mem.c` | C tool: halts the SX1302, writes a firmware blob to MCU SRAM via `lgw_mem_wb`, verifies byte-for-byte via `lgw_mem_rb`, releases halt |
| `probe_arb_internals.c` | Reads ARB-MCU runtime state (status, debug, parity, working SRAM regions) without disrupting packet forwarding (requires brief `lgw_stop`) |
| `reversible_patch.c` | Applies a binary diff against the on-disk `arb_fw.bin`, with auto-backup and rollback |

### Docs (docs/)

| File | Topic |
|---|---|
| `12_arb_busy_loop_bug_candidate.md` | Identification of the busy-spin at offset `0x04d5↔0x04d6` in the ARB-MCU firmware — symptom, register evidence, repro conditions |
| `14_pic16_parity_algorithm.md` | The parity algorithm Semtech uses to protect the MCU SRAM, cracked and verified to zero residual against 8192 slots |
| `15_arb_fw_patch_strategy_a.md` | Counter-bounded escape patch — uses one free register (`0x70`) as iteration counter, three free code slots (`0x0404–0x0406`) as escape handler |
| `16_strategy_a_validation_62h.md` | First 62 h of post-deploy observation: 1 stall vs ~10 in the equivalent prior window |

### Snapshots (snapshots/)

Forensic data we captured on **our own** gateway (Raspberry Pi 3B + Waveshare
SX1302-868M HAT, TTN EU1, AmbaSat-1 device on 868.1 MHz SF12). All files are
our own measurements — `dump_status` outputs, kernel interrupt counters,
journalctl excerpts, working SRAM dumps captured during the live trace.

## The bug, in one paragraph

At code address `0x04d5` the ARB-MCU firmware reads a hardware-busy bit from
register `0x0e<0>` and performs `BTFSC 0x0e, 0` followed by `GOTO 0x04d5`. If
the busy bit never clears (which happens occasionally — likely cosmic-ray
SEU, marginal SX1250 IRQ timing, or a known erratum we haven't found public
documentation for), the MCU spins here forever. The host sees a chip that is
"alive" (SPI works, AGC reports `0x14` steady-state with both parity bits
set), but no LoRa packet ever reaches the application layer.

## The patch, in one paragraph

We replace the `GOTO 0x04d5` at offset `0x04d6` with a `GOTO 0x0404`, where
we install a three-instruction escape handler that increments a counter
(`file 0x70`) and either loops back to the original spin or jumps to the
post-spin continuation at `0x04d7` after 256 iterations. At 32 MHz core
clock that's ~24 µs of patience — well below any LoRa SF12 symbol time, so
healthy operation is unaffected. Stall states escape automatically.

The patch occupies **4 instruction slots**; the modified firmware preserves
the original parity scheme (verified: `AGC_PARITY_ERROR = 0`,
`ARB_PARITY_ERROR = 0` after deploy).

## How to apply (on your own gateway)

1. Read `docs/12_arb_busy_loop_bug_candidate.md` and confirm symptoms match.
2. Build the analysis tools: `cd analysis && make` (TODO: write Makefile).
3. `analysis/probe_arb_internals` against your live gateway → confirm
   `S0_ARB_DEBUG_STS_*` shows the suspected steady-state.
4. Apply the patch via `analysis/reversible_patch arb 0x04d6=goto:0x0404`
   (TODO: finish CLI of reversible_patch).
5. Reload via `analysis/reload_mem arb path/to/arb_fw_patched.bin`.
6. Verify with `probe_arb_internals` again — both parity bits should be 0,
   die-temp unchanged, ackr=100%.

## Why public from day one

This is an operational bug in commodity hardware, not a security
vulnerability. There is no embargo and no responsible-disclosure window —
the more eyes on the methodology, the faster we'll know whether other
operators see the same pathology or whether our setup is unusual.

If anyone from Semtech (or another vendor of SX1302-based equipment) has
context on `AGC_STATUS = 0x14`, on a documented mitigation we missed, or
on an upstream HAL fix already in flight, please open an issue and I'll
update this README accordingly.

## Legal

The analysis tools, documentation, and snapshot data in this repository are
**original work** by the contributors and licensed under **BSD-3-Clause**
(see `LICENSE`).

The reverse-engineering work that this repository depends on was performed
on firmware lawfully obtained as part of the BSD-3-licensed
`Lora-net/sx1302_hal` project, and limited to **what is necessary to achieve
interoperability** between Semtech's silicon and a working LoRaWAN gateway —
the explicit safe-harbor of EU Directive 2009/24/EC Art. 6 and German
§ 69d UrhG.

If you are Semtech and would prefer different disclosure timing or
contribution arrangement, please contact us.

## Acknowledgements

- The Lora-net team at Semtech for `sx1302_hal` (BSD-3-Clause)
- Microchip for the publicly documented PIC16 mid-range ISA
- The AmbaSat-1 community whose unbroken transmission stream made every
  RX-stall trivially reproducible
- gputils for `gpdasm`
