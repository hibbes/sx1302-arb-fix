#!/usr/bin/env python3
"""Find AGC_STATUS write opcode patterns in agc_fw_sx1250.bin.

Usage:
    python3 find_status_patterns.py firmware/agc_fw.bin

Looks for byte sandwiches where bytes [0..3] and [5..6] are constant and
byte [4] varies across multiple known status values (0x01..0x0F). Such
sandwiches are likely "STORE imm → AGC_STATUS register" instructions.
"""
import sys
from collections import defaultdict


def main():
    if len(sys.argv) < 2:
        print("usage: find_status_patterns.py <fw.bin>", file=sys.stderr)
        sys.exit(1)
    blob = open(sys.argv[1], 'rb').read()

    status_vals = set(range(0x01, 0x10))
    W = 7
    groups = defaultdict(list)
    for i in range(len(blob) - W):
        win = blob[i:i+W]
        key = (win[:4], win[5:7])
        groups[key].append((i+4, win[4]))

    print(f"AGC fw size: {len(blob)} bytes")
    print(f"\nLooking for 7-byte sandwich patterns covering ≥3 status values 0x01..0x0F:\n")
    found = []
    for key, hits in groups.items():
        imms = set(v for _, v in hits)
        shared = imms & status_vals
        if len(shared) >= 3:
            found.append((len(shared), key, hits, imms))
    for n_shared, key, hits, imms in sorted(found, reverse=True):
        b_hex = " ".join(f"{x:02x}" for x in key[0])
        a_hex = " ".join(f"{x:02x}" for x in key[1])
        print(f"  {n_shared} status values: {b_hex}  ?? {a_hex}")
        print(f"    distinct imms: {sorted(hex(v) for v in imms)}")
        for pos, val in hits[:8]:
            print(f"      pos={pos:5d}  imm=0x{val:02X}")
        print()

    # Special: look for 0x14 as well
    print("\n--- 0x14 contexts (5 occurrences expected) ---")
    for p in [i for i, b in enumerate(blob) if b == 0x14]:
        lo, hi = max(0, p-8), min(len(blob), p+9)
        win = " ".join(f"{x:02x}" for x in blob[lo:hi])
        print(f"  pos {p:5d}: {win}")


if __name__ == "__main__":
    main()
