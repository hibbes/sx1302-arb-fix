/*
 * util_reload_mem — write a fw blob back into a running MCU's SRAM.
 *
 * Halts the target MCU via MCU_CLEAR=1 + HOST_PROG=1, writes the given
 * fw blob via lgw_mem_wb, releases the halt. The MCU then begins
 * executing from the fw entry point as if freshly loaded — but
 * crucially WITHOUT a HAT power cycle.
 *
 * Hypothesis (this tool tests it):
 * If the AGC_STATUS = 0x14 stuck state is a logic-state lockup of the
 * fw state machine (not corrupted bytes), then reloading the same bytes
 * back into halted SRAM should reset the state machine: AGC_STATUS will
 * drop from 0x14 to 0x01 (or run through the init sequence).
 *
 * Usage:
 *   sudo ./reload_mem <agc|arb> <fw.bin>
 *
 * Pre-requisite: SX1302 daemon stopped.
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
        fprintf(stderr, "usage: reload_mem <agc|arb> <fw.bin>\n");
        return 1;
    }
    uint16_t addr;
    uint16_t reg_clear, reg_prog, reg_parity;
    if (strcmp(argv[1], "agc") == 0) {
        addr       = AGC_MEM_ADDR;
        reg_clear  = SX1302_REG_AGC_MCU_CTRL_MCU_CLEAR;
        reg_prog   = SX1302_REG_AGC_MCU_CTRL_HOST_PROG;
        reg_parity = SX1302_REG_AGC_MCU_CTRL_PARITY_ERROR;
    } else if (strcmp(argv[1], "arb") == 0) {
        addr       = ARB_MEM_ADDR;
        reg_clear  = SX1302_REG_ARB_MCU_CTRL_MCU_CLEAR;
        reg_prog   = SX1302_REG_ARB_MCU_CTRL_HOST_PROG;
        reg_parity = SX1302_REG_ARB_MCU_CTRL_PARITY_ERROR;
    } else {
        fprintf(stderr, "mcu must be 'agc' or 'arb'\n");
        return 1;
    }

    FILE *f = fopen(argv[2], "rb");
    if (!f) { perror("fopen"); return 1; }
    uint8_t fw[MCU_FW_SIZE];
    size_t n = fread(fw, 1, MCU_FW_SIZE, f);
    fclose(f);
    if (n != MCU_FW_SIZE) {
        fprintf(stderr, "expected %d bytes, got %zu\n", MCU_FW_SIZE, n);
        return 1;
    }

    if (lgw_connect(LGW_COM_SPI, "/dev/spidev0.0") != LGW_REG_SUCCESS) {
        fprintf(stderr, "lgw_connect failed\n");
        return 1;
    }

    /* Read AGC_STATUS BEFORE the halt to capture pre-reload state. */
    int32_t agc_before = -1, arb_before = -1;
    lgw_reg_r(SX1302_REG_AGC_MCU_MCU_AGC_STATUS_MCU_AGC_STATUS, &agc_before);
    lgw_reg_r(SX1302_REG_ARB_MCU_MCU_ARB_STATUS_MCU_ARB_STATUS, &arb_before);
    fprintf(stderr, "PRE-reload:  AGC_STATUS=0x%02X  ARB_STATUS=0x%02X\n",
            agc_before & 0xFF, arb_before & 0xFF);

    /* Halt + host-prog + select page 0. */
    int err = 0;
    err |= lgw_reg_w(reg_clear, 0x01);
    err |= lgw_reg_w(reg_prog,  0x01);
    err |= lgw_reg_w(SX1302_REG_COMMON_PAGE_PAGE, 0x00);
    if (err != LGW_REG_SUCCESS) {
        fprintf(stderr, "MCU halt setup failed\n");
        lgw_disconnect();
        return 1;
    }

    /* Write fw blob. */
    int wb_err = lgw_mem_wb(addr, fw, MCU_FW_SIZE);
    if (wb_err != LGW_REG_SUCCESS) {
        fprintf(stderr, "lgw_mem_wb failed (rc=%d)\n", wb_err);
    }

    /* Optional: read back to verify. */
    uint8_t verify[MCU_FW_SIZE];
    if (lgw_mem_rb(addr, verify, MCU_FW_SIZE, false) == LGW_REG_SUCCESS) {
        if (memcmp(fw, verify, MCU_FW_SIZE) == 0) {
            fprintf(stderr, "verify: SRAM matches blob byte-for-byte\n");
        } else {
            int diffs = 0;
            for (int i = 0; i < MCU_FW_SIZE; i++) if (fw[i] != verify[i]) diffs++;
            fprintf(stderr, "verify: %d/%d bytes differ\n", diffs, MCU_FW_SIZE);
        }
    }

    /* Release halt. */
    err  = lgw_reg_w(reg_prog,  0x00);
    err |= lgw_reg_w(reg_clear, 0x00);
    if (err != LGW_REG_SUCCESS) {
        fprintf(stderr, "warning: MCU resume failed (rc=%d)\n", err);
    }

    /* Read parity + AGC_STATUS shortly after release. */
    int32_t parity = -1, agc_after = -1, arb_after = -1;
    lgw_reg_r(reg_parity, &parity);
    lgw_reg_r(SX1302_REG_AGC_MCU_MCU_AGC_STATUS_MCU_AGC_STATUS, &agc_after);
    lgw_reg_r(SX1302_REG_ARB_MCU_MCU_ARB_STATUS_MCU_ARB_STATUS, &arb_after);
    fprintf(stderr, "POST-reload: AGC_STATUS=0x%02X  ARB_STATUS=0x%02X  PARITY_%s=%d\n",
            agc_after & 0xFF, arb_after & 0xFF, argv[1], parity & 0xFF);

    lgw_disconnect();
    return wb_err == LGW_REG_SUCCESS ? 0 : 1;
}
