#ifndef _fos_jpeg_h_
#define _fos_jpeg_h_

#include <stdint.h>

/* what goes into the JPEG's EXIF (any may be NULL) */
struct fos_jpeg_meta
{
    const char *when;           /* "2026-10-22 17:45" */
    const char *desc;           /* the film */
    const char *fstop;          /* "2.8" */
};

/* width x height RGB pixels (rows stride bytes apart) as a baseline JPEG
 * at path; quality 1..100. 1 when written. */
int fos_jpeg_write(const char *path, const uint8_t *rgb, int stride, int width, int height,
                   int quality, const struct fos_jpeg_meta *meta);

#ifdef CONFIG_R
/* the same from a frame in pieces: rows[y] + 3 * x0 is the row's first pixel */
int fos_jpeg_write_rows(const char *path, void *const *rows, int x0, int width, int height,
                        int quality, const struct fos_jpeg_meta *meta);
#endif

#endif
