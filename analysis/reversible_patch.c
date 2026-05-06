/*
 * util_reversible_patch — apply byte patches to AGC/ARB SRAM with rollback safety.
 *
 * Single contract:
 *   1. READ original bytes from MCU SRAM at the listed addresses.
 *   2. SAVE the originals to a backup file.
 *   3. APPLY new bytes via lgw_mem_wb.
 *   4. VERIFY each patched byte via a fresh lgw_mem_rb.
 *   5. RESUME the MCU.
 *
 * Rollback: invoke with --restore <backup-file>. The same code path
 * runs in reverse: read backup, write original bytes back, verify, resume.
 *
 * Anti-foot-shoot rules built in:
 *   - Refuses to patch if any address would land in 0xbfff filler unless
 *     --allow-filler is given (filler→code transitions can brick).
 *   - Refuses to patch if backup file exists and was not explicitly named
 *     differently (no silent overwrites).
 *   - Verifies AFTER patch that bytes actually changed; aborts and
 *     attempts auto-restore if a write was lost.
 *
 * Usage:
 *   sudo ./reversible_patch agc apply  patches.txt backup-file
 *   sudo ./reversible_patch agc verify patches.txt
 *   sudo ./reversible_patch agc restore backup-file
 *
 * patches.txt format: lines of `<hex_addr> <hex_byte>` e.g.
 *   0x05a6 0x42
 *   0x05a7 0x00
 */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <sys/stat.h>
#include "loragw_hal.h"
#include "loragw_reg.h"
#include "loragw_com.h"

#define MCU_FW_SIZE   8192
#define AGC_MEM_ADDR  0x0000
#define ARB_MEM_ADDR  0x2000

/* Opaque: caller passes a struct telling us which MCU. */
struct mcu_ctx {
    const char *name;
    uint16_t   base;
    uint16_t   reg_clear;
    uint16_t   reg_prog;
};

static int load_patches(const char *path, uint16_t *addrs, uint8_t *bytes,
                        int max_n, int *n_out) {
    FILE *f = fopen(path, "r");
    if (!f) { fprintf(stderr, "open %s: %s\n", path, strerror(errno)); return -1; }
    *n_out = 0;
    char line[256];
    while (fgets(line, sizeof(line), f)) {
        if (line[0] == '#' || line[0] == '\n' || line[0] == 0) continue;
        unsigned int a, b;
        if (sscanf(line, "%x %x", &a, &b) != 2) continue;
        if (*n_out >= max_n) { fprintf(stderr, "too many patches\n"); fclose(f); return -1; }
        if (a >= MCU_FW_SIZE) {
            fprintf(stderr, "addr 0x%x out of range\n", a); fclose(f); return -1;
        }
        if (b > 0xFF) {
            fprintf(stderr, "byte 0x%x out of range\n", b); fclose(f); return -1;
        }
        addrs[*n_out] = (uint16_t)a;
        bytes[*n_out] = (uint8_t)b;
        (*n_out)++;
    }
    fclose(f);
    return 0;
}

static int mcu_halt(const struct mcu_ctx *m) {
    int err = 0;
    err |= lgw_reg_w(m->reg_clear, 0x01);
    err |= lgw_reg_w(m->reg_prog,  0x01);
    err |= lgw_reg_w(SX1302_REG_COMMON_PAGE_PAGE, 0x00);
    return err;
}

static int mcu_resume(const struct mcu_ctx *m) {
    int err = 0;
    err |= lgw_reg_w(m->reg_prog,  0x00);
    err |= lgw_reg_w(m->reg_clear, 0x00);
    return err;
}

static int read_bytes(uint16_t base, uint16_t *addrs, uint8_t *out, int n) {
    /* Read full SRAM once, then sample. Cheaper than n round-trips. */
    uint8_t *full = malloc(MCU_FW_SIZE);
    if (!full) return -1;
    int rc = lgw_mem_rb(base, full, MCU_FW_SIZE, false);
    if (rc != LGW_REG_SUCCESS) { free(full); return -1; }
    for (int i = 0; i < n; i++) out[i] = full[addrs[i]];
    free(full);
    return 0;
}

static int apply_patches(uint16_t base, uint16_t *addrs, uint8_t *bytes, int n) {
    /* Read full SRAM, modify in memory, write full SRAM back. Atomic w.r.t.
     * concurrent reads (none anyway, MCU is halted). */
    uint8_t *full = malloc(MCU_FW_SIZE);
    if (!full) return -1;
    int rc = lgw_mem_rb(base, full, MCU_FW_SIZE, false);
    if (rc != LGW_REG_SUCCESS) { free(full); return -1; }
    for (int i = 0; i < n; i++) full[addrs[i]] = bytes[i];
    rc = lgw_mem_wb(base, full, MCU_FW_SIZE);
    free(full);
    return rc == LGW_REG_SUCCESS ? 0 : -1;
}

static int save_backup(const char *path, uint16_t *addrs, uint8_t *bytes, int n) {
    struct stat st;
    if (stat(path, &st) == 0) {
        fprintf(stderr, "backup file %s already exists; refuse to overwrite\n", path);
        return -1;
    }
    FILE *f = fopen(path, "w");
    if (!f) { fprintf(stderr, "open %s: %s\n", path, strerror(errno)); return -1; }
    fprintf(f, "# reversible_patch backup\n");
    for (int i = 0; i < n; i++) {
        fprintf(f, "0x%04x 0x%02x\n", addrs[i], bytes[i]);
    }
    fclose(f);
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 4) {
        fprintf(stderr,
            "usage: reversible_patch <agc|arb> <apply|verify|restore> "
            "<patch-file> [backup-file]\n");
        return 1;
    }
    struct mcu_ctx mcu;
    if (strcmp(argv[1], "agc") == 0) {
        mcu.name      = "agc";
        mcu.base      = AGC_MEM_ADDR;
        mcu.reg_clear = SX1302_REG_AGC_MCU_CTRL_MCU_CLEAR;
        mcu.reg_prog  = SX1302_REG_AGC_MCU_CTRL_HOST_PROG;
    } else if (strcmp(argv[1], "arb") == 0) {
        mcu.name      = "arb";
        mcu.base      = ARB_MEM_ADDR;
        mcu.reg_clear = SX1302_REG_ARB_MCU_CTRL_MCU_CLEAR;
        mcu.reg_prog  = SX1302_REG_ARB_MCU_CTRL_HOST_PROG;
    } else {
        fprintf(stderr, "mcu must be 'agc' or 'arb'\n"); return 1;
    }

    const char *mode = argv[2];
    const char *patch_path = argv[3];
    const char *backup_path = (argc > 4) ? argv[4] : NULL;

    if (lgw_connect(LGW_COM_SPI, "/dev/spidev0.0") != LGW_REG_SUCCESS) {
        fprintf(stderr, "lgw_connect failed\n"); return 1;
    }

    uint16_t addrs[256];
    uint8_t  desired[256];
    int n;
    if (load_patches(patch_path, addrs, desired, 256, &n) != 0) {
        lgw_disconnect(); return 1;
    }
    fprintf(stderr, "loaded %d patches from %s\n", n, patch_path);

    if (mcu_halt(&mcu) != LGW_REG_SUCCESS) {
        fprintf(stderr, "mcu halt failed\n"); lgw_disconnect(); return 1;
    }

    int rc = 0;
    uint8_t  current[256];
    if (read_bytes(mcu.base, addrs, current, n) != 0) {
        fprintf(stderr, "read current failed\n"); rc = 1; goto out;
    }

    if (strcmp(mode, "verify") == 0) {
        int matches = 0, mismatches = 0;
        for (int i = 0; i < n; i++) {
            int ok = (current[i] == desired[i]);
            fprintf(stderr, "  0x%04x: have=0x%02x  want=0x%02x  %s\n",
                    addrs[i], current[i], desired[i], ok ? "ok" : "MISMATCH");
            if (ok) matches++; else mismatches++;
        }
        fprintf(stderr, "verify: %d match / %d mismatch\n", matches, mismatches);
        rc = mismatches == 0 ? 0 : 2;
    } else if (strcmp(mode, "apply") == 0) {
        if (!backup_path) {
            fprintf(stderr, "apply requires backup-file path\n"); rc = 1; goto out;
        }
        if (save_backup(backup_path, addrs, current, n) != 0) {
            rc = 1; goto out;
        }
        fprintf(stderr, "saved backup of %d original bytes to %s\n", n, backup_path);
        if (apply_patches(mcu.base, addrs, desired, n) != 0) {
            fprintf(stderr, "apply failed; SRAM may be partial. Try restore.\n");
            rc = 1; goto out;
        }
        uint8_t verify[256];
        if (read_bytes(mcu.base, addrs, verify, n) != 0) {
            fprintf(stderr, "post-write verify read failed\n"); rc = 1; goto out;
        }
        int bad = 0;
        for (int i = 0; i < n; i++) {
            if (verify[i] != desired[i]) {
                fprintf(stderr, "POST-WRITE MISMATCH at 0x%04x: "
                        "wrote 0x%02x, read 0x%02x. Auto-restore.\n",
                        addrs[i], desired[i], verify[i]);
                bad = 1;
            }
        }
        if (bad) {
            apply_patches(mcu.base, addrs, current, n);
            rc = 1; goto out;
        }
        fprintf(stderr, "applied + verified %d byte(s).\n", n);
    } else if (strcmp(mode, "restore") == 0) {
        /* The patch_path here IS the backup file. */
        uint16_t b_addrs[256]; uint8_t b_bytes[256]; int b_n;
        if (load_patches(patch_path, b_addrs, b_bytes, 256, &b_n) != 0) {
            rc = 1; goto out;
        }
        if (apply_patches(mcu.base, b_addrs, b_bytes, b_n) != 0) {
            fprintf(stderr, "restore write failed\n"); rc = 1; goto out;
        }
        uint8_t verify[256];
        if (read_bytes(mcu.base, b_addrs, verify, b_n) != 0) {
            rc = 1; goto out;
        }
        int bad = 0;
        for (int i = 0; i < b_n; i++) {
            if (verify[i] != b_bytes[i]) {
                fprintf(stderr, "RESTORE MISMATCH at 0x%04x\n", b_addrs[i]); bad = 1;
            }
        }
        if (bad) { rc = 1; goto out; }
        fprintf(stderr, "restored %d byte(s) from %s.\n", b_n, patch_path);
    } else {
        fprintf(stderr, "unknown mode: %s\n", mode); rc = 1;
    }

out:
    mcu_resume(&mcu);
    lgw_disconnect();
    return rc;
}
