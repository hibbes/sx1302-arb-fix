# Build the SX1302 ARB-fix analysis tools.
#
# These C tools link against libloragw from your sx1302_hal build. Point
# LIBLORAGW at the libloragw/ directory of a built sx1302_hal checkout:
#
#   make LIBLORAGW=/path/to/sx1302_hal/libloragw
#
# (libloragw.a must already be built there, i.e. `make` in sx1302_hal first.)

LIBLORAGW ?= ../../sx1302_hal/libloragw

CC      ?= cc
CFLAGS  ?= -O2 -Wall -Wextra
CFLAGS  += -I$(LIBLORAGW)/inc
LDLIBS   = $(LIBLORAGW)/libloragw.a -lm -lrt -lpthread

TOOLS = dump_mem probe_mem probe_arb_internals reload_mem reversible_patch

.PHONY: all clean
all: $(TOOLS)

$(TOOLS): %: %.c
	$(CC) $(CFLAGS) -o $@ $< $(LDLIBS)

clean:
	rm -f $(TOOLS)
