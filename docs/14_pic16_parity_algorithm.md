# PIC16 Code-RAM Parity Algorithm — CRACKED (offline, 2026-05-02)

## Result

```
P0 =  popcount(word14)       & 1
P1 = (popcount(word14) >> 1) & 1
```

The two parity bits at slot positions [15:14] are the lower 2 bits of the
Hamming weight (popcount) of the 14-bit instruction word at [13:0]:

```
slot[15:14] = popcount(slot[13:0]) mod 4
```

**Verified zero residuals across 8192 slots** (4096 AGC + 4096 ARB). No
live-MCU tests needed.

## Method

Phase A, fully offline:

1. Layout per `analysis/pic16_disasm.py` line 159-161: each slot is a
   little-endian 16-bit halfword; bits [13:0] = instruction, bits [15:14] =
   parity.
2. Hypothesis 1: P0, P1 are linear functions of the 14 instruction bits
   over GF(2). Tested with Gaussian elimination on a 4096×15 matrix.
   - **P0**: rank 15, residuals 0/4096, coefficients = all 14 input bits
     (XOR over all bits). Linear-fit confirmed: `P0 = b0 ⊕ b1 ⊕ ... ⊕ b13`.
     Cross-validation AGC→ARB and ARB→AGC: 0/4096 disagree.
   - **P1**: linear fit fails, residuals 1468/4096 (AGC), 541/4096 (ARB).
     P1 is NOT linear in the instruction bits.
3. Hypothesis 2: P1 depends on (instruction, address) linearly. Tested
   with extended 27-variable matrix. Still fails: 2028/4096 (AGC),
   541/4096 (ARB) residuals.
4. Hypothesis 3: P1 depends on opcode class. Tested per-class (4 classes
   by top 2 bits) linear fits. Fails at ~30-50% residuals per class.
5. Hypothesis 4: P1 has a closed-form non-linear expression in the bits.
   Tested several candidates:
   - `P1 == bit_k` for any single bit k → all very poor fit
   - `P1 == parity(low byte)` → bad ~1500
   - `P1 == parity(even-positions)` → bad ~1300
   - **`P1 == (popcount(word14) >> 1) & 1`** → **bad = 0/4096 in both blobs**

P1 is the second-lowest bit of the popcount. (P1, P0) together form
`popcount(word14) mod 4`. This is a **quadratic** function over GF(2):

```
P1 = ⊕_{i<j} (b_i ∧ b_j)
```

The complete homogeneous symmetric polynomial of degree 2.

## Why this is the right algorithm

This is a textbook 2-bit parity / weight encoding. It detects:
- All single-bit errors (P0 always flips)
- All odd-count multi-bit errors (P0 flips)
- Many even-count errors (P1 flips when popcount jumps over a 2-boundary)

The encoding has the desirable property that the parity field is the
arithmetic count itself (mod 4), making it cheap to implement in
silicon: a popcount tree with two output bits.

It is NOT Hamming(14,2) (which would correct single bit errors) — this
encoding is detect-only, consistent with what we observe at runtime
(SX1302 raises PARITY_ERROR but doesn't auto-correct).

## Verification routine

```python
import struct

def parity(word14):
    word14 &= 0x3FFF
    pc = bin(word14).count("1")
    return (pc & 1, (pc >> 1) & 1)  # (P0, P1)

def encode_slot(word14):
    p0, p1 = parity(word14)
    return word14 | (p0 << 14) | (p1 << 15)

# Sanity-check both blobs:
for path in ("agc_fw_sx1250.bin", "arb_fw.bin"):
    bad = 0
    blob = open(f"firmware/{path}", "rb").read()
    for i in range(0, len(blob), 2):
        raw = struct.unpack_from("<H", blob, i)[0]
        word14 = raw & 0x3FFF
        if encode_slot(word14) != raw:
            bad += 1
    print(f"{path}: {bad}/4096 mismatch")
```

Expected output: `0/4096 mismatch` for both files.

## Implications for ARB-fw Patch

Now that we can compute correct (P0, P1) for any 14-bit instruction, we can:

1. **Construct any new instruction word with parity-correct slot encoding**
   (`encode_slot(new_instr)`).
2. **Apply the patch via `lgw_mem_wb`** without tripping the runtime
   PARITY-Check. The HW reads the slot, recomputes popcount, compares
   with our encoded P0, P1; both match → PARITY_ERROR stays 0.
3. **No need for Phase B live-tests.** The algorithm is deterministic and
   fully reverse-engineered from the existing valid blobs.

## Patch encoding example

Strategy A (escape via counter increment):

Original ARB code at word 0x04d5:
```
04d5: btfsc 0x0e, 0   = 0x180e   (raw slot bytes, parity-correct)
04d6: goto  0x04d5    = 0x28d5   (raw slot bytes, parity-correct)
```

Proposed patch — replace BTFSC with INCFSZ on a free file-register:
```
04d5: incfsz <FREE>, F   ; opcode 0x0F00 | (1<<7) | (FREE<<0)
                         ; = 0x0F80 | FREE   (assuming d=1=destination=F)
```

`encode_slot(0x0F80 | FREE)` gives the correct 16-bit slot bytes to write
back via `lgw_mem_wb`. After the patch, popcount(0x0F80 | FREE) determines
the new parity bits; HW will validate them on the next read; PARITY=0
stays.

## Open items (not blocking)

- Free file-register identification (vorabeit 2). Needed before patch
  encoding can be finalized.
- Channelizer-Reset-Bit-Adresse aus PIC16-Sicht (vorarbeit 3): does the
  ARB MCU even have direct access to the bit that we toggle externally
  via `modem_cycle`? If not, Strategy A's escape from the busy-spin needs
  a different mechanism.
- Test-deploy on real Pi. After encoding is correct: write modified slot,
  read back, confirm slot bytes match expectation, confirm PARITY=0,
  confirm chip continues operating.

## Status

- [VERIFIED] P0 = popcount(word14) & 1
- [VERIFIED] P1 = (popcount(word14) >> 1) & 1
- [VERIFIED] Both formulas zero-residual across 8192 slots in 2 independent blobs
- Phase A complete, no live-MCU tests needed
- Phase B (live tests) cancelled as unnecessary
