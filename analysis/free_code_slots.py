#!/usr/bin/env python3
"""Find free code-RAM regions (0xBFFF filler runs) for the escape-handler patch."""
import struct
import re

blob = open("/tmp/arb_fw.bin", "rb").read()
slots = []
for i in range(0, len(blob), 2):
    raw = struct.unpack_from("<H", blob, i)[0]
    addr = i // 2
    is_filler = (raw == 0xBFFF)
    slots.append((addr, raw, is_filler))

# Find runs of filler
runs = []
cur_start = None
for addr, raw, is_filler in slots:
    if is_filler:
        if cur_start is None:
            cur_start = addr
    else:
        if cur_start is not None:
            runs.append((cur_start, addr - 1, addr - cur_start))
            cur_start = None
if cur_start is not None:
    runs.append((cur_start, slots[-1][0], slots[-1][0] - cur_start + 1))

print(f"Total filler slots: {sum(1 for _, _, f in slots if f)}/{len(slots)}")
print(f"Filler runs (start, end, length):")
for s, e, ln in sorted(runs, key=lambda r: -r[2])[:10]:
    print(f"  0x{s:04x} - 0x{e:04x}  ({ln} slots)")

# Also find the highest address of non-filler code
last_real = max((addr for addr, raw, is_filler in slots if not is_filler), default=0)
print(f"\nLast non-filler address: 0x{last_real:04x}")
print(f"Code size: {last_real+1}/4096 slots used = {(last_real+1)*100/4096:.1f}%")

# All BSF on STATUS bit 5 or 6 (bank-enter into Bank 1)?
print("\n=== Bank-1 entry detection ===")
disasm_path = "/tmp/arb_disasm.txt"
bsf_status = []
bcf_status = []
status_bit_7 = []
with open(disasm_path) as f:
    for line in f:
        m = re.match(r"^([0-9A-Fa-f]{4}):\s+([0-9A-Fa-f]{4})\s+\d\s+(\S+)\s+(.+)$", line.rstrip())
        if not m:
            continue
        addr = int(m.group(1), 16)
        mnem = m.group(3)
        opnd = m.group(4)
        if mnem in ("BSF", "BCF") and "0x03," in opnd:
            bm = re.search(r"0x03,\s*(\d)", opnd)
            if bm:
                bit = int(bm.group(1))
                if mnem == "BSF" and bit in (5, 6):
                    bsf_status.append((addr, mnem, opnd))
                elif mnem == "BCF" and bit in (5, 6):
                    bcf_status.append((addr, mnem, opnd))
                elif bit == 7:
                    status_bit_7.append((addr, mnem, opnd))

print(f"BSF on STATUS bit 5/6 (= ENTER Bank 1): {len(bsf_status)}")
for a, m, o in bsf_status[:10]:
    print(f"  0x{a:04x}: {m} {o}")
print(f"BCF on STATUS bit 5/6 (= EXIT to Bank 0): {len(bcf_status)} (already showed in earlier output)")
print(f"BCF/BSF on STATUS bit 7 (= IRP for indirect bank): {len(status_bit_7)}")
for a, m, o in status_bit_7[:10]:
    print(f"  0x{a:04x}: {m} {o}")

# Final recommendation
print("\n=== Recommendation ===")
print(f"Best counter file-register candidates (Bank 0, never directly touched):")
print(f"  Primary:    0x70  (high-end GP-RAM, isolated)")
print(f"  Secondary:  0x71-0x7F  (15 backups in same region)")
print(f"  Tertiary:   0x40-0x41  (two-byte free zone if 16-bit counter wanted)")
print()
print(f"Best escape-handler location: end of code-RAM (high filler slots)")
top3 = sorted(runs, key=lambda r: -r[2])[:3]
for s, e, ln in top3:
    if ln >= 3:
        print(f"  0x{s:04x}-0x{e:04x}  ({ln} slots — trivially fits a 3-instruction handler)")
        break
