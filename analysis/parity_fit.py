#!/usr/bin/env python3
"""PIC16 parity algorithm reverse-engineering — Phase A (offline).

Layout per pic16_disasm.py:
  Each 16-bit slot = bits[13:0] instruction, bits[15:14] parity (P1, P0).
  Two blobs: agc_fw_sx1250.bin, arb_fw.bin, 4096 slots each.

Hypothesis 1: Parity is a linear function of the 14 instruction bits over GF(2).
  P0 = sum_i (a0_i * b_i) + c0   (mod 2)
  P1 = sum_i (a1_i * b_i) + c1   (mod 2)
  Coefficients a0_i, a1_i ∈ {0,1}, constants c0, c1 ∈ {0,1}.

Method: Gaussian elimination over GF(2) on a 4096 x 15 matrix
  (14 instruction bits + 1 constant column) targeting each parity bit.

If the linear hypothesis fits 100% of valid instructions in BOTH blobs:
  → algorithm cracked, no live-tests needed.

If not: report residuals (how many instructions disagree with best linear fit),
  fall back to nonlinear analysis or live-tests.
"""
import struct
import sys


def load_blob(path):
    """Return list of (word14, P0, P1) tuples."""
    blob = open(path, "rb").read()
    if len(blob) % 2 != 0:
        raise ValueError(f"{path}: odd byte count")
    out = []
    for i in range(0, len(blob), 2):
        raw = struct.unpack_from("<H", blob, i)[0]
        word14 = raw & 0x3FFF
        p0 = (raw >> 14) & 1
        p1 = (raw >> 15) & 1
        out.append((word14, p0, p1))
    return out


def gf2_solve(rows, target_idx):
    """Gaussian elimination over GF(2).

    rows: list of (input_bits_int, target_bits_tuple)
    target_idx: which target bit to solve for (0 or 1 etc.)
    Returns: dict {basis_size, residuals (count of unmatched), coeffs (list of bit-positions whose XOR == target)}.
    """
    nvars = 15  # 14 instruction bits + 1 constant
    # Build augmented matrix: each row is 16-bit int (15 nvars + 1 target)
    matrix = []
    for inp, tgts in rows:
        # Augmented row: bit i (i<14) = b_i; bit 14 = 1 (constant); bit 15 = target
        aug = inp & 0x3FFF
        aug |= (1 << 14)  # constant column
        aug |= (tgts[target_idx] & 1) << 15
        matrix.append(aug)

    # Gaussian elimination on first 15 columns
    pivot_rows = []
    used_cols = []
    for col in range(15):
        pivot = -1
        for r_idx in range(len(matrix)):
            if r_idx in pivot_rows:
                continue
            if (matrix[r_idx] >> col) & 1:
                pivot = r_idx
                break
        if pivot == -1:
            continue
        pivot_rows.append(pivot)
        used_cols.append(col)
        for r_idx in range(len(matrix)):
            if r_idx != pivot and ((matrix[r_idx] >> col) & 1):
                matrix[r_idx] ^= matrix[pivot]

    # Now: rank = len(pivot_rows). Coefficients?
    # Each row in pivot_rows has exactly one '1' in cols 0..14 + maybe '1' in col 15.
    # The coefficient for col c is determined by: which pivot row has its 1 in col c?
    # If a pivot row has its '1' in col c and target bit '1' → coeff[c] = 1.
    # If col c had no pivot → coeff[c] is a free variable; for minimal solution, set 0.
    coeffs = [0] * 15
    for c, p in zip(used_cols, pivot_rows):
        if (matrix[p] >> 15) & 1:
            coeffs[c] = 1

    # Verify against all rows
    residuals = 0
    for inp, tgts in rows:
        # Compute predicted parity
        pred = 0
        for c in range(14):
            if coeffs[c] and ((inp >> c) & 1):
                pred ^= 1
        pred ^= coeffs[14]  # constant
        if pred != tgts[target_idx]:
            residuals += 1

    return {
        "rank": len(pivot_rows),
        "residuals": residuals,
        "coeffs": coeffs,
        "active_input_bits": [c for c in range(14) if coeffs[c]],
        "constant": coeffs[14],
    }


def analyze(path):
    print(f"\n{'='*70}\n{path}\n{'='*70}")
    blob = load_blob(path)
    print(f"slots: {len(blob)}")

    # Sanity: distinct word14 with different parity → ambiguous (rules out pure function)
    pmap = {}
    ambiguous = 0
    for w, p0, p1 in blob:
        key = w
        val = (p0, p1)
        if key in pmap and pmap[key] != val:
            ambiguous += 1
        pmap[key] = val
    print(f"distinct word14: {len(pmap)}; word14 with conflicting parity: {ambiguous}")
    if ambiguous > 0:
        print("  → parity is NOT a pure function of word14 — depends on address or other state")
    else:
        print("  → parity IS a pure function of word14 (good)")

    # Distribution of (P0, P1)
    from collections import Counter
    pdist = Counter((p0, p1) for _, p0, p1 in blob)
    print(f"parity distribution: {dict(pdist)}")

    # Filler check
    filler = struct.pack("<H", 0xBFFF)  # known filler from disasm repo notes
    raw = open(path, "rb").read()
    n_filler = sum(1 for i in range(0, len(raw), 2) if raw[i:i+2] == filler)
    print(f"0xBFFF filler slots: {n_filler}/{len(blob)}")

    # Restrict to non-filler for the fit
    real = [(w, p0, p1) for w, p0, p1 in blob if (w | 0x8000) != 0xBFFF or (p0, p1) != (1, 0)]
    print(f"non-filler instructions: {len(real)}")

    rows = [(w, (p0, p1)) for w, p0, p1 in real]

    print("\n--- Linear fit P0 ---")
    r0 = gf2_solve(rows, 0)
    print(f"  active input bits: {r0['active_input_bits']}, constant={r0['constant']}")
    print(f"  rank={r0['rank']}, residuals={r0['residuals']}/{len(rows)}")

    print("\n--- Linear fit P1 ---")
    r1 = gf2_solve(rows, 1)
    print(f"  active input bits: {r1['active_input_bits']}, constant={r1['constant']}")
    print(f"  rank={r1['rank']}, residuals={r1['residuals']}/{len(rows)}")

    return r0, r1, rows


def cross_validate(rows_a, r0_a, r1_a, rows_b, label_b):
    """Apply coefficients from blob A to blob B."""
    print(f"\n--- Cross-validate A→{label_b} ---")
    for which, r in [("P0", r0_a), ("P1", r1_a)]:
        bad = 0
        for w, tgts in rows_b:
            pred = 0
            for c in range(14):
                if r["coeffs"][c] and ((w >> c) & 1):
                    pred ^= 1
            pred ^= r["coeffs"][14]
            if pred != tgts[0 if which == "P0" else 1]:
                bad += 1
        print(f"  {which}: {bad}/{len(rows_b)} disagree")


if __name__ == "__main__":
    agc = "/tmp/agc_fw.bin"
    arb = "/tmp/arb_fw.bin"
    r0_a, r1_a, rows_a = analyze(agc)
    r0_b, r1_b, rows_b = analyze(arb)

    rows_b_tuples = [(w, (p0, p1)) for w, p0, p1 in load_blob(arb)
                     if (w | 0x8000) != 0xBFFF or (p0, p1) != (1, 0)]
    cross_validate(rows_a, r0_a, r1_a, rows_b_tuples, "arb")

    rows_a_tuples = [(w, (p0, p1)) for w, p0, p1 in load_blob(agc)
                     if (w | 0x8000) != 0xBFFF or (p0, p1) != (1, 0)]
    cross_validate(rows_b, r0_b, r1_b, rows_a_tuples, "agc")
