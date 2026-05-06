# SRAM integrity test — running fw vs loaded blob

**Date**: 2026-04-29 17:58 UTC
**Result**: AGC and ARB SRAM are byte-for-byte identical to the loaded fw blobs.

## Method

Built a small `dump_mem` tool (see `analysis/dump_mem.c`) that follows
the HAL fw_check sequence: halt the target MCU via `MCU_CLEAR=1` +
`HOST_PROG=1`, `lgw_mem_rb` 8192 bytes from `AGC_MEM_ADDR=0x0000` (or
`ARB_MEM_ADDR=0x2000`), then release halt. The MCU resumes with the
same SRAM state it had before the read.

Daemon was stopped (~4 sec) for clean SPI single-master access. After
release the daemon was restarted and continued normal RX.

## Result

```
=== AGC: blob(firmware/agc_fw.bin) vs sram(agc_sram_running.bin) ===
  total bytes: 8192
  diff bytes:  0 (0.000%)
  IDENTICAL — running SRAM is byte-for-byte the loaded fw

=== ARB: blob(firmware/arb_fw.bin) vs sram(arb_sram_running.bin) ===
  total bytes: 8192
  diff bytes:  0 (0.000%)
  IDENTICAL — running SRAM is byte-for-byte the loaded fw
```

Conditions during the test:
- AGC_STATUS = 0x14 sticky for >2.5 minutes (read just before the test)
- AGC_PARITY_ERROR = 1 sticky
- ARB_PARITY_ERROR = 1 sticky
- Packets received normally (AmbaSat-1 at 17:25:35 UTC, 33 min before test)

## Conclusions

### What this disproves

- **SRAM corruption by SEU / EMI**: ruled out. Not a single bit flipped.
- **fw self-modification**: the fw does not write back to its own code
  region. (Or if it does, the writes match the original blob bytes.)
- **Issue #14 PARITY=1 is genuinely a false-positive**: even with both
  PARITY_ERROR bits at 1, the actual fw bytes are perfectly intact.
  Semtech-maintainer mcoracin's recommendation to ignore the PARITY
  reads is empirically validated.

### What this implies for the AGC_STATUS = 0x14 stuck state

The 2026-04-29 PM stall (user-confirmed: AmbaSat-1 transmitted during
the silent window) was **not** caused by fw corruption. The fw itself
was intact, just like it is now. The chip got stuck in a logic state
of an otherwise perfectly-loaded firmware.

This narrows the failure mode dramatically:

| Hypothesis | Status |
|------------|--------|
| H1: Bit-OR accumulation produces 0x14 | Still plausible. Best fit. |
| H2: Literal load from Block 4 LUT | Still plausible. |
| H3: Degraded operational sub-state | Still plausible — the fw is intact but in a state it cannot exit unaided. |
| H4: SPI read corruption from RX-load | Now ruled out as cause of 0x14 (but explains PARITY=1). |
| H5 (new): Closed-source fw has a real state-machine bug | Now strongly suggested. |

### What this implies for recovery

The Cold-Boot (GPIO18-low-2s in `reset_lgw.sh`) heals not because it
fixes corrupted memory, but because **wiping SRAM forces the fw state
machine back to the power-on-reset state**. The fw bytes themselves
were never damaged.

This means a much cheaper recovery should be possible: **rewrite the
fw via `lgw_mem_wb` while the MCU is halted, then resume**. No HAT
power cycle, no Pi reboot, just a re-load of the same bytes back
into SRAM. The state machine reinitializes from `AGC_STATUS = 0x01`
upward through the init sequence and reaches main loop again.

This is unverified — it should be tested on the next stuck-state
incident as a Stage 1.2 (between Stage 1 daemon restart and Stage 1.5
GPIO18-low cycle). If it heals, we have a non-disruptive recovery
path that does not need any HAT power manipulation.

## Hardware-level implications

The fact that we can halt the MCU, read all 8 KB of SRAM via SPI, and
resume without breaking the running state is **a working debug
backdoor for the SX1302 internal MCUs**. It opens up:

- Single-step (patch one halfword to known value, observe AGC_STATUS).
- Differential snapshots: read SRAM at multiple stuck-state moments
  and diff them. If non-fw RAM regions exist (above 0x1FFF for AGC),
  diffs across snapshots would localize the state-machine variables.
- Memory-window probing: extend `lgw_mem_rb` to addresses outside
  the MCU_FW_SIZE window, look for additional accessible memory.

These are next steps for a future investigation.

## Files

- `analysis/dump_mem.c` — the tool source (build with HAL libs)
- `snapshots/2026-04-29_post-heal-live-trace/agc_sram_running.bin` — first 8 KB at 0x0000
- `snapshots/2026-04-29_post-heal-live-trace/arb_sram_running.bin` — first 8 KB at 0x2000
- Both binaries are identical to `firmware/agc_fw.bin` / `firmware/arb_fw.bin` respectively.
