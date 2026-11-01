#include "fos_rgba.h"
#include "fos_gfx.h"
#include "fos_dev.h"

void fos_rgba_build_palette(uint32_t out[256], const uint32_t base[256],
                            const uint8_t (*photo)[3], int n, int fade,
                            int standard, int saturation, int warmth, int brightness)
{
    n = fos_clamp(n, 0, FOS_PRINT_COLOURS);
    fade = fos_clamp(fade, 0, 256);
    out[0] = out[255] = 0;
    for (int i = 1; i < 255; i++)
    {
        uint32_t rgb = base[i];
        if (photo)
        {
            const uint8_t *c = i <= n ? photo[i - 1] :
                i >= FOS_FIXED_BASE && i < FOS_FIXED_BASE + FOS_FIXED_N ?
                fos_fixed_rgb[i - FOS_FIXED_BASE] : 0;
            rgb = c ? (uint32_t)(c[0] * fade >> 8) << 16 |
                      (uint32_t)(c[1] * fade >> 8) << 8 |
                      (uint32_t)(c[2] * fade >> 8) : 0;
        }
        /* Match the existing simulator's calibrated screen rendering. */
        out[i] = 0xff000000u | fos_screen_rgb(
            fos_screen_yuv_rgb(rgb, 235, standard, saturation, warmth, brightness), 235);
    }
}
