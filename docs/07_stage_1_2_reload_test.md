# Stage 1.2 reload test — does fw blob reload reset the state machine?

**Date**: 2026-04-29 18:02-18:04 UTC
**Tool**: `analysis/reload_mem.c` (built fresh: halt MCU, lgw_mem_wb the
fw blob, release halt; observes AGC_STATUS pre and post)

## Hypothesis

If `AGC_STATUS = 0x14` stuck state is a logic-state lockup of an
otherwise intact firmware (per `06_sram_integrity_test.md`), then
re-writing the same fw blob into halted SRAM should reset the state
machine. We expect AGC_STATUS to drop from any sticky value to 0x01
(init-wait state) immediately on resume.

## Method

1. Read AGC_STATUS via concurrent `dump_status` (no halt) to capture
   pre-test state with daemon running.
2. Stop daemon for clean SPI single-master.
3. Run `reload_mem agc /tmp/agc_fw.bin`: halts AGC MCU, writes fw blob,
   verifies bytes match, releases halt. Reads AGC_STATUS pre and post.
4. Run `dump_status` to confirm post-reload AGC_STATUS evolution.
5. Repeat for ARB MCU.
6. Restart daemon (HAL goes through full lgw_start: load fw + init
   mailbox sequence + main loop).
7. Read AGC_STATUS once more.

## Observed sequence

```
T+00s   pre-stop concurrent read:  AGC=0x00, ARB=0x00, PARITY=0, RSSI=67
T+02s   daemon stopped
T+03s   reload AGC:
          PRE-reload:  AGC_STATUS=0x00  ARB_STATUS=0x00
          verify: SRAM matches blob byte-for-byte
          POST-reload: AGC_STATUS=0x01  ARB_STATUS=0x00  PARITY_agc=0
T+04s   dump_status: AGC=0x01, ARB=0x00, PARITY=0, RSSI=98
T+05s   reload ARB:
          PRE-reload:  AGC_STATUS=0x01  ARB_STATUS=0x00
          POST-reload: AGC_STATUS=0x01  ARB_STATUS=0x00  PARITY_arb=0
T+06s   dump_status: AGC=0x01 (still), RSSI=100
T+07s   daemon restart
T+15s   dump_status: AGC=0x00, ARB=0x00, PARITY=0, RSSI=75 (healthy main loop)
```

## Findings

### 1. Reload mechanism works as predicted

Pre-reload AGC_STATUS = 0x00. Post-reload AGC_STATUS = 0x01. The
reload definitively resets the AGC fw state machine to its first init
wait-state. The fw begins waiting for the host's mailbox-3 init
sequence (which we did not provide; the daemon-restart in step 6 then
performed the full HAL init and brought AGC_STATUS to 0x00 main loop).

This validates the **Stage 1.2 hypothesis mechanistically**: a fw
blob reload heals a state-machine lockup without any HAT power
manipulation. Cost: ~1 second of MCU halt + a single lgw_mem_wb.

### 2. We could not test "heals an actual stuck state"

By the time the test ran, AGC_STATUS had already returned to 0x00.
The test did not catch the chip in the 0x14 state we saw two hours
earlier. So: mechanistic heal-from-init confirmed, but
heal-from-stuck not directly demonstrated. The mechanism should
work the same way (any state → halted SRAM → write fw → resume = init
wait at 0x01), but until we catch a real stuck-state and try the
reload, this remains inference.

### 3. AGC_STATUS = 0x14 is NOT a permanent stuck-state

Reading the dashboard DB shows three AmbaSat packets received between
the heal at 16:54 and now, while AGC_STATUS was reading 0x14:

| Time UTC | RSSI | SNR | AGC_STATUS at the time |
|----------|------|-----|------------------------|
| 16:54:16 | -108 | -19.5 | not measured (too soon after heal) |
| 17:06:46 | -78 | +9.5 | 0x14 (live trace at 17:09) |
| 17:25:35 | -78 | +9.0 | 0x14 (live trace at 17:25) |
| 17:44:25 | -78 | +9.0 | unmeasured but presumed 0x14 |

Packets continued to arrive with **normal RSSI/SNR** while
AGC_STATUS = 0x14. By the time we ran the reload test (18:02),
AGC_STATUS had transitioned back to 0x00.

This forces a major revision of our earlier interpretation:

**Old read (wrong)**: 0x14 was a degraded sub-state from the stall.
**New read**: 0x14 is a **legitimate idle/quiet sub-state** the AGC
fw enters when the channel is quiet. The fw transitions back to
0x00 main-loop on RX activity or some other trigger we have not
isolated yet. Bit 4 might be "AGC quiescent / waiting for activity"
and bit 2 some subordinate flag.

The **real** stuck-state was the 63-minute window 15:51-16:54 where
NO packets arrived despite confirmed AmbaSat-1 transmission. The
chip during that window had no live snapshot — the watchdog probe
fired its (now-known-buggy) read after stopping the daemon, so we
saw post-stop register values, not actual stuck-state values. That
said, the user's confirmation that AmbaSat-1 was transmitting
remains decisive: that window was a real RX failure.

## Implications

### For watchdog-trigger logic

The current watchdog cannot distinguish "0x14 normal idle" from
"0x14 stuck" by reading AGC_STATUS alone. Trigger on **CHAN_RSSI_LIVE
all-zero** plus **last_packet > N hours** remains correct. Ignore
AGC_STATUS values for trigger logic; use them only for forensics.

### For Stage 1.2 deployment

Mechanistically a Stage 1.2 reload (between Stage 1 daemon restart
and Stage 1.5 GPIO18-low cycle) is **viable and safe**. A reload
plus daemon restart costs ~1 second of MCU halt and ~1 second of
SPI write. If a future stall does not heal at Stage 1, trying
Stage 1.2 before Stage 1.5 is a strict improvement: cheaper
and avoids HAT power perturbation.

**Status 2026-04-29 PM**: deployed live in `hibbes/lora-dashboard`
commit `a0f1257` on `main`. Watchdog escalation order is now:

| Stage | Action | Time after Stage 1 | Cost | Heals |
|-------|--------|--------------------|----- |-------|
| 1   | systemctl restart lora-pkt-fwd | 0     | ~5 sec daemon downtime | restart-class issues |
| 1.2 | reload_mem agc + arb (halt+wb+resume) | +15 min | ~5 sec daemon + 1 sec halt | fw state-machine lockups [NEW] |
| 1.5 | GPIO18-low 10 sec cold-boot | +30 min | 10 sec HAT 3.3V down | SX1250 + SRAM-wipe class |
| 2   | systemctl reboot | +45 min | ~60 sec full Pi cold boot | everything else |

Cooldowns prevent loops: Stage 1 retries after 1 h, Stage 1.2 after
90 min, Stage 1.5 after 2 h, Stage 2 blocks all stages for 6 h.

Pre-requisites on Pi (one-time manual install):
- `/home/pi/sx1302_hal/util_dump_status/reload_mem` (compiled from
  `analysis/reload_mem.c`)
- `/opt/lora-dashboard/pi/firmware/agc_fw_sx1250.bin` and `arb_fw.bin`
  (extracted from the HAL `.var` files)

If reload_mem binary or fw blobs are missing, the watchdog logs a
warning and falls through to Stage 1.5 (graceful degradation, no
crash).

### For investigation

The 0x14-vs-0x00 transitions over time are themselves data. If we
periodically log AGC_STATUS (e.g., every 5 min via the existing
dump_status calls in the watchdog probe, now read-only) we can
correlate AGC_STATUS history against packet arrival times and the
watchdog's actions. Over a week of data we should be able to map
out the legitimate state transitions and detect anomalies.

### What the next real stall will tell us

When the next stall happens (with the fixed watchdog), the forensics
snapshot will be taken **before** any healing action and will capture
the actual stuck-state register values. At that point we should:

1. Run `dump_mem` to verify SRAM integrity (expected: still intact).
2. Run `reload_mem` as Stage 1.2 attempt.
3. If reload heals (AGC_STATUS goes 0x01 → ... → 0x00 and packets
   resume), Stage 1.5 GPIO-cycle is no longer needed for this failure
   class.
4. If reload does not heal but Stage 1.5 does, the lockup is
   somewhere outside the 8 KB MCU code window (possibly in another
   memory region we have not mapped, or in the SX1250 radio chips
   that GPIO18-low fully resets via HAT 3.3V collapse).

Either result is informative.
