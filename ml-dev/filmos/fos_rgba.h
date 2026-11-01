/* M-Film's indexed canvas on the DIGIC 8 RGBA compositor. GPL-2.0. */
#ifndef FOS_RGBA_H
#define FOS_RGBA_H

#include <stdint.h>

/* Transparent, but not 0: ML's RGBA refresh leaves a pixel that converts
 * to exactly 0 untouched, so 0 would keep what was drawn there before
 * (the iris blades over LiveView). Alpha 0 still shows LiveView. */
#define FOS_RGBA_CLEAR 0x00000001u

/* A NULL photo selects base; otherwise entries 1..n are the photograph.
 * Indices 0 and 255 stay transparent (FOS_RGBA_CLEAR). fade is 0..256, as in fos_port_photo. */
void fos_rgba_build_palette(uint32_t out[256], const uint32_t base[256],
                            const uint8_t (*photo)[3], int n, int fade,
                            int standard, int saturation, int warmth, int brightness);

#endif
