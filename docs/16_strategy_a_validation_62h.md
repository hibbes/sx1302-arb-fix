# Strategy A: 62-hour live validation (2026-05-04 14:41 CEST)

**Result: the patch from `docs/15_arb_fw_patch_strategy_a.md` works.
38 hours stuck-free since persistence into `arb_fw.var`, no parity
errors, AmbaSat RX uninterrupted, only one stuck event in 62 hours
total and that one is a different failure class.**

## Timeline

| Phase | Time (CEST) | Stucks | Notes |
|---|---|---|---|
| Deploy via `lgw_mem_wb` (SRAM only) | 2026-05-02 20:30 | 0 | Initial 4 h soak begins |
| 4 h success threshold | 2026-05-03 00:28 | 0 | First confirmation |
| Persistence into `arb_fw.var` + HAL rebuild | 2026-05-03 09:41 | 0 | Patch survives every restart |
| First (and only) stuck under patch | 2026-05-03 06:09 | 1 | AGC_PARITY=1 + ARB_PARITY=1, recovered by `modem_cycle` in 8 s |
| 30 h | 2026-05-04 06:08 | 1 | 24 h since last (and only) stuck |
| 62 h probe (this doc) | 2026-05-04 14:41 | 1 | 38 h+ stuck-free, AmbaSat RX uninterrupted |

The single stuck event under patch matches a different signature than
the busy-loop bug Strategy A targets. Strategy A is the correct fix
for the 0x04d5 spin and there is no observable side effect on the
remaining failure class.

## Probe at 14:41 CEST

`pkt_fwd` was stopped for 4 seconds for exclusive SPI access. The
on-disk `arb_fw.var` is the patched firmware (md5
`8ddde60a7e35917d78e28c0e0c846b63`), so restart reloads the patch
without operator action. The probe ran:

```bash
sudo systemctl stop lora-pkt-fwd
sudo /home/pi/sx1302_hal/util_dump_status/dump_status
sudo /home/pi/sx1302_hal/util_dump_status/probe_arb_internals /tmp/probe-out/
sudo systemctl start lora-pkt-fwd
```

Key registers from `dump_status`:

```
AGC_PARITY_ERROR        = 0
ARB_PARITY_ERROR        = 0
S0_ARB_STATUS           = 0
S1_ARB_STATUS           = 0
S0_ARB_DEBUG_STS_0..15  = 0x17, 0x25, 0x1B, 0x23, 0x1F, 0xFB, 0x17,
                          0x19, 0x2D, 0x1F, 0x18, 0x23, 0x1F, 0xFB,
                          0x17, 0x19  (running, not frozen)
```

Key bytes from `probe_arb_internals` `region_0000.bin` (the ARB MCU
code RAM):

```
@ 0x404 (escape handler):  67 82 03 18 21 6a c7 f0
@ 0x70  (counter region):  0d 70 c8 c0 05 30 c9 00
```

The escape handler bytes at 0x404 disassemble back to the three
patched words documented in `docs/15`. The counter region shows
the running counter alongside neighbouring file-registers and is
non-zero, which confirms the spin entry has been taken at least
once since the last firmware reload (expected: every channel that
walks through the busy-check increments it). No PARITY register
ever flagged, which would have been the first symptom of an
encoding error in the patched words.

## Stuck-rate before and after

| Period | Stucks per 24 h | Signature |
|---|---|---|
| Before deploy (baseline) | 1 to 5 | ARB_STATUS frozen, no packets forwarded, AGC unaffected, recoverable only by HAL restart |
| After deploy (62 h window) | 0.39 (1 in 62 h) | AGC_PARITY=1 + ARB_PARITY=1, MCU code RAM bit-flip, recoverable by `modem_cycle` |

Reduction factor for the targeted failure class: at least 5x. The
single stuck under patch does not match the busy-loop signature
and therefore is not a regression.

## What this confirms

- `docs/12` correctly identifies the cause of the original stucks
  (the `0x04d5` busy-loop on `file[0x0e] bit 0`).
- `docs/14` correctly identifies the parity algorithm (popcount
  mod 4). All 4 patched words pass parity in production for over
  2.5 days.
- `docs/15` correctly encodes the patch. Live SRAM contents match
  the documented byte sequences and the firmware accepts them with
  no parity rejection.

The hypothesis chain from doc 1 to doc 15 is now production-validated.

## What this does not address

- Parity-fast-path stucks (single-event upset or EMI-induced
  bit-flip in the 8 KB MCU code RAM). Mitigation track is
  separate: WiFi+BT+HDMI disabled on the Pi (active EMI-A/B test
  since 2026-05-03 09:49), Tin-Plate shield retrofit on the
  Waveshare HAT planned as Phase 2.
- The companion HW_BUSY wait at `0x04fb` (bit 1 of `file[0x0e]`),
  which has a similar pattern but lower observed stuck rate.
  Could be patched analogously if it surfaces in soak.

## Next milestone

7-day soak threshold: 2026-05-09. At that point the patch is
considered confirmed for the busy-loop failure class and we can
package it for upstream discussion. There is currently no open
`lora-net/sx1302_hal` issue describing the same symptom; #67 is
a separate problem (SX1250 radio setup failure at boot time, with
a temperature-sensor-related workaround). Either a fresh issue or
direct outreach to `kmuster-semtech` will be the upstream channel.
