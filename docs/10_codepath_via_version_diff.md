# Code-path analysis via fw version diff

The `Lora-net/sx1302_hal` repo carries five tagged versions of the AGC
firmware blob. Diffing them against each other reveals which regions
are stable Library code versus regions that change with feature
additions.

## Version stability

| Tag | Released | AGC-fw bytes |
|-----|----------|--------------|
| V1.0.0 | 2019? | 8192 (== V1.0.5 == V1.1.2) |
| V1.0.5 | early v1 patch | 8192 (binary identical to V1.0.0) |
| V1.1.2 | last v1 release | 8192 (binary identical to V1.0.0) |
| V2.0.0 | 2020-12-09 | 8192 (== V2.1.0) |
| V2.1.0 | last release | 8192 (binary identical to V2.0.0) |

So there are **only TWO distinct AGC fw images** in the public history:

- "V1": binary identical across V1.0.0 .. V1.1.2 (full 1.x release line)
- "V2": binary identical across V2.0.0 .. V2.1.0 (current release line)

V1 → V2 is **6145 of 8192 bytes different (75%)**. This is a near-total
rewrite of the AGC firmware, not the "added LBT and configurable PA
delay" the V2.0.0 release notes describe. Whether intended or not,
the AGC firmware shipped in V2.0.0 is essentially a new piece of code.

## Mutable vs immutable byte regions

64-byte block analysis across V1.0.0, V2.0.0, V2.1.0:

| Range | Status | Notes |
|-------|--------|-------|
| `0x0000-0x0fff` (4 KB) | **mutable** | Front of fw, completely rewritten in V2 |
| `0x1000-0x14bf` (1216 B) | **immutable** | But really only 154 B real code + 1062 B `0xbfff` filler |
| `0x14c0-0x1fff` (2880 B) | **mutable** | Completely rewritten in V2 |

Conclusion: virtually no code-bearing region is preserved between V1
and V2. The "stable" region is just padding plus a tiny utility
function near `0x1000-0x1099`.

## Bytes that survived from V1 to V2 unchanged

Two byte sequences appear in BOTH V1 and V2 firmware at different
offsets:

```
V1.0.0 pos 3715: ... 93 00 48 99 00 18 [14] 18 d0 e0 4a ...
V2.1.0 pos 3799: ... 93 00 48 99 00 18 [14] 18 d0 de 0a ...
                     ^^^^^^^^^^^^^^^^^^^^^^^^^ 9-byte common prefix
```

```
V1.0.0 pos 6467: ... c8 a0 fe 84 80 03 [14] 00 cd c9 00 ...
V2.1.0 pos 6379: ... c8 a0 fe 84 80 03 [14] 00 cd c5 00 ...
                     ^^^^^^^^^^^^^^^^^^^^^^^^^ 9-byte common
```

Both sequences contain the byte `0x14` at the same position within the
common pattern. **This means `0x14` here is a literal data operand
of an instruction that has been part of the AGC fw since the first
public release** — not a status-write target value. The two surviving
patterns are likely small library functions: probably a delay loop or
a fixed bit manipulation that gets called by the radio-A and radio-B
init paths.

This is consistent with the earlier finding that the three identified
"AGC_STATUS = imm" opcode patterns (A, B, C with prefix `8a 51`) are
**all V2-exclusive**. The byte pair `8a 51` does not appear ANYWHERE
in V1.0.0. So the entire status-write instruction encoding scheme was
introduced or re-encoded in V2.

## What this tells us about the logic bug

The stuck state we observed today on V2.x is **not** a regression from
V1 — it could only ever have existed in V2 because V1 is a completely
different fw with different state machine.

Whether V1 also had stuck states is unknown to us; nobody we know runs
V1.x in 2026. The community migrated to V2 around 2021.

If the bug were really easy to find (e.g., a wrong status-write value),
the V1 → V2 rewrite would have been the chance to fix it. Either the
bug existed in V1 too and was deliberately not fixed, or it was
introduced fresh in V2. Both options point at it being subtle: probably
a race condition, an unhandled corner case (LNA saturation?), or a
timeout that fires under a specific PHY-layer event sequence.

## ISA still unknown

A second-wave web search for the SX1302 internal MCU ISA across:
- French academic theses (TEL, HAL-INRIA, Grenoble universities)
- Cycleo / Semtech patent filings
- FCC/CE certification documents
- LinkedIn skill listings of Cycleo engineers
- Russian/Chinese reverse-engineering communities

returned no public ISA spec. Best characterization remains "custom
16-bit halfword Cycleo soft-core, undocumented".

The diff-analysis approach above does NOT need the ISA — it works
purely on byte-level invariants. We can extend it:

1. Identify which V2 mutable regions correspond to "LBT support" and
   "configurable PA delay" by aligning V2 against the V2.0.0 release
   notes feature list. The remaining mutable bytes are the unattributed
   rewrite.

2. Differential snapshots of the running V2 SRAM against the loaded V2
   blob during a stuck event will show which bytes (if any) changed at
   runtime — a smoking gun for SRAM corruption (already ruled out in
   `06_sram_integrity_test.md`) or, more interestingly, will tell us
   the exact byte offsets of code that GETS EXECUTED during the stuck
   state but does not advance.

3. State-RAM (`0x6000` region) baseline diffing during a stuck event
   will localize stuck variables to specific offsets. The healthy
   baseline already exists at
   `snapshots/2026-04-29_healthy-baseline/state_0x6000.bin`.

The watchdog forensics now captures all of these (see
`hibbes/lora-dashboard` commit `1cbe7e9`).
