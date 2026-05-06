/*
 * util_probe_arb_internals — wide SPI-memory scan with ARB MCU halted.
 *
 * Goal: discover where PIC16 file registers (specifically file 0x5c, 0x46-0x4a,
 * 0x5e — the inputs to the suspicious INDF-write cluster at ARB 0x067e-0x069d)
 * are mirrored into the SX1302's SPI address window. The HAL doesn't expose this
 * directly, but several memory regions outside the known code-RAM (0x0000-0x3FFF)
 * and known state-RAM (0x4000-0x7FFF) might carry mirrors.
 *
 * Usage:
 *   sudo ./probe_arb_internals <outdir>
 *
 * Output (in outdir/, one file per 4KB region):
 *   region_0000.bin   AGC code RAM
 *   region_2000.bin   ARB code RAM
 *   region_4000.bin   RX buffer (DIRECT_RAM_IF)
 *   region_5000.bin   typically unmapped — check for peripheral mirror
 *   region_6000.bin   state-RAM primary
 *   region_6800.bin   state-RAM
 *   region_6C00.bin   state-RAM
 *   region_7000.bin   state-RAM
 *   region_7800.bin   uncharted
 *   region_8000.bin   uncharted (high-bank, possibly file-register mirror)
 *   region_9000.bin   uncharted
 *   region_F000.bin   uncharted (last 4KB)
 *
 * Halt sequence: MCU_CLEAR=1 + HOST_PROG=1 + COMMON_PAGE=0 (matches dump_mem).
 * This ensures the SX1302 SPI window doesn't race with running fw writes.
 *
 * Pre-requisite: SX1302 daemon stopped (single-master SPI).
 */

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include "loragw_hal.h"
#include "loragw_reg.h"
#include "loragw_com.h"

#define CHUNK_SIZE 4096

static const uint16_t REGIONS[] = {
    0x0000, 0x2000,                                     /* code RAM (sanity) */
    0x4000,                                             /* RX buffer */
    0x5000,                                             /* unmapped? probe */
    0x6000, 0x6800, 0x6C00, 0x7000, 0x7800,            /* state-RAM */
    0x8000, 0x9000, 0xA000, 0xB000, 0xC000, 0xD000,    /* uncharted high */
    0xE000, 0xF000,
};

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: probe_arb_internals <outdir>\n");
        return 1;
    }
    const char *outdir = argv[1];
    if (mkdir(outdir, 0755) != 0 && errno != EEXIST) {
        fprintf(stderr, "mkdir %s: ", outdir); perror("");
        return 1;
    }

    if (lgw_connect(LGW_COM_SPI, "/dev/spidev0.0") != LGW_REG_SUCCESS) {
        fprintf(stderr, "lgw_connect failed\n");
        return 1;
    }

    /* Halt both MCUs to ensure SPI window is consistent. */
    int err = 0;
    err |= lgw_reg_w(SX1302_REG_AGC_MCU_CTRL_MCU_CLEAR, 1);
    err |= lgw_reg_w(SX1302_REG_AGC_MCU_CTRL_HOST_PROG, 1);
    err |= lgw_reg_w(SX1302_REG_ARB_MCU_CTRL_MCU_CLEAR, 1);
    err |= lgw_reg_w(SX1302_REG_ARB_MCU_CTRL_HOST_PROG, 1);
    err |= lgw_reg_w(SX1302_REG_COMMON_PAGE_PAGE, 0);
    if (err != LGW_REG_SUCCESS) {
        fprintf(stderr, "MCU halt setup failed (rc=%d)\n", err);
        lgw_disconnect();
        return 1;
    }

    uint8_t buf[CHUNK_SIZE];
    char path[512];
    int n_regions = sizeof(REGIONS) / sizeof(REGIONS[0]);
    int n_ok = 0;

    for (int i = 0; i < n_regions; i++) {
        uint16_t addr = REGIONS[i];
        int direct_ram = (addr >= 0x4000 && addr < 0x5000);
        if (direct_ram) lgw_reg_w(SX1302_REG_RX_TOP_RX_BUFFER_DIRECT_RAM_IF, 1);

        int rc = lgw_mem_rb(addr, buf, CHUNK_SIZE, false);
        if (direct_ram) lgw_reg_w(SX1302_REG_RX_TOP_RX_BUFFER_DIRECT_RAM_IF, 0);

        if (rc != LGW_REG_SUCCESS) {
            fprintf(stderr, "region 0x%04x: lgw_mem_rb failed (rc=%d), skipping\n", addr, rc);
            continue;
        }

        snprintf(path, sizeof(path), "%s/region_%04X.bin", outdir, addr);
        FILE *f = fopen(path, "wb");
        if (!f) {
            fprintf(stderr, "region 0x%04x: fopen %s: ", addr, path);
            perror("");
            continue;
        }
        fwrite(buf, 1, CHUNK_SIZE, f);
        fclose(f);
        n_ok++;
        fprintf(stderr, "region 0x%04x → %s (%d bytes)\n", addr, path, CHUNK_SIZE);
    }

    /* Release halt */
    err  = lgw_reg_w(SX1302_REG_ARB_MCU_CTRL_HOST_PROG, 0);
    err |= lgw_reg_w(SX1302_REG_ARB_MCU_CTRL_MCU_CLEAR, 0);
    err |= lgw_reg_w(SX1302_REG_AGC_MCU_CTRL_HOST_PROG, 0);
    err |= lgw_reg_w(SX1302_REG_AGC_MCU_CTRL_MCU_CLEAR, 0);
    if (err != LGW_REG_SUCCESS) {
        fprintf(stderr, "warning: MCU resume failed (rc=%d)\n", err);
    }

    lgw_disconnect();
    fprintf(stderr, "captured %d/%d regions\n", n_ok, n_regions);
    return (n_ok > 0) ? 0 : 1;
}
