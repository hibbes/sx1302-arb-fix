# ISA research: what is the AGC/ARB MCU?

Independent web research (2026-04-29 PM) covering GitHub, Semtech docs,
LoRa community forums, conference archives, university theses, patent
databases, and academic indexes returned **zero public reverse-
engineering work** on the SX1302/SX1308 internal MCUs.

## Summary

**The ISA is publicly unknown.** Best characterization from empirical
fingerprints + Cycleo lineage: **custom 16-bit Semtech RISC**,
undocumented, no public toolchain.

## Evidence for "16-bit halfword machine"

The unused-memory filler is `ff bf` = `0xbfff` little-endian. This
holds across all three firmware blobs.

ARB firmware byte-distinctness per 1 KB block:

| Block | Distinct bytes | 0xbfff fill | Interpretation |
|-------|----------------|-------------|----------------|
| 0 | 32 | 92.8% | mostly filler with a small init |
| 1 | 2 | 100.0% | pure filler |
| 2 | 161 | 1.4% | **code** |
| 3 | 159 | 0.0% | **code** |
| 4 | 2 | 100.0% | pure filler |
| 5 | 2 | 100.0% | pure filler |
| 6 | 2 | 100.0% | pure filler |
| 7 | 2 | 100.0% | pure filler |

ARB code region: roughly bytes `2048..4095` (2 KB), the rest is
`0xbfff`. This is impossible if the byte-stream were 8-bit instructions
(filler would be `0xff` or `0x00`). It is **strong evidence the MCU
fetches 16-bit halfwords and `0xbfff` is its NOP/undefined-instruction
filler value**.

AGC firmware has 13.6% `0xbfff` halfwords distributed throughout —
much denser code than ARB, which makes sense since AGC handles the
runtime gain control and ARB just packet-arbitrates between channels.

CAL firmware has 24.5% `0xbfff` halfwords with one fully-filler block
(block 4).

## Evidence against common ISAs

| ISA | Test | Result |
|-----|------|--------|
| 8051 | RET (0x22) count in 8 KB | 10 (orders of magnitude too low) |
| ARM Thumb | filler `0xbfff` | undefined opcode in Thumb encoding |
| AVR | fill pattern + opcode histogram | no match |
| PIC | fill pattern + opcode histogram | no match |
| MSP430 | fill pattern `0x3fff` instead | no match (filler is `0xbfff`) |
| Cortex-M0 | top-byte histogram | no match for typical M0 distribution |

## Why "Cycleo custom RISC" is the leading hypothesis

- Semtech acquired Cycleo (Grenoble/Meylan) in May 2012 specifically
  for the LoRa baseband design.
- SX1301 (2017 generation predecessor) already had the same dual-MCU
  split called "Radio AGC MCU" + "Packet arbiter MCU" with closed
  firmware. This is a multi-generation in-house architecture, not a
  one-off experiment.
- Datasheets for both SX1301 and SX1302 are silent on internal MCU
  ISA, JTAG/SWD pads, debug interface — all access goes through host
  SPI memory windows (`lgw_mem_wb`/`rb`).
- Olivier Seller (Cycleo co-founder, still at Semtech Meylan) is the
  most likely human source.

## Sources

Primary references found:

- [Lora-net/sx1302_hal](https://github.com/Lora-net/sx1302_hal) — exposes only `AGC_MEM_ADDR=0x0000`, `ARB_MEM_ADDR=0x2000`, `MCU_FW_SIZE=8192`. No ISA hints.
- [SX1302 Datasheet rev 1.1 (Jan 2020)](https://media.digikey.com/pdf/Data%20Sheets/Semtech%20PDFs/SX1302_rev1.1_Jan2020.pdf) — host-side only.
- [SX1301 Datasheet v2.4 (Jun 2017)](https://www.mouser.com/datasheet/2/761/sx1301-1523429.pdf) — predecessor with same split.
- [Matt Knight, Reversing LoRa (2016)](https://static1.squarespace.com/static/54cecce7e4b054df1848b5f9/t/57489e6e07eaa0105215dc6c/1464376943218/Reversing-Lora-Knight.pdf) — most prominent LoRa reverse-engineering work, but covers PHY/chirp modulation only. No SX130x baseband.
- [Inovallée: Semtech / Cycleo Grenoble background](https://www.inovallee.com/en/semtech-le-geant-de-la-technologie-lora-choisit-inovallee-pour-revolutionner-le-monde-de-liot/)
- [airbus-seclab/cpu_rec](https://github.com/airbus-seclab/cpu_rec) — ISA classifier, suggested next-step tool.

Searches that returned zero results:

- GitHub: "sx1302 firmware", "sx1308 firmware disassembly", "agc_fw_sx1250 disassembly"
- Conference talks: CCC, 36c3, DEF CON, Hardwear.io, recon.cx — only
  Knight's PHY-level work
- University theses: TU Berlin, Imperial, EPFL, ETH — only PHY work
- Die-shot databases: Zeptobars, Chipworks — no SX1302 die shot
- Patent search for Semtech AGC/ARB MCU core — no hits identifying
  the ISA

## Suggested next steps

1. **Run cpu_rec on the blobs** to N-gram-classify against ~70 known ISAs.
   A negative result on all known ISAs is itself evidence for "custom".
   ```bash
   git clone https://github.com/airbus-seclab/cpu_rec
   python3 cpu_rec/cpu_rec.py firmware/agc_fw.bin
   python3 cpu_rec/cpu_rec.py firmware/arb_fw.bin
   python3 cpu_rec/cpu_rec.py firmware/cal_fw.bin
   ```

2. **Use the SPI memory window as a debugger.** HAL exposes
   `lgw_mem_wb`/`lgw_mem_rb` over the AGC_MEM region. Halt the MCU
   (force-host-ctrl), patch one halfword to a known marker, observe
   whether AGC_STATUS still progresses. Empirically maps fetch
   granularity (16 vs 32 bit) and may help localize PC after halt.

3. **Diff-analyze across HAL versions.** `git log --follow -- libloragw/src/agc_fw_sx1250.var`
   in the upstream repo: a semantic version bump usually changes only a
   few hundred bytes, localizing function boundaries.

4. **Contact Olivier Seller or sx1302_hal maintainers via TTN forum.**
   A polite issue asking "Is there public documentation of the AGC/ARB
   MCU ISA?" produces a useful "proprietary, not planned" answer if
   nothing else.

## What this means for `AGC_STATUS = 0x14`

Without the ISA we cannot disassemble. But:

- The bit-OR-accumulation hypothesis (`0x14 = 0x10 | 0x04`) for status
  bits is consistent with how a 16-bit RISC typically does bit
  manipulation on memory-mapped registers (separate `BIS` or
  `OR.B` instructions for each bit, not a `MOV` of a composite
  immediate).
- The empirical observation that 0x14 does not appear in the same
  immediate-MOV opcode patterns as 0x01..0x0A is therefore consistent
  with the "this is a runtime accumulated state, not a written
  constant" reading.
