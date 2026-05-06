#!/usr/bin/env python3
"""Identify free file-registers in ARB-fw for the Strategy A counter.

Method:
1. Parse the disassembly output of analysis/pic16_disasm.py.
2. Extract all file-register addresses referenced as direct operands in
   byte- and bit-oriented operations.
3. Detect bank-select side effects: in PIC16 mid-range, file addresses
   are 7 bits in the instruction; bank 0 vs 1 depends on RP0 in STATUS
   (file 0x03 bit 5). The disassembler shows 7-bit addr; the actual
   bank depends on context. For freedom, we want a register that is
   NEVER written (and ideally never read) in EITHER bank.
4. Also detect FSR-based indirect access via file 0x00 (INDF). When
   INDF is touched, ANY register might be the target; we need a more
   conservative analysis.
5. Build a heat-map: for each 7-bit file address, count direct hits.
6. Output candidate "always-zero" addresses, sorted by safety.

Caveats logged:
- INDF writes (file 0x00) can target arbitrary banks via FSR; if any
  exist, mark candidates with low confidence.
- BCF/BSF on STATUS bit 5/6 (RP0/RP1) toggle banks; we'd need full
  flow-analysis to perfectly track which bank each file-op is in.
  Mid-range with only Bank 0+1 is the common case; if STATUS bit 5 is
  set somewhere and not cleared before a file-op, things go to bank 1.
"""
import re
import sys
from collections import defaultdict


# Operations that take a file-register as direct operand.
# (mnemonic, operand_role)  role: 'rw' = read+write, 'r' = read, 'w' = write,
# 'bit_rw' = bit toggle (RW), 'bit_r' = bit read.
FILE_OPS = {
    "MOVWF":  "w",
    "CLRF":   "w",
    "MOVF":   "r",   # MOVF f,W is read; MOVF f,F is read+write (test for zero)
    "SUBWF":  "rw",
    "DECF":   "rw",
    "IORWF":  "rw",
    "ANDWF":  "rw",
    "XORWF":  "rw",
    "ADDWF":  "rw",
    "COMF":   "rw",
    "INCF":   "rw",
    "DECFSZ": "rw",
    "RRF":    "rw",
    "RLF":    "rw",
    "SWAPF":  "rw",
    "INCFSZ": "rw",
    "BCF":    "bit_rw",
    "BSF":    "bit_rw",
    "BTFSC":  "bit_r",
    "BTFSS":  "bit_r",
}


def parse_disasm(path):
    """Yield (word_addr, raw, parity, mnemonic, operand_string) tuples."""
    line_re = re.compile(
        r"^([0-9A-Fa-f]{4}):\s+([0-9A-Fa-f]{4})\s+(\d)\s+(\S+)(.*)$"
    )
    with open(path) as f:
        for line in f:
            m = line_re.match(line.rstrip())
            if not m:
                continue
            yield (
                int(m.group(1), 16),
                int(m.group(2), 16),
                int(m.group(3)),
                m.group(4),
                m.group(5).strip(),
            )


def extract_file_addr(operand):
    """Try to find a '0xNN' file-register operand in the operand string."""
    m = re.search(r"0x([0-9A-Fa-f]{2,4})", operand)
    if not m:
        return None
    return int(m.group(1), 16)


def main():
    if len(sys.argv) < 2:
        print("usage: free_reg_finder.py <disasm.txt>", file=sys.stderr)
        sys.exit(1)

    direct_use = defaultdict(lambda: {"r": 0, "w": 0, "bit_r": 0, "bit_rw": 0})
    indf_writes = []
    indf_reads = []
    fsr_touches = []
    status_bit_writes = []  # BCF/BSF on STATUS bit 5/6 = bank-select

    total_insn = 0
    for addr, raw, parity, mnem, operand in parse_disasm(sys.argv[1]):
        total_insn += 1
        if mnem not in FILE_OPS:
            continue

        operand_addr = extract_file_addr(operand)
        if operand_addr is None:
            continue
        # The file-address is in the low 7 bits (mid-range PIC16); higher
        # bits would be from the disassembler showing the constant for
        # MOVLW etc. — but those aren't in FILE_OPS.
        f = operand_addr & 0x7F

        role = FILE_OPS[mnem]
        if role == "r":
            direct_use[f]["r"] += 1
        elif role == "w":
            direct_use[f]["w"] += 1
        elif role == "rw":
            direct_use[f]["r"] += 1
            direct_use[f]["w"] += 1
        elif role == "bit_r":
            direct_use[f]["bit_r"] += 1
        elif role == "bit_rw":
            direct_use[f]["bit_rw"] += 1

        # Indirect (INDF / FSR) detection
        if f == 0x00:
            if role in ("w", "rw", "bit_rw"):
                indf_writes.append((addr, mnem, operand))
            else:
                indf_reads.append((addr, mnem, operand))
        if f == 0x04:  # FSR
            if role in ("w", "rw"):
                fsr_touches.append((addr, mnem, operand))
        if f == 0x03:  # STATUS
            # Check if it's a BCF/BSF on bit 5 or 6 (bank-select)
            m = re.search(r"0x03,\s*(\d)", operand)
            if m and m.group(1) in ("5", "6"):
                if mnem in ("BCF", "BSF"):
                    status_bit_writes.append((addr, mnem, operand))

    print(f"\nTotal disassembled instructions: {total_insn}")
    print(f"Direct file-register references: {sum(sum(v.values()) for v in direct_use.values())}")
    print(f"INDF writes (FSR-indirect, target arbitrary): {len(indf_writes)}")
    print(f"INDF reads: {len(indf_reads)}")
    print(f"FSR (file 0x04) writes: {len(fsr_touches)}")
    print(f"STATUS-bank-bit (5/6) BCF/BSF: {len(status_bit_writes)}")

    if status_bit_writes:
        print("\n  STATUS bank-select touches (first 10):")
        for a, m, o in status_bit_writes[:10]:
            print(f"    0x{a:04x}: {m} {o}")

    if fsr_touches:
        print("\n  FSR writes (first 10) — these define INDF-targets:")
        for a, m, o in fsr_touches[:10]:
            print(f"    0x{a:04x}: {m} {o}")

    # FSR target hint: collect MOVLW values immediately followed by MOVWF FSR.
    # (Won't catch all cases — could be dynamic — but catches static patterns.)
    movlw_to_fsr = []
    prev = None
    for entry in parse_disasm(sys.argv[1]):
        if prev is not None:
            paddr, _, _, pmnem, popnd = prev
            addr, _, _, mnem, opnd = entry
            if pmnem == "MOVLW" and mnem == "MOVWF" and "0x04" in opnd:
                m = re.search(r"0x([0-9A-Fa-f]{2,4})", popnd)
                if m:
                    movlw_to_fsr.append((paddr, int(m.group(1), 16)))
        prev = entry
    if movlw_to_fsr:
        seen_fsr_vals = sorted(set(v for _, v in movlw_to_fsr))
        print(f"\n  Static MOVLW->MOVWF FSR pairs: {len(movlw_to_fsr)}; distinct FSR values: {len(seen_fsr_vals)}")
        # FSR points to a target byte (potentially in bank 1 if IRP set,
        # but most often bank 0). Mark these as "INDF-targeted" addresses.
        print(f"  → these addresses might be touched indirectly:")
        print(f"     {[hex(v) for v in seen_fsr_vals[:30]]}{' ...' if len(seen_fsr_vals)>30 else ''}")

    # Heat-map of all 128 7-bit file-addresses
    print("\n=== Direct-reference heat-map (Bank 0 view, 7-bit address) ===")
    print("  addr  reads  writes  bit_r  bit_rw  status")
    candidates = []
    static_indf_targets = set(v & 0x7F for _, v in movlw_to_fsr)
    for f in range(0x80):
        u = direct_use[f]
        total = u["r"] + u["w"] + u["bit_r"] + u["bit_rw"]
        if total == 0:
            note = "FREE-direct"
            if f in static_indf_targets:
                note += "_BUT_INDF_HIT"
            candidates.append((f, note))
            print(f"  0x{f:02x}  {u['r']:5d}  {u['w']:6d}  {u['bit_r']:5d}  {u['bit_rw']:6d}  {note}")
        else:
            # Print non-zero only if interesting (for compactness)
            if f < 0x10 or u["w"] == 0:  # SFRs or read-only references
                pass

    print(f"\n=== Free-direct candidates ({len(candidates)} addresses) ===")
    for f, note in candidates:
        print(f"  0x{f:02x}  {note}")

    # Best candidates: free-direct AND not in static_indf_targets
    safe = [f for f, note in candidates if f not in static_indf_targets]
    print(f"\n=== STRONG candidates (free-direct + no INDF-static-hit): {len(safe)} ===")
    if safe:
        for f in safe:
            print(f"  0x{f:02x}")
    else:
        print("  (none — every free-direct slot is in static-INDF target set)")

    # Banks: in mid-range PIC16, bank 0 (0x00-0x7F) and bank 1 (0x80-0xFF).
    # Custom Cycleo SFRs may be at higher addresses; if no STATUS bit-5
    # writes, code stays in bank 0 only → addresses 0x80-0xFF unused → free.
    print("\n=== Bank analysis ===")
    if not status_bit_writes:
        print("  No BCF/BSF on STATUS bit 5/6 → ARB stays in Bank 0 only.")
        print("  → Bank 1 (0x80-0xFF) is fully unused for direct file-ops.")
        print("    But indirect access via FSR with IRP=1 (STATUS bit 7) may still hit.")


if __name__ == "__main__":
    main()
