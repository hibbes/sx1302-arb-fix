/*
 * util_dump_mem — read AGC or ARB MCU memory from running SX1302.
 *
 * Halts the target MCU briefly via MCU_CLEAR=1 + HOST_PROG=1, reads its
 * 8 KB SRAM via lgw_mem_rb, then releases the halt. The MCU resumes
 * with the same SRAM state it had before the read (no fw reload).
 *
 * Usage:
 *   sudo ./dump_mem <agc|arb> <outfile>
 *
 * Pre-requisite: SX1302 daemon stopped (single-master SPI).
 */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "loragw_hal.h"
#include "loragw_reg.h"
#include "loragw_com.h"

#define MCU_FW_SIZE   8192
#define AGC_MEM_ADDR  0x0000
#define ARB_MEM_ADDR  0x2000

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: dump_mem <agc|arb> <outfile>\n");
        return 1;
    }
    uint16_t addr;
    uint16_t reg_clear, reg_prog;
    if (strcmp(argv[1], "agc") == 0) {
        addr      = AGC_MEM_ADDR;
        reg_clear = SX1302_REG_AGC_MCU_CTRL_MCU_CLEAR;
        reg_prog  = SX1302_REG_AGC_MCU_CTRL_HOST_PROG;
    } else if (strcmp(argv[1], "arb") == 0) {
        addr      = ARB_MEM_ADDR;
        reg_clear = SX1302_REG_ARB_MCU_CTRL_MCU_CLEAR;
        reg_prog  = SX1302_REG_ARB_MCU_CTRL_HOST_PROG;
    } else {
        fprintf(stderr, "mcu must be 'agc' or 'arb'\n");
        return 1;
    }

    if (lgw_connect(LGW_COM_SPI, "/dev/spidev0.0") != LGW_REG_SUCCESS) {
        fprintf(stderr, "lgw_connect failed\n");
        return 1;
    }

    /* Halt the MCU and switch to host-prog mode (matches HAL fw_check seq). */
    int err = 0;
    err |= lgw_reg_w(reg_clear, 0x01);
    err |= lgw_reg_w(reg_prog,  0x01);
    err |= lgw_reg_w(SX1302_REG_COMMON_PAGE_PAGE, 0x00);
    if (err != LGW_REG_SUCCESS) {
        fprintf(stderr, "MCU halt setup failed\n");
        lgw_disconnect();
        return 1;
    }

    uint8_t buf[MCU_FW_SIZE];
    int rb_err = lgw_mem_rb(addr, buf, MCU_FW_SIZE, false);

    /* Release halt regardless of read result. */
    err  = lgw_reg_w(reg_prog,  0x00);
    err |= lgw_reg_w(reg_clear, 0x00);
    if (err != LGW_REG_SUCCESS) {
        fprintf(stderr, "warning: MCU resume failed (rc=%d)\n", err);
    }

    if (rb_err != LGW_REG_SUCCESS) {
        fprintf(stderr, "lgw_mem_rb failed (rc=%d)\n", rb_err);
        lgw_disconnect();
        return 1;
    }

    FILE *f = fopen(argv[2], "wb");
    if (!f) { perror("fopen"); lgw_disconnect(); return 1; }
    fwrite(buf, 1, MCU_FW_SIZE, f);
    fclose(f);

    lgw_disconnect();
    fprintf(stderr, "Wrote %d bytes from 0x%04x (%s) to %s (MCU halt-read-resume)\n",
            MCU_FW_SIZE, addr, argv[1], argv[2]);
    return 0;
}
