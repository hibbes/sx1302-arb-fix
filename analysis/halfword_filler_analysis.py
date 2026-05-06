#!/usr/bin/env python3
"""Locate code regions in SX1302 firmware blobs by detecting 0xbfff filler.

The unused-memory filler in SX1302 fw is 0xbfff (16-bit LE). Code regions
are the parts that are NOT mostly filler. This script reports per-1KB-block
filler density.
"""
import struct
import sys


def main():
    paths = sys.argv[1:] or [
        'firmware/agc_fw.bin', 'firmware/arb_fw.bin', 'firmware/cal_fw.bin'
    ]
    print(f"{'file':30s} {'block':5s} {'distinct_bytes':14s} {'filler_pct':10s}  region")
    print("-" * 80)
    for path in paths:
        blob = open(path, 'rb').read()
        for blk in range(len(blob) // 1024):
            seg = blob[blk*1024:(blk+1)*1024]
            distinct = len(set(seg))
            n_filler = sum(
                1 for i in range(0, len(seg)-1, 2)
                if struct.unpack_from('<H', seg, i)[0] == 0xbfff
            )
            pct = 100 * n_filler / (len(seg)//2)
            region = "filler" if pct > 90 else ("CODE" if pct < 30 else "mixed")
            print(f"{path:30s} {blk:5d} {distinct:14d} {pct:9.1f}%  {region}")


if __name__ == "__main__":
    main()
