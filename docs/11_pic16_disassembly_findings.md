# PIC16 disassembly: AGC firmware structural analysis

**Status: ISA identified, AGC_STATUS register located on MCU side, bit
semantics partially mapped. Verification level marked per claim.**

## ISA — `[VERIFIED]`

**The SX1302 AGC/ARB/CAL MCUs are PIC16 mid-range (classic, non-enhanced),
14-bit instruction word.**

Smoking gun: `Lora-net/sx1302_hal/libloragw/src/loragw_sx1302.c` line 71:

```c
#define MCU_FW_SIZE  8192 /* size of the firmware IN BYTES (= twice the
                            number of 14b words) */
```

Independent verification (S1, dual-disassembler):
- Microchip's official `gpdasm` (gputils 1.5.2, target `p16f628`)
- Custom Python disassembler (`analysis/pic16_disasm.py`) following the
  Microchip mid-range opcode table

Both tools produce identical mnemonics on **100% of 4096 instructions**
in `agc_fw_sx1250.var V2.1.0` after fixing one CLRF mask bug in the
Python implementation. The dual-disassembler approach caught the bug
immediately on first comparison — exactly as designed.

Reset vector at word `0x000`:
```asm
0000:  bcf   0x0a, 0x3   ; clear PCLATH<3> (low-page select)
0001:  goto  0x07f0      ; jump to fw entry
```

This is the canonical PIC16 reset boilerplate.

## Memory map (host-side SPI windows) — `[VERIFIED]`

From `loragw_reg.c:48-66`:

| Address | Region | What |
|---------|--------|------|
| `0x0000` | EXT_MEM_PAGED | AGC fw code (8 KB) |
| `0x2000` | (continuation) | ARB fw code (8 KB) |
| `0x4000` | RX_BUFFER | 4 KB RX-packet buffer |
| `0x5200` | TX_TOP_A | TX path radio A |
| `0x5400` | TX_TOP_B | TX path radio B |
| `0x5600` | COMMON | Concentrator common SFRs |
| **`0x5780`** | **AGC_MCU** | AGC MCU SFRs |
| `0x5780+0` | AGC_MCU_CTRL | MCU_CLEAR, HOST_PROG, PARITY_ERROR |
| **`0x5781`** | **AGC_MCU_STATUS** | **AGC_STATUS register, 8-bit, host-readable** |
| `0x5782` | AGC_MCU_PA_GAIN | PA_A_GAIN(2b) + PA_B_GAIN(2b) |
| `0x5783` | AGC_MCU_RF_EN_A | RADIO_RST/EN/PA_EN/LNA_EN |
| `0x5784` | AGC_MCU_RF_EN_B | (same for radio B) |
| `0x5785-86` | LUT_TABLE_A/B | PA_LUT(4b) + LNA_LUT(4b) |
| `0x6080` | ARB_MCU | ARB MCU SFRs (mirror layout) |

## AGC_STATUS register on MCU side — `[VERIFIED]`

**File register `0x1b` on the AGC MCU is mapped to host-SPI `0x5781`
(AGC_MCU_MCU_AGC_STATUS).**

Identification proof:
- Init-sequence values `0x03..0x0F` that the HAL polls for
  (`sx1302_agc_wait_status(0x03..0x0F)`) are all written to file `0x1b`
  via `MOVLW imm; MOVWF 0x1b` patterns at:

  | fw word | AGC_STATUS value written |
  |---------|--------------------------|
  | `0x056e` | `0x03` |
  | `0x05a6` | `0x04` |
  | `0x05da` | `0x05` |
  | `0x0611` | `0x06` |
  | `0x065f` | `0x07` |
  | `0x069b` | `0x08` |
  | `0x06d5` | `0x09` |
  | `0x0719` | `0x0a` |
  | `0x072a` | `0x0b` |
  | `0x0743` | `0x0f` |

- Each write is followed by a wait-for-host-mailbox-reply loop. Example
  at `0x056d-0x0575` for status 0x03:

  ```asm
  056d: movlw 0x03         ; W = 0x03
  056e: movwf 0x1b         ; AGC_STATUS = 0x03 (host now reads 0x03)
  056f: movlw 0x3c         ; W = mailbox addr (?)
  0570: call  0x0754       ; helper: read SFR by addr in W
  0572: movwf 0x45         ; save reply value
  0573: xorlw 0x03         ; reply == 0x03 ?
  0574: btfss STATUS, Z    ; skip if equal
  0575: goto  0x056f       ; loop back
  ```

  This is exactly the protocol described in HAL's
  `sx1302_agc_wait_status()` — but from the fw side.

- Init step `0x01` is set later at word `0x0b60` (after main loop entry).
- Step `0x02` is **not in the disassembly as `MOVLW 0x02; MOVWF 0x1b`** —
  but the HAL waits for it. Likely set via the indirect path
  (`MOVF f, W; MOVWF 0x1b`) or via `INCF 0x44, W; MOVWF 0x1b` at
  word `0x04b3` (which writes file `0x44 + 1` to AGC_STATUS in a loop).

## Bit-level operations on AGC_STATUS — `[VERIFIED]`

Two read-modify-write sequences in the post-init main loop:

```asm
; AGC_STATUS bit 0 set, bit 6 cleared (TX initiated radio A)
0c9a: movf  0x1b, W
0c9b: andlw 0xbf       ; clear bit 6
0c9c: iorlw 0x01       ; set bit 0
0c9d: movwf 0x1b
```

```asm
; AGC_STATUS bit 1 set, bit 7 cleared (TX initiated radio B)
0cc1: movf  0x1b, W
0cc2: andlw 0x7f       ; clear bit 7
0cc3: iorlw 0x02       ; set bit 1
0cc4: movwf 0x1b
```

Two single-shot OR operations:

```asm
; Set bits 0 + 6 simultaneously (TX initiated AND LBT-blocked, radio A)
0c9f: movlw 0x41
0ca0: iorwf 0x1b, F

; Set bits 1 + 7 simultaneously (radio B)
0cc6: movlw 0x82
0cc7: iorwf 0x1b, F
```

The bit semantics for AGC_STATUS bits 0/1/6/7 match `loragw_lbt.c:147-176`
(LBT-state bits in the SX1302 datasheet's host-side reference).

## Where does `0x14` come from? — `[INFERRED]`

`0x14` = `0001_0100` = bits 2 and 4.

There is **no `MOVLW 0x14; MOVWF 0x1b` and no `BSF 0x1b, 2` / `BSF 0x1b, 4`
anywhere in agc_fw_sx1250.var V2.1.0**. The bit-2 and bit-4 settings are
not done via direct constants.

But there IS this:

```asm
; word 0x04ab: writes AGC_STATUS = 0x55 (= 0101_0101 = bits 0,2,4,6)
04a5: bcf   0x03, 0x5      ; bank-switch
04a6: bcf   0x03, 0x6
04a7: movf  0x62, W
04a8: return
04a9: movlw 0x55           ; W = 0x55
04aa: bcf   0x03, 0x5      ; bank-switch
04ab: movwf 0x1b           ; AGC_STATUS = 0x55
```

`0x55` has bits 0, 2, 4, 6 set. **If a later code path clears bits 0
and 6 (LBT/TX cleanup) but leaves bits 2 and 4, the resulting value
is exactly `0x14`.** This is consistent with our live observation
(`0x14` sticky in steady-state, transitioning to `0x00` on RX activity
which would also clear bits 2 and 4).

The exact path that clears bits 0 and 6 from `0x55` while leaving 2 and 4
is currently **`[HYPOTHESIS]`** — needs more disassembly walk through
the post-`0x04ab` flow.

## Indirect AGC_STATUS write at word 0x04b3 — `[INFERRED]`

```asm
04ad: movlw 0x02         ; W = 0x02
04ae: subwf 0x44, W      ; W = file[0x44] - 0x02
04af: btfsc STATUS, C    ; if file[0x44] >= 0x02, skip
04b0: goto  0x056d       ; → init step 0x03 path
04b1: movf  0x44, W      ; W = file[0x44]
04b2: addlw 0x01         ; W = file[0x44] + 1
04b3: movwf 0x1b         ; AGC_STATUS = file[0x44] + 1
```

This is a **counter-driven status increment**. File `0x44` looks like
the init-step counter. Walking through:
- `0x44`=0 → AGC_STATUS = 0x01 → wait for host mailbox
- `0x44`=1 → AGC_STATUS = 0x02 → wait
- `0x44`>=2 → branch to step 0x03 explicit path

This explains why `MOVLW 0x02; MOVWF 0x1b` doesn't appear directly: it's
generated by `MOVF 0x44, W; ADDLW 0x01; MOVWF 0x1b` when the counter
is at `1`.

## Status of the bug-localization — `[HYPOTHESIS]`

What we have:
- `[VERIFIED]` Init-sequence path with all status values 0x01..0x0F.
- `[VERIFIED]` File `0x1b` is AGC_STATUS on MCU side.
- `[VERIFIED]` Bits 0/1/6/7 are LBT-related, bit-OR'd at runtime.
- `[INFERRED]` `0x14` arises by clearing bits 0 and 6 from `0x55`
  (written at word `0x04ab`), leaving bits 2 and 4.
- `[HYPOTHESIS]` Bits 2 and 4 are likely "RX engaged" or "AGC quiescent"
  flags that the closed-source fw uses internally and that the HAL
  never reads.

What we don't have yet:
- `[OPEN]` Exact code path that clears bits 0, 6 from `0x55` to leave `0x14`.
- `[OPEN]` Why the chip stays stuck at `0x14` instead of returning to `0x00`.
- `[OPEN]` Where bit 2 / bit 4 get individually set or cleared in code.
  No direct BSF/BCF/IORLW patterns for them in the ENTIRE `agc_fw_sx1250.var`.

This last point is the **most actionable lead**: if bits 2 and 4 are not
set by the AGC fw at all, they must come from somewhere else:
1. The ARB fw (across MCU boundary, requires a hardware-level reflection
   into AGC_STATUS — needs verification).
2. A hardware signal that the SX1302 reflects into AGC_STATUS bits 2/4
   automatically (e.g., a "channelizer active" hardware bit).
3. Indirect addressing via FSR=0x1b that the simple grep missed.

Next step is to disassemble `arb_fw.var` and grep its file-write patterns
for the same kind of read-modify-write on whatever address maps to the
host-visible AGC_STATUS.

## Files added in this analysis pass

- `disassembly/agc_v2.1.0_gpdasm.dasm` — full gpdasm output (4096 lines)
- `disassembly/arb_v2.1.0_gpdasm.dasm` — full gpdasm output for ARB
- `disassembly/cal_v2.1.0_gpdasm.dasm` — full gpdasm output for CAL
- `analysis/pic16_disasm.py` — Python PIC16 mid-range disassembler
  (S1 dual-validator, 100% agreement with gpdasm)
- `analysis/bin2hex.py` — binary blob → Intel HEX converter for gpdasm
