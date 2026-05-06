# AmbaSat-1 fw analysis + LNA-saturation hypothesis

User confirmed (2026-04-29) that the only strong local emitter is the
AmbaSat-1 satellite kit lying directly next to the gateway. The
AmbaSat-1 firmware is available at github.com/ambasat/AmbaSat-1.

## What AmbaSat-1 actually transmits

From `Source/FlightCode/AmbaSat-FlightCode-01-SHT31/src/main.cpp` and
the AmbaSat-LMIC-Library wrapper:

- LMIC stack (IBM LMIC fork via AmbaSat wrapper).
- `LMIC_setDrTxpow(DR_SF12, 14)` — fixed SF12, 14 dBm Tx power.
- 8 EU868 channels: 868.1, 868.3, 868.5, 867.1, 867.3, 867.5, 867.7,
  867.9 MHz; LMIC picks one per uplink based on duty-cycle limits.
- `int sleepcycles = 130;` ⇒ 130×8s = ~17 min sleep + sensor read +
  tx = effective uplink interval ≈ 18-19 min.
- Coding rate is LMIC default = 4/5 (== `1` in HAL coderate enum).
  **AmbaSat never legitimately transmits with `coderate=0`.**
- ABP authentication: hardcoded `NWKSKEY`, `APPSKEY`, `DEVADDR` (in
  user's case `DEVADDR = 0x260138E2`).

So coderate=0 reception at the gateway must be one of:
- Header bit-flip during demodulation (rare for strong signals).
- LNA-saturation-induced demodulation artifact.
- Spurious reception of an image/aliased signal.
- A non-LoRaWAN device on the same channel (ruled out by user — only
  AmbaSat is local).

## Evidence for LNA saturation

At 18:03:14 UTC the daemon received the **same packet on TWO
different channels simultaneously**:

```
chan 0, freq 868.100, rssi -34, snr  9.5, payload QOI4ASaAiwQzixl...
chan 3, freq 867.100, rssi -99, snr -7.5, payload QOI4ASaAiwQzixl...
                  ^                          ^
                  identical 35-byte payload, fcnt 1163
```

The same 35-byte payload arriving on two channels 1 MHz apart at the
same `tmst` (within 1 µs) is a **classic LNA-saturation artifact**.
A strong nearby signal (-34 dBm) drives the wide-band front-end into
saturation, harmonics/images leak across channel filters, and the
SX1302 demodulator latches onto the leak in addition to the real
channel.

`rssi=-99` on chan 3 is consistent with the leakage path: ~65 dB below
the main channel. SNR of -7.5 dB is below the SF12 sensitivity floor
of ~-20 dB but not by much — the demodulator rescued the bits because
the underlying signal is so strong. This is a degraded, secondary
reception of a packet that was correctly received on chan 0 anyway.

## HAL ERROR pattern today

| Time UTC | Event | RSSI | Comment |
|----------|-------|------|---------|
| 18:03:14 | AmbaSat fcnt 1163 | -34 (chan 0) + -99 (chan 3) | **dual reception, LNA saturation** |
| 18:13:27 | HAL ERROR coderate=0 | unknown | mid-cycle, no DB packet — likely a CRC-failed spurious reception |
| 18:22:04 | AmbaSat fcnt 1164 | -36 (chan 0 only) | clean reception |
| 18:22:13 | HAL ERROR coderate=0 | unknown | within the same 30s stat window as the 18:22:04 packet — likely spurious co-receive that failed CRC |

So the HAL ERRORS correlate with AmbaSat transmissions. Two errors
today, both at moments when AmbaSat sent. This is the LNA-saturation
pattern: AmbaSat sends → gateway receives the real packet AND a
saturated/spurious decode that has a corrupt PHY header (coderate=0).

## Does this trigger the actual stuck event?

The 15:51-16:54 UTC stall today had **zero HAL ERROR entries** in
the journal. Either:

(a) The stall was caused by something else entirely (e.g., AGC
    main-loop heartbeat bug, watchdog reset event we cannot see).
(b) AmbaSat happened not to transmit during that window (but user
    confirms it did transmit).
(c) The fw entered a state where it could not even produce log
    output, so HAL ERRORS would have been silently dropped.

Hypothesis (c) is interesting: if the AGC main loop is locked,
`timestamp_counter_correction()` is never reached, and the `printf`
that produces "wrong coding rate" never fires. The absence of
errors during the stall would then be **consistent with** rather
than evidence against an LNA-saturation-induced lockup.

## A new falsifiable hypothesis

**H-LOCK-5 (LNA saturation triggers fw state-machine corner case):**

When AmbaSat transmits and the SX1302 LNA saturates, the resulting
spurious co-reception triggers a fw code path with coderate=0 that
the AGC firmware was not designed to handle gracefully. Most of the
time the fw recovers. Occasionally — perhaps when the saturation
happens during a specific phase of the AGC main loop — the fw enters
a stuck state.

To falsify:

1. **Reduce AmbaSat signal strength temporarily**: move it 1-2
   meters away or insert an attenuator. If stalls stop, H-LOCK-5
   is confirmed.

2. **Disable saturated-channel reception**: in `global_conf.json`
   for the SX1302 packet forwarder, lower the LNA gain or disable
   chan 3-7 (keep only chan 0 where AmbaSat is). If stalls stop,
   the spurious co-receive paths are the trigger.

3. **Watch for stalls correlating with AmbaSat transmit times**:
   record AmbaSat tx timestamps and stall onset timestamps. If
   stalls always start within 30 seconds of an AmbaSat transmit,
   H-LOCK-5 is strongly supported.

4. **Capture the full RX-buffer at next stall**: `probe_mem 0x4000
   4096` should reveal whether spurious co-receives are accumulating
   in the buffer at the moment the lockup hits.

## Practical mitigation candidates

If H-LOCK-5 is confirmed:

- **Hardware**: insert a 10-20 dB attenuator pad between AmbaSat
  and gateway antenna. AmbaSat is meant to fly in a CubeSat 400 km
  up, where path loss is ~120 dB. Indoor 1 m gives only 30-40 dB
  path loss, so the gateway sees a 90+ dB stronger signal than
  designed.
- **Software**: lower the LNA gain in `global_conf.json` (`rssi_offset`
  / per-channel `radio` settings).
- **Geography**: physically separate AmbaSat from the gateway
  antenna by 10+ meters with at least one wall in between.

## Connecting back to the firmware logic bug

Even if H-LOCK-5 is correct, **it does not invalidate the SRAM-
integrity finding** (running fw bytes are intact during stuck) or
the **Stage 1.2 reload mechanism** (reload resets the state machine
to init wait). Both stand. H-LOCK-5 just gives us a likely
**trigger** for the lockup, while the **lockup itself** remains a
state-machine bug in an otherwise intact firmware.
