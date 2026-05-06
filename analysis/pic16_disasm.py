#!/usr/bin/env python3
"""PIC16 mid-range (classic, non-enhanced) disassembler.

Reference: Microchip PIC16F84/PIC16F628 instruction set, the classic
14-bit-instruction-word mid-range core. Validated against gpdasm output.

Encoding notation:
  bit 13 = MSB, bit 0 = LSB
  d = destination select (0 = W, 1 = f)
  f = file register address (7 bits)
  b = bit number in byte (3 bits)
  k = literal (8 or 11 bits)

This implementation refuses to guess. Unknown opcodes emit DW (data word).
"""
import struct
import sys


# Classic mid-range opcode table. Each entry: (mask, value, mnemonic, fmt).
# fmt is a python format string with available fields {d,f,b,k,addr}.
OPCODES = [
    # Byte-oriented file register operations (00 ...)
    (0xFF80, 0x0080, "MOVWF",  "MOVWF   0x{f:02X}"),
    (0xFF80, 0x0000, "NOP_OR_MISC", None),  # handled specially below
    (0xFF80, 0x0100, "CLRF/CLRW", None),    # handled specially below
    (0xFF00, 0x0200, "SUBWF",  "SUBWF   0x{f:02X}, {d_str}"),
    (0xFF00, 0x0300, "DECF",   "DECF    0x{f:02X}, {d_str}"),
    (0xFF00, 0x0400, "IORWF",  "IORWF   0x{f:02X}, {d_str}"),
    (0xFF00, 0x0500, "ANDWF",  "ANDWF   0x{f:02X}, {d_str}"),
    (0xFF00, 0x0600, "XORWF",  "XORWF   0x{f:02X}, {d_str}"),
    (0xFF00, 0x0700, "ADDWF",  "ADDWF   0x{f:02X}, {d_str}"),
    (0xFF00, 0x0800, "MOVF",   "MOVF    0x{f:02X}, {d_str}"),
    (0xFF00, 0x0900, "COMF",   "COMF    0x{f:02X}, {d_str}"),
    (0xFF00, 0x0A00, "INCF",   "INCF    0x{f:02X}, {d_str}"),
    (0xFF00, 0x0B00, "DECFSZ", "DECFSZ  0x{f:02X}, {d_str}"),
    (0xFF00, 0x0C00, "RRF",    "RRF     0x{f:02X}, {d_str}"),
    (0xFF00, 0x0D00, "RLF",    "RLF     0x{f:02X}, {d_str}"),
    (0xFF00, 0x0E00, "SWAPF",  "SWAPF   0x{f:02X}, {d_str}"),
    (0xFF00, 0x0F00, "INCFSZ", "INCFSZ  0x{f:02X}, {d_str}"),
    # Bit-oriented file register operations (01 bbbb ffffffff)
    (0x3C00, 0x1000, "BCF",    "BCF     0x{f:02X}, {b}"),
    (0x3C00, 0x1400, "BSF",    "BSF     0x{f:02X}, {b}"),
    (0x3C00, 0x1800, "BTFSC",  "BTFSC   0x{f:02X}, {b}"),
    (0x3C00, 0x1C00, "BTFSS",  "BTFSS   0x{f:02X}, {b}"),
    # Literal and control (10 = call/goto, 11 = literal)
    (0x3800, 0x2000, "CALL",   "CALL    0x{k:03X}"),
    (0x3800, 0x2800, "GOTO",   "GOTO    0x{k:03X}"),
    # Literal ops (11 ...)
    (0x3C00, 0x3000, "MOVLW",  "MOVLW   0x{k:02X}"),
    (0x3C00, 0x3400, "RETLW",  "RETLW   0x{k:02X}"),
    (0x3F00, 0x3800, "IORLW",  "IORLW   0x{k:02X}"),
    (0x3F00, 0x3900, "ANDLW",  "ANDLW   0x{k:02X}"),
    (0x3F00, 0x3A00, "XORLW",  "XORLW   0x{k:02X}"),
    (0x3E00, 0x3C00, "SUBLW",  "SUBLW   0x{k:02X}"),
    (0x3E00, 0x3E00, "ADDLW",  "ADDLW   0x{k:02X}"),
]


def decode(word, addr=0):
    """Return mnemonic string for a 14-bit instruction word."""
    word &= 0x3FFF

    # Misc 0x0000..0x007F (no f), specifically:
    if (word & 0xFF80) == 0x0000:
        # NOP, RETURN, RETFIE, SLEEP, CLRWDT, etc.
        misc = {
            0x0008: "RETURN",
            0x0009: "RETFIE",
            0x0063: "SLEEP",
            0x0064: "CLRWDT",
            0x0062: "OPTION",   # legacy
            0x0065: "TRIS",     # legacy
        }
        if word in misc:
            return misc[word]
        # NOP = 0x0000, 0x0020, 0x0040, 0x0060 (any of the four)
        if (word & 0xFF9F) == 0x0000:
            return "NOP"
        return f"DW      0x{word:04X}  ; misc unknown"

    # CLRW (0x0100-0x017F, bit 7 = 0) / CLRF f (0x0180-0x01FF, bit 7 = 1)
    if (word & 0xFF00) == 0x0100:
        if (word >> 7) & 1:
            f = word & 0x7F
            return f"CLRF    0x{f:02X}"
        return "CLRW"

    # MOVWF (0x0080..0x00FF)
    if (word & 0xFF80) == 0x0080:
        f = word & 0x7F
        return f"MOVWF   0x{f:02X}"

    # Byte-oriented (00 0010xx..00 1111xx)
    if (word & 0x3000) == 0x0000:
        op4 = (word >> 8) & 0xF
        d = (word >> 7) & 1
        f = word & 0x7F
        d_str = "F" if d else "W"
        names = {
            0x02: "SUBWF", 0x03: "DECF", 0x04: "IORWF", 0x05: "ANDWF",
            0x06: "XORWF", 0x07: "ADDWF", 0x08: "MOVF",  0x09: "COMF",
            0x0A: "INCF",  0x0B: "DECFSZ", 0x0C: "RRF",  0x0D: "RLF",
            0x0E: "SWAPF", 0x0F: "INCFSZ",
        }
        if op4 in names:
            return f"{names[op4]:8s}0x{f:02X}, {d_str}"
        return f"DW      0x{word:04X}  ; byte-op unknown"

    # Bit-oriented (01 ...)
    if (word & 0x3000) == 0x1000:
        op2 = (word >> 10) & 3
        b = (word >> 7) & 7
        f = word & 0x7F
        names = {0: "BCF", 1: "BSF", 2: "BTFSC", 3: "BTFSS"}
        return f"{names[op2]:8s}0x{f:02X}, {b}"

    # CALL / GOTO (10 ...)
    if (word & 0x3000) == 0x2000:
        op1 = (word >> 11) & 1
        k = word & 0x7FF
        name = "GOTO" if op1 else "CALL"
        return f"{name:8s}0x{k:03X}"

    # Literal ops (11 ...)
    if (word & 0x3000) == 0x3000:
        op4 = (word >> 8) & 0xF
        k = word & 0xFF
        # MOVLW: 11 00xx kkkkkkkk (op4 = 0 or 1)
        if op4 in (0, 1):
            return f"MOVLW   0x{k:02X}"
        # RETLW: 11 01xx kkkkkkkk (op4 = 4 or 5)
        if op4 in (4, 5):
            return f"RETLW   0x{k:02X}"
        # 11 1000 = IORLW
        if op4 == 8:
            return f"IORLW   0x{k:02X}"
        # 11 1001 = ANDLW
        if op4 == 9:
            return f"ANDLW   0x{k:02X}"
        # 11 1010 = XORLW
        if op4 == 0xA:
            return f"XORLW   0x{k:02X}"
        # 11 110x = SUBLW
        if op4 in (0xC, 0xD):
            return f"SUBLW   0x{k:02X}"
        # 11 111x = ADDLW
        if op4 in (0xE, 0xF):
            return f"ADDLW   0x{k:02X}"
        return f"DW      0x{word:04X}  ; literal unknown"

    return f"DW      0x{word:04X}  ; unmapped"


def disassemble(blob, base_addr=0):
    """Return list of (instruction_addr, raw_word, mnemonic, parity_bits)."""
    out = []
    for i in range(0, len(blob) - 1, 2):
        raw = struct.unpack_from("<H", blob, i)[0]
        word14 = raw & 0x3FFF
        parity = (raw >> 14) & 0x3
        mnem = decode(word14, base_addr + (i // 2))
        out.append((base_addr + i // 2, raw, mnem, parity))
    return out


def main():
    if len(sys.argv) < 2:
        print("usage: pic16_disasm.py <fw.bin> [start_word_addr] [count]", file=sys.stderr)
        sys.exit(1)
    blob = open(sys.argv[1], "rb").read()
    start = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0
    count = int(sys.argv[3], 0) if len(sys.argv) > 3 else len(blob) // 2

    rows = disassemble(blob)
    end = min(start + count, len(rows))
    print(f"; PIC16 mid-range disassembly of {sys.argv[1]}")
    print(f"; word_addr  raw   pty  mnemonic")
    print(f"; ----------------------------------------")
    for r in rows[start:end]:
        addr, raw, mnem, par = r
        print(f"{addr:04X}: {raw:04x}  {par:1d}   {mnem}")


if __name__ == "__main__":
    main()
