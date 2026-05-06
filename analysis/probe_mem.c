/*
 * util_probe_mem — read N bytes from any SX1302 lgw_mem address window.
 *
 * Usage:
 *   sudo ./probe_mem <hex_addr> <size> [outfile]
 *
 * If addr is in the RX-buffer region (0x4000-0x4FFF), DIRECT_RAM_IF is
 * temporarily enabled.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "loragw_hal.h"
#include "loragw_reg.h"
#include "loragw_com.h"

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: probe_mem <hex_addr> <size> [out]\n");
        return 1;
    }
    uint32_t addr = strtoul(argv[1], NULL, 0);
    uint32_t size = strtoul(argv[2], NULL, 0);

    if (lgw_connect(LGW_COM_SPI, "/dev/spidev0.0") != LGW_REG_SUCCESS) {
        fprintf(stderr, "lgw_connect failed\n");
        return 1;
    }

    int direct_ram = (addr >= 0x4000 && addr < 0x5000);
    if (direct_ram) lgw_reg_w(SX1302_REG_RX_TOP_RX_BUFFER_DIRECT_RAM_IF, 1);

    uint8_t *buf = malloc(size);
    if (!buf) { lgw_disconnect(); return 1; }
    int rc = lgw_mem_rb((uint16_t)addr, buf, (uint16_t)size, false);
    if (direct_ram) lgw_reg_w(SX1302_REG_RX_TOP_RX_BUFFER_DIRECT_RAM_IF, 0);

    if (rc != LGW_REG_SUCCESS) {
        fprintf(stderr, "lgw_mem_rb failed at 0x%04x (rc=%d)\n", addr, rc);
        free(buf);
        lgw_disconnect();
        return 1;
    }

    if (argc >= 4) {
        FILE *f = fopen(argv[3], "wb");
        if (!f) { perror("fopen"); free(buf); lgw_disconnect(); return 1; }
        fwrite(buf, 1, size, f);
        fclose(f);
    }
    fprintf(stderr, "read %u bytes from 0x%04x\n", size, addr);
    free(buf);
    lgw_disconnect();
    return 0;
}
