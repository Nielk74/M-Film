/* fos_crx: the RAW in a Canon CR3 file, a line of one colour plane at a time.
 *
 * A CR3 is an ISO media file. Its largest CRX track holds the sensor's mosaic
 * as four planes, each coded on its own, tile by tile, so a frame can be
 * decoded straight into the print without ever being held in memory.
 */

#ifndef _fos_crx_h_
#define _fos_crx_h_

#include <stdint.h>

/* what the file's boxes say */
struct fos_cr3
{
    int orient;                         /* EXIF orientation */
    int crop_l, crop_t, crop_w, crop_h; /* the picture area; crop_l < 0: not given */
    int ob_l, ob_r;                     /* masked columns, if given */
    int raw_w, raw_h;                   /* the mosaic */
    int tile_w, tile_h;                 /* in the mosaic */
    int version, bits, planes, cfa, enc, levels;
    int hdr_offset, hdr_size;           /* the CRX header, then the tiles */
    int ctmd_offset, ctmd_size;         /* the frame's metadata track */
};

/* 1: read; 0: not a CR3 at all; < 0: a CR3 that can't be used (see *why) */
int fos_cr3_parse(void *f, struct fos_cr3 *c, const char **why);
/* the white balance of the shot, red and blue against green; 0: the file doesn't say */
int fos_cr3_white(void *f, const struct fos_cr3 *c, float *r, float *b);

/* a decoded line: mosaic columns x0, x0 + 2, ... of mosaic row y */
struct fos_crx_line
{
    const uint16_t *v;
    int n, x0, y;
};

struct fos_crx;

struct fos_crx *fos_crx_open(void *f, const struct fos_cr3 *c, const char **why);
/* 1: a line, top to bottom, each row's planes together; 0: the frame is done;
 * < 0: damaged (see fos_crx_error) */
int fos_crx_next(struct fos_crx *x, struct fos_crx_line *line);
int fos_crx_read_ms(const struct fos_crx *x);    /* the time spent reading the card */
const char *fos_crx_error(const struct fos_crx *x);
void fos_crx_close(struct fos_crx *x);

#endif
