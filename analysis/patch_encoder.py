#!/usr/bin/env python3
"""Encode the 4 patch slots for Strategy A and verify against original ARB blob.

Patches:
  0x04d6: GOTO 0x04d5  →  GOTO 0x0404           (redirect spin to escape handler)
  0x0404: 0xBFFF       →  INCFSZ 0x70, F        (NEW: counter, skip on wrap)
  0x0405: 0xBFFF       →  GOTO   0x04d5         (NEW: loop back to spin)
  0x0406: 0xBFFF       →  GOTO   0x04d7         (NEW: escape to original continue)

PIC16 mid-range opcode encoding (lowest 14 bits):
  GOTO   k:  10_1kkk_kkkk_kkkk        = 0x2800 | k    (k = 11-bit target)
  INCFSZ f,d: 0000_1111_dfff_ffff     = 0x0F00 | (d<<7) | f   (d=1=F)
  BTFSC  f,b: 01_10_bbbf_ffff_fff     = 0x1800 | (b<<7) | f
"""
import struct
import sys


def parity(word14):
    word14 &= 0x3FFF
    pc = bin(word14).count("1")
    return (pc & 1, (pc >> 1) & 1)


def encode_slot(word14):
    p0, p1 = parity(word14)
    return word14 | (p0 << 14) | (p1 << 15)


def goto(target):
    target &= 0x7FF  # 11-bit page-relative
    return 0x2800 | target


def call(target):
    target &= 0x7FF
    return 0x2000 | target


def incfsz(f, dest):
    # dest: 0=W, 1=F
    return 0x0F00 | ((dest & 1) << 7) | (f & 0x7F)


def btfsc(f, b):
    return 0x1800 | ((b & 7) << 7) | (f & 0x7F)


# Build patches
patches = [
    (0x04d6, goto(0x0404), "GOTO 0x0404 (redirect spin to escape handler)"),
    (0x0404, incfsz(0x70, 1), "INCFSZ 0x70, F (counter; skip GOTO on wrap)"),
    (0x0405, goto(0x04d5), "GOTO 0x04d5 (loop back to spin)"),
    (0x0406, goto(0x04d7), "GOTO 0x04d7 (escape to original continue)"),
]

print("=== Strategy A patches for ARB-fw ===\n")

# Verify ORIGINAL ARB content at the 4 addresses
blob = open("/tmp/arb_fw.bin", "rb").read()
print(f"{'addr':>6}  {'orig':>6}  {'patch':>6}  {'orig_p':>6}  {'new_p':>6}  {'comment'}")
print("-" * 90)
for addr, word14, comment in patches:
    orig_slot = struct.unpack_from("<H", blob, addr * 2)[0]
    orig_word = orig_slot & 0x3FFF
    orig_p0 = (orig_slot >> 14) & 1
    orig_p1 = (orig_slot >> 15) & 1

    new_slot = encode_slot(word14)
    new_p0 = (new_slot >> 14) & 1
    new_p1 = (new_slot >> 15) & 1

    print(f"  0x{addr:04x}  0x{orig_word:04x}  0x{word14:04x}  ({orig_p0},{orig_p1})  ({new_p0},{new_p1})  {comment}")

print("\n=== Slot bytes (LE, ready for lgw_mem_wb) ===\n")
print(f"{'addr':>6}  {'orig':>10}  {'new':>10}  {'bytes_to_write':>16}")
print("-" * 70)
for addr, word14, comment in patches:
    orig_slot = struct.unpack_from("<H", blob, addr * 2)[0]
    new_slot = encode_slot(word14)
    if orig_slot == new_slot:
        change = "= IDENTICAL ="
    else:
        new_bytes = struct.pack("<H", new_slot)
        change = f"{new_bytes.hex()}"
    print(f"  0x{addr:04x}  0x{orig_slot:04X}      0x{new_slot:04X}      {change}")

print("\n=== Sanity ===")
# Reverify parity formula on entire blob
bad = 0
for i in range(0, len(blob), 2):
    raw = struct.unpack_from("<H", blob, i)[0]
    word14 = raw & 0x3FFF
    if encode_slot(word14) != raw:
        bad += 1
print(f"ARB blob parity self-check: {bad}/4096 mismatch (should be 0)")

# Also sanity: assert the original 0x04d6 was indeed GOTO 0x04d5
orig_04d6 = struct.unpack_from("<H", blob, 0x04d6 * 2)[0] & 0x3FFF
expected = 0x2800 | 0x04D5  # = 0x2CD5
print(f"Original 0x04d6: 0x{orig_04d6:04x} (expected GOTO 0x04d5 = 0x{expected:04x}): "
      f"{'✓' if orig_04d6 == expected else '✗'}")

# Original 0x0404 should be filler 0xBFFF before patching
orig_0404 = struct.unpack_from("<H", blob, 0x0404 * 2)[0]
print(f"Original 0x0404 slot: 0x{orig_0404:04x} (expected filler 0xBFFF): "
      f"{'✓' if orig_0404 == 0xBFFF else '✗'}")
orig_0405 = struct.unpack_from("<H", blob, 0x0405 * 2)[0]
print(f"Original 0x0405 slot: 0x{orig_0405:04x} (expected filler 0xBFFF): "
      f"{'✓' if orig_0405 == 0xBFFF else '✗'}")
orig_0406 = struct.unpack_from("<H", blob, 0x0406 * 2)[0]
print(f"Original 0x0406 slot: 0x{orig_0406:04x} (expected filler 0xBFFF): "
      f"{'✓' if orig_0406 == 0xBFFF else '✗'}")
