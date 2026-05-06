#!/usr/bin/env python3
"""Convert PIC16 firmware binary blob to Intel HEX for gpdasm.

Input: little-endian 16-bit halfwords (= PIC16 instructions).
Output: standard Intel HEX with byte-addressed records.
"""
import sys


def emit_record(addr, data):
    n = len(data)
    rec = bytes([n, (addr >> 8) & 0xFF, addr & 0xFF, 0x00]) + data
    chk = (-sum(rec)) & 0xFF
    return ":" + rec.hex().upper() + f"{chk:02X}"


def main():
    if len(sys.argv) < 2:
        print("usage: bin2hex.py <fw.bin>", file=sys.stderr)
        sys.exit(1)
    blob = open(sys.argv[1], "rb").read()
    addr = 0
    while addr < len(blob):
        chunk = blob[addr:addr + 16]
        print(emit_record(addr, chunk))
        addr += 16
    print(":00000001FF")


if __name__ == "__main__":
    main()
