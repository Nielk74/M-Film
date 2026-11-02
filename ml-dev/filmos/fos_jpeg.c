/* fos_jpeg: a small baseline JPEG writer for the developed frames - 4:2:0,
 * the standard Huffman tables, integer DCT (the camera has no FPU), and
 * EXIF with the date, the film and the f-stop.
 */

#include "fos_jpeg.h"
#include "fos.h"
#include <string.h>

#ifdef CONFIG_R
/* ML builds for size; these are big frames, and only whole numbers */
#pragma GCC optimize ("O2")
#endif

/* ---------------------------------------------------------------- tables */

static const uint8_t zigzag[64] =
{
     0,  1,  8, 16,  9,  2,  3, 10, 17, 24, 32, 25, 18, 11,  4,  5,
    12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13,  6,  7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47, 55, 62, 63,
};

/* the example tables of the standard (K.1, K.2), in natural order */
static const uint8_t std_q[2][64] =
{
    { 16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55, 14, 13, 16, 24, 40, 57, 69, 56,
      14, 17, 22, 29, 51, 87, 80, 62, 18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
      49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99 },
    { 17, 18, 24, 47, 99, 99, 99, 99, 18, 21, 26, 66, 99, 99, 99, 99, 24, 26, 56, 99, 99, 99, 99, 99,
      47, 66, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99,
      99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99 },
};

/* the standard's Huffman tables (K.3): code counts by length, then values */
static const uint8_t dc_bits[2][16] = { { 0, 1, 5, 1, 1, 1, 1, 1, 1 }, { 0, 3, 1, 1, 1, 1, 1, 1, 1, 1, 1 } };
static const uint8_t dc_vals[12] = { 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11 };
static const uint8_t ac_bits[2][16] =
{
    { 0, 2, 1, 3, 3, 2, 4, 3, 5, 5, 4, 4, 0, 0, 1, 0x7d },
    { 0, 2, 1, 2, 4, 4, 3, 4, 7, 5, 4, 4, 0, 1, 2, 0x77 },
};
static const uint8_t ac_vals[2][162] =
{
    { 0x01, 0x02, 0x03, 0x00, 0x04, 0x11, 0x05, 0x12, 0x21, 0x31, 0x41, 0x06, 0x13, 0x51, 0x61, 0x07,
      0x22, 0x71, 0x14, 0x32, 0x81, 0x91, 0xa1, 0x08, 0x23, 0x42, 0xb1, 0xc1, 0x15, 0x52, 0xd1, 0xf0,
      0x24, 0x33, 0x62, 0x72, 0x82, 0x09, 0x0a, 0x16, 0x17, 0x18, 0x19, 0x1a, 0x25, 0x26, 0x27, 0x28,
      0x29, 0x2a, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3a, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49,
      0x4a, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59, 0x5a, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69,
      0x6a, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x79, 0x7a, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89,
      0x8a, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9a, 0xa2, 0xa3, 0xa4, 0xa5, 0xa6, 0xa7,
      0xa8, 0xa9, 0xaa, 0xb2, 0xb3, 0xb4, 0xb5, 0xb6, 0xb7, 0xb8, 0xb9, 0xba, 0xc2, 0xc3, 0xc4, 0xc5,
      0xc6, 0xc7, 0xc8, 0xc9, 0xca, 0xd2, 0xd3, 0xd4, 0xd5, 0xd6, 0xd7, 0xd8, 0xd9, 0xda, 0xe1, 0xe2,
      0xe3, 0xe4, 0xe5, 0xe6, 0xe7, 0xe8, 0xe9, 0xea, 0xf1, 0xf2, 0xf3, 0xf4, 0xf5, 0xf6, 0xf7, 0xf8,
      0xf9, 0xfa },
    { 0x00, 0x01, 0x02, 0x03, 0x11, 0x04, 0x05, 0x21, 0x31, 0x06, 0x12, 0x41, 0x51, 0x07, 0x61, 0x71,
      0x13, 0x22, 0x32, 0x81, 0x08, 0x14, 0x42, 0x91, 0xa1, 0xb1, 0xc1, 0x09, 0x23, 0x33, 0x52, 0xf0,
      0x15, 0x62, 0x72, 0xd1, 0x0a, 0x16, 0x24, 0x34, 0xe1, 0x25, 0xf1, 0x17, 0x18, 0x19, 0x1a, 0x26,
      0x27, 0x28, 0x29, 0x2a, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3a, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48,
      0x49, 0x4a, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59, 0x5a, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68,
      0x69, 0x6a, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x79, 0x7a, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87,
      0x88, 0x89, 0x8a, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9a, 0xa2, 0xa3, 0xa4, 0xa5,
      0xa6, 0xa7, 0xa8, 0xa9, 0xaa, 0xb2, 0xb3, 0xb4, 0xb5, 0xb6, 0xb7, 0xb8, 0xb9, 0xba, 0xc2, 0xc3,
      0xc4, 0xc5, 0xc6, 0xc7, 0xc8, 0xc9, 0xca, 0xd2, 0xd3, 0xd4, 0xd5, 0xd6, 0xd7, 0xd8, 0xd9, 0xda,
      0xe2, 0xe3, 0xe4, 0xe5, 0xe6, 0xe7, 0xe8, 0xe9, 0xea, 0xf2, 0xf3, 0xf4, 0xf5, 0xf6, 0xf7, 0xf8,
      0xf9, 0xfa },
};

/* ---------------------------------------------------------------- output */

#ifdef CONFIG_R
#define OUT_SIZE (64 * 1024)    /* the camera flushes its caches for each write */
#else
#define OUT_SIZE 2048
#endif

struct jw
{
    void *f;
    uint8_t buf[OUT_SIZE];
    int n, ok;
    uint32_t bits;
    int nbits;
    uint16_t dc_code[2][12], ac_code[2][256];
    uint8_t dc_len[2][12], ac_len[2][256];
    int32_t recip[2][64];       /* 65536 / (8 q), zigzag order */
    uint8_t q[2][64];           /* zigzag order, as written */
};

static void flush(struct jw *w)
{
    if (w->n && fos_port_write(w->f, w->buf, w->n) != w->n) w->ok = 0;
    w->n = 0;
}

static void put8(struct jw *w, int b)
{
    if (w->n == (int) sizeof(w->buf)) flush(w);
    w->buf[w->n++] = (uint8_t) b;
}

static void put16(struct jw *w, int v) { put8(w, v >> 8); put8(w, v); }

static void put_bits(struct jw *w, uint32_t code, int len)
{
    w->bits = w->bits << len | (code & ((1u << len) - 1));
    w->nbits += len;
    while (w->nbits >= 8)
    {
        int b = (int) (w->bits >> (w->nbits - 8)) & 0xFF;
        put8(w, b);
        if (b == 0xFF) put8(w, 0);          /* stuffed */
        w->nbits -= 8;
    }
}

static void huff_codes(const uint8_t *bits, const uint8_t *vals, uint16_t *code, uint8_t *len)
{
    int c = 0, k = 0;
    for (int l = 1; l <= 16; l++)
    {
        for (int i = 0; i < bits[l - 1]; i++, k++)
        {
            code[vals[k]] = (uint16_t) c++;
            len[vals[k]] = (uint8_t) l;
        }
        c <<= 1;
    }
}

/* ---------------------------------------------------------------- one block */

/* forward DCT, integer (libjpeg's islow, its constants in 1/8192): the
 * output 8 times too large */
enum { C0298 = 2446, C0390 = 3196, C0541 = 4433, C0765 = 6270, C0899 = 7373, C1175 = 9633,
       C1501 = 12299, C1847 = 15137, C1961 = 16069, C2053 = 16819, C2562 = 20995, C3072 = 25172 };
static void fdct(int32_t *d)
{
    for (int pass = 0; pass < 2; pass++)
    {
        for (int i = 0; i < 8; i++)
        {
            int32_t *p = pass ? d + i : d + i * 8;
            const int s = pass ? 8 : 1;
            int32_t t0 = p[0] + p[7 * s], t7 = p[0] - p[7 * s], t1 = p[s] + p[6 * s], t6 = p[s] - p[6 * s];
            int32_t t2 = p[2 * s] + p[5 * s], t5 = p[2 * s] - p[5 * s], t3 = p[3 * s] + p[4 * s], t4 = p[3 * s] - p[4 * s];
            int32_t t10 = t0 + t3, t13 = t0 - t3, t11 = t1 + t2, t12 = t1 - t2;
            const int sh = pass ? 15 : 11;
            if (pass) { p[0] = (t10 + t11 + 2) >> 2; p[4 * s] = (t10 - t11 + 2) >> 2; }
            else      { p[0] = (t10 + t11) * 4;      p[4 * s] = (t10 - t11) * 4; }
            int32_t z1 = (t12 + t13) * C0541;
            p[2 * s] = (z1 + t13 * C0765 + (1 << (sh - 1))) >> sh;
            p[6 * s] = (z1 - t12 * C1847 + (1 << (sh - 1))) >> sh;
            int32_t a = t4 + t7, b = t5 + t6, c = t4 + t6, e = t5 + t7;
            int32_t z5 = (c + e) * C1175;
            t4 *= C0298; t5 *= C2053; t6 *= C3072; t7 *= C1501;
            a *= -C0899; b *= -C2562; c = c * -C1961 + z5; e = e * -C0390 + z5;
            p[7 * s] = (t4 + a + c + (1 << (sh - 1))) >> sh;
            p[5 * s] = (t5 + b + e + (1 << (sh - 1))) >> sh;
            p[3 * s] = (t6 + b + c + (1 << (sh - 1))) >> sh;
            p[s]     = (t7 + a + e + (1 << (sh - 1))) >> sh;
        }
    }
}

static int bit_size(int v)
{
#ifdef CONFIG_R
    return v ? 32 - __builtin_clz((unsigned) (v < 0 ? -v : v)) : 0;
#else
    int n = 0;
    if (v < 0) v = -v;
    while (v) { n++; v >>= 1; }
    return n;
#endif
}

#ifdef CONFIG_R
/* a block of samples (level-shifted) to its coefficients */
static void block_prepare(int32_t *d, const int32_t *recip, int16_t *q)
{
    fdct(d);
    for (int i = 0; i < 64; i++)
    {
        int32_t v = d[zigzag[i]], r = recip[i];
        q[i] = (int16_t) (v >= 0 ? (int) ((v * r + 32768) >> 16) : -(int) ((-v * r + 32768) >> 16));
    }
}

/* a block's coefficients to the file; t: 0 luma, 1 chroma */
static void block_write(struct jw *w, const int16_t *q, int t, int *dc_prev)
{
#else
/* a block of samples (level-shifted) to the file; t: 0 luma, 1 chroma */
static void block(struct jw *w, int32_t *d, int t, int *dc_prev)
{
    fdct(d);
    int q[64];
    for (int i = 0; i < 64; i++)
    {
        int32_t v = d[zigzag[i]], r = w->recip[t][i];
        q[i] = v >= 0 ? (int) ((v * r + 32768) >> 16) : -(int) ((-v * r + 32768) >> 16);
    }
#endif
    int diff = q[0] - *dc_prev, n = bit_size(diff);
    *dc_prev = q[0];
    put_bits(w, w->dc_code[t][n], w->dc_len[t][n]);
    if (n) put_bits(w, (uint32_t) (diff < 0 ? diff - 1 : diff), n);
    int run = 0;
    for (int i = 1; i < 64; i++)
    {
        if (!q[i]) { run++; continue; }
        while (run > 15) { put_bits(w, w->ac_code[t][0xF0], w->ac_len[t][0xF0]); run -= 16; }
        int s = bit_size(q[i]), sym = run << 4 | s;
        put_bits(w, w->ac_code[t][sym], w->ac_len[t][sym]);
        put_bits(w, (uint32_t) (q[i] < 0 ? q[i] - 1 : q[i]), s);
        run = 0;
    }
    if (run) put_bits(w, w->ac_code[t][0], w->ac_len[t][0]);
}

/* ---------------------------------------------------------------- EXIF */

/* a tiny TIFF: IFD0 (description, make, model, software, date) and the
 * EXIF IFD (date taken, f-number) */
/* text for XML: the few characters that can't stand as they are */
static int xml_text(char *out, int size, const char *s)
{
    int n = 0;
    for (; *s && n < size - 6; s++)
    {
        const char *e = *s == '&' ? "&amp;" : *s == '<' ? "&lt;" : *s == '>' ? "&gt;" : 0;
        if (e) { int l = (int) strlen(e); memcpy(out + n, e, l); n += l; }
        else out[n++] = *s;
    }
    out[n] = 0;
    return n;
}

static int xmp(char *out, int size, const char *film)
{
    char f[160];
    xml_text(f, sizeof(f), film);
    int n = snprintf(out, size,
        "<x:xmpmeta xmlns:x=\"adobe:ns:meta/\"><rdf:RDF xmlns:rdf=\"http://www.w3.org/1999/02/22-rdf-syntax-ns#\">"
        "<rdf:Description rdf:about=\"\" xmlns:dc=\"http://purl.org/dc/elements/1.1/\">"
        "<dc:description><rdf:Alt><rdf:li xml:lang=\"x-default\">%s</rdf:li></rdf:Alt></dc:description>"
        "<dc:subject><rdf:Bag><rdf:li>%s</rdf:li><rdf:li>M-Film</rdf:li><rdf:li>film simulation</rdf:li></rdf:Bag></dc:subject>"
        "</rdf:Description></rdf:RDF></x:xmpmeta>", f, f);
    return n < size ? n : size - 1;
}

static int exif(uint8_t *out, const struct fos_jpeg_meta *m)
{
    char date[20] = "";
    if (m->when && strlen(m->when) >= 16)
    {
        /* "2026-10-22 17:45" -> "2026:10:22 17:45:00" */
        memcpy(date, m->when, 16);
        date[4] = date[7] = ':';
        memcpy(date + 16, ":00", 4);
    }
    int fnum = 0;                               /* x10 */
    if (m->fstop && *m->fstop)
    {
        const char *s = m->fstop;
        while (*s >= '0' && *s <= '9') fnum = fnum * 10 + (*s++ - '0');
        fnum *= 10;
        if (*s == '.' && s[1] >= '0' && s[1] <= '9') fnum += s[1] - '0';
    }
    struct { int tag, type; const char *s; } ifd0[] =
    {
        { 0x010E, 2, m->desc }, { 0x010F, 2, "Canon" }, { 0x0110, 2, fos_port_model() },
        { 0x0131, 2, "M-FILM darkroom" }, { 0x0132, 2, date },
    };
    const int n0 = 6, n1 = 1 + (fnum > 0);
    int p = 8, data = 8 + 2 + n0 * 12 + 4 + 2 + n1 * 12 + 4;
    memcpy(out, "II*\0\x08\0\0\0", 8);
#define W16(v) (out[p] = (uint8_t) (v), out[p + 1] = (uint8_t) ((v) >> 8), p += 2)
#define W32(v) (W16((v) & 0xFFFF), W16((uint32_t) (v) >> 16))
    W16(n0);
    for (int i = 0; i < n0 - 1; i++)
    {
        const char *s = ifd0[i].s && *ifd0[i].s ? ifd0[i].s : " ";
        int len = (int) strlen(s) + 1;
        W16(ifd0[i].tag); W16(2); W32(len);
        if (len <= 4) { memset(out + p, 0, 4); memcpy(out + p, s, len); p += 4; }
        else { W32(data); memcpy(out + data, s, len); data += (len + 1) & ~1; }
    }
    int exif_at = 8 + 2 + n0 * 12 + 4;
    W16(0x8769); W16(4); W32(1); W32(exif_at);
    W32(0);
    W16(n1);                                    /* tags in ascending order */
    if (fnum > 0)
    {
        W16(0x829D); W16(5); W32(1); W32(data);
        int q = data;
        out[q] = (uint8_t) fnum; out[q + 1] = (uint8_t) (fnum >> 8); out[q + 2] = out[q + 3] = 0;
        out[q + 4] = 10; out[q + 5] = out[q + 6] = out[q + 7] = 0;
        data += 8;
    }
    W16(0x9003); W16(2); W32(20); W32(data);
    memcpy(out + data, date[0] ? date : "                   ", 20);
    data += 20;
    W32(0);
#undef W16
#undef W32
    return data;
}

/* ---------------------------------------------------------------- the file */

#ifdef CONFIG_R
/* Only writing the blocks to the file has to go in order. Preparing them (the colours, the DCT) is
 * most of the work, and either core can do it: rows of 16 x 16 are prepared ahead into a ring, and
 * this core writes the next one as soon as it is there, and prepares others when it isn't. */
#define RING_SLOTS 16
#define MCU_COEFFS (6 * 64)

/* Real film's shadows hold little colour noise, a high ISO's a lot. In the dark parts of a big frame
 * each chroma sample moves towards the mean of the dark samples about it. A brighter sample neither
 * moves nor lends its colour, so nothing bleeds into the black beside it. Smaller frames have
 * averaged their noise away already. */
#define CALM_WIDTH 2000
#define CALM_R 2                /* in samples, each 2 x 2 pixels */
#define CALM_DARK 110           /* a sample this bright keeps out of it */
#define CALM_FROM 30            /* the mean brightness about it: all the way below this */
#define CALM_TO 90              /* and not at all from this */

static int32_t calm_recip[(2 * CALM_R + 1) * (2 * CALM_R + 1) + 1];
static uint16_t calm_weight[256];

static void calm_tables()
{
    const int span = CALM_TO - CALM_FROM;
    for (int n = 1; n < (int) (sizeof(calm_recip) / sizeof(calm_recip[0])); n++) calm_recip[n] = 65536 / n;
    for (int v = 0; v < 256; v++)
    {
        const int t = v <= CALM_FROM ? 0 : v >= CALM_TO ? span : v - CALM_FROM;
        calm_weight[v] = (uint16_t) (256 - 256 * t * t * (3 * span - 2 * t) / (span * span * span));
    }
}

/* one core's working memory for a row's chroma */
struct chroma
{
    int32_t *cb, *cr;           /* the samples: its 8 rows and the reach more on each side; sums of 4 pixels */
    int32_t *col;               /* cb, cr, brightness and how many, of a column's dark samples in reach */
    int16_t *out;               /* the row's (cb, cr), as the blocks take them */
    uint8_t *peak;              /* its strongest colour: by brightness alone, neon red or blue would pass for dark */
};

struct ring
{
    const struct jw *w;
    const uint8_t *rgb;
    int stride, x0, width, height;
    void *const *rows;
    int mcus, nrows, slots;
    int calm;                   /* the reach, in samples; 0: none */
    struct chroma chroma[2];    /* this core's, the other's */
    int16_t *coeffs;            /* slots x mcus x MCU_COEFFS */
    int *ready;                 /* a slot's row + 1, once it is prepared */
    int next, written, busy;    /* atomics */
    int other_rows;
};

static int chroma_alloc(const struct ring *g, struct chroma *s)
{
    const int cells = g->mcus * 8, samples = cells * (8 + 2 * g->calm);
    int32_t *m = fos_port_alloc(samples * 2 * 4 + cells * 4 * 4 + cells * 8 * 2 * 2 + samples);
    if (!m) return 0;
    s->cb = m;
    s->cr = s->cb + samples;
    s->col = s->cr + samples;
    s->out = (int16_t *) (s->col + cells * 4);
    s->peak = (uint8_t *) (s->out + cells * 8 * 2);
    return 1;
}

/* a row of samples into the columns' sums (sign 1) or out of them (sign -1) */
static void calm_columns(const struct chroma *s, int cells, int row, int sign)
{
    const int32_t *const cb = s->cb + row * cells, *const cr = s->cr + row * cells;
    const uint8_t *const peak = s->peak + row * cells;
    int32_t *c = s->col;
    for (int x = 0; x < cells; x++, c += 4)
    {
        if (peak[x] >= CALM_DARK) continue;
        c[0] += sign * cb[x];
        c[1] += sign * cr[x];
        c[2] += sign * peak[x];
        c[3] += sign;
    }
}

/* row r's chroma into s->out; the sums of 2 x 2 pixels, the edges repeating the last pixel */
static void chroma_row(const struct ring *g, int r, const struct chroma *s)
{
    const int cells = g->mcus * 8, R = g->calm, width = g->width, height = g->height;
    int32_t *cb = s->cb, *cr = s->cr;
    uint8_t *peak = s->peak;
    for (int cy = -R; cy < 8 + R; cy++)
    {
        int y0 = r * 16 + 2 * cy, y1 = y0 + 1;
        y0 = y0 < 0 ? 0 : y0 < height ? y0 : height - 1;
        y1 = y1 < 0 ? 0 : y1 < height ? y1 : height - 1;
        const uint8_t *const p0 = g->rows ? (const uint8_t *) g->rows[y0] + 3 * g->x0 : g->rgb + y0 * g->stride;
        const uint8_t *const p1 = g->rows ? (const uint8_t *) g->rows[y1] + 3 * g->x0 : g->rgb + y1 * g->stride;
        for (int cx = 0; cx < cells; cx++)
        {
            const int a = 3 * (2 * cx < width ? 2 * cx : width - 1), b = 3 * (2 * cx + 1 < width ? 2 * cx + 1 : width - 1);
            const int r_ = p0[a] + p0[b] + p1[a] + p1[b];
            const int g_ = p0[a + 1] + p0[b + 1] + p1[a + 1] + p1[b + 1];
            const int bl = p0[a + 2] + p0[b + 2] + p1[a + 2] + p1[b + 2];
            *cb++ = -11059 * r_ - 21709 * g_ + 32768 * bl;
            *cr++ = 32768 * r_ - 27439 * g_ - 5329 * bl;
            *peak++ = (uint8_t) ((r_ > g_ ? (r_ > bl ? r_ : bl) : (g_ > bl ? g_ : bl)) >> 2);
        }
    }
    if (R) memset(s->col, 0, cells * 4 * sizeof(int32_t));
    for (int y = 0; R && y <= 2 * R; y++) calm_columns(s, cells, y, 1);
    for (int oy = 0; oy < 8; oy++)
    {
        const int own = (oy + R) * cells;
        const int32_t *const c = s->col;
        int32_t scb = 0, scr = 0, sl = 0, sn = 0;
        for (int x = 0; R && x <= R; x++) { scb += c[4 * x]; scr += c[4 * x + 1]; sl += c[4 * x + 2]; sn += c[4 * x + 3]; }
        int16_t *o = s->out + oy * cells * 2;
        for (int x = 0; x < cells; x++, o += 2)
        {
            int32_t vb = s->cb[own + x], vr = s->cr[own + x];
            if (R)
            {
                if (s->peak[own + x] < CALM_DARK)
                {
                    const int32_t re = calm_recip[sn];
                    const int w = calm_weight[(sl * re) >> 16];
                    if (w)
                    {
                        vb = (int32_t) (((int64_t) vb * (256 - w) + ((int64_t) scb * re >> 16) * w) >> 8);
                        vr = (int32_t) (((int64_t) vr * (256 - w) + ((int64_t) scr * re >> 16) * w) >> 8);
                    }
                }
                const int in = x + R + 1, away = x - R;
                if (in < cells) { scb += c[4 * in]; scr += c[4 * in + 1]; sl += c[4 * in + 2]; sn += c[4 * in + 3]; }
                if (away >= 0) { scb -= c[4 * away]; scr -= c[4 * away + 1]; sl -= c[4 * away + 2]; sn -= c[4 * away + 3]; }
            }
            o[0] = (int16_t) ((vb + 131072) >> 18);
            o[1] = (int16_t) ((vr + 131072) >> 18);
        }
        if (R && oy < 7)
        {
            calm_columns(s, cells, oy, -1);
            calm_columns(s, cells, oy + 2 * R + 1, 1);
        }
    }
}

static void prepare_row(const struct ring *g, int r, const struct chroma *s)
{
    const int width = g->width, height = g->height, stride = g->stride, x0 = g->x0;
    const uint8_t *const rgb = g->rgb;
    void *const *const rows = g->rows;
    int16_t *out = g->coeffs + (r % g->slots) * g->mcus * MCU_COEFFS;
    const int my = r * 16;
    int32_t blk[64], cb[64], cr[64];
    chroma_row(g, r, s);
    for (int mx = 0; mx < width; mx += 16, out += MCU_COEFFS)
    {
        for (int b = 0; b < 4; b++)
        {
            int bx = mx + (b & 1) * 8, by = my + (b >> 1) * 8;
            for (int y = 0; y < 8; y++)
            {
                const int ry = by + y < height ? by + y : height - 1;
                const uint8_t *row = rows ? (const uint8_t *) rows[ry] + 3 * x0 : rgb + ry * stride;
                if (bx + 8 <= width)
                {
                    /* nearly every block: no edge to repeat */
                    const uint8_t *px = row + 3 * bx;
                    int32_t *lum = blk + y * 8;
                    for (int x = 0; x < 8; x++, px += 3)
                        lum[x] = ((19595 * px[0] + 38470 * px[1] + 7471 * px[2] + 32768) >> 16) - 128;
                    continue;
                }
                for (int x = 0; x < 8; x++)
                {
                    const uint8_t *px = row + 3 * (bx + x < width ? bx + x : width - 1);
                    blk[y * 8 + x] = ((19595 * px[0] + 38470 * px[1] + 7471 * px[2] + 32768) >> 16) - 128;
                }
            }
            block_prepare(blk, g->w->recip[0], out + b * 64);
        }
        const int cells = g->mcus * 8, at = mx / 2;
        for (int i = 0; i < 64; i++)
        {
            const int16_t *const c = s->out + 2 * ((i >> 3) * cells + at + (i & 7));
            cb[i] = c[0];
            cr[i] = c[1];
        }
        block_prepare(cb, g->w->recip[1], out + 4 * 64);
        block_prepare(cr, g->w->recip[1], out + 5 * 64);
    }
    __atomic_store_n(&g->ready[r % g->slots], r + 1, __ATOMIC_RELEASE);
}

/* the next row to prepare, while its slot is free; -1 if there's none for now, -2 if none are left */
static int ring_claim(struct ring *g)
{
    int r = __atomic_load_n(&g->next, __ATOMIC_RELAXED);
    if (r >= g->nrows) return -2;
    if (r >= __atomic_load_n(&g->written, __ATOMIC_ACQUIRE) + g->slots) return -1;
    return __atomic_compare_exchange_n(&g->next, &r, r + 1, 0, __ATOMIC_ACQ_REL, __ATOMIC_RELAXED) ? r : -1;
}

static void ring_other_core(void *arg)
{
    struct ring *g = arg;
    for (;;)
    {
        const int r = ring_claim(g);
        if (r == -2) break;
        if (r >= 0)
        {
            prepare_row(g, r, &g->chroma[1]);
            g->other_rows++;
        }
    }
    __atomic_store_n(&g->busy, 0, __ATOMIC_RELEASE);
}

/* rows: a frame in pieces, each row's start; else rgb and its stride */
static int jpeg_write(const char *path, const uint8_t *rgb, int stride, void *const *rows, int x0, int width, int height,
                      int quality, const struct fos_jpeg_meta *meta)
#else
int fos_jpeg_write(const char *path, const uint8_t *rgb, int stride, int width, int height,
                   int quality, const struct fos_jpeg_meta *meta)
#endif
{
    struct jw *w = fos_port_alloc(sizeof(struct jw));
    if (!w) return 0;
    memset(w, 0, sizeof(*w));
    w->ok = 1;
    int scale = quality < 50 ? 5000 / quality : 200 - quality * 2;
    for (int t = 0; t < 2; t++)
    {
        for (int i = 0; i < 64; i++)
        {
            int v = (std_q[t][zigzag[i]] * scale + 50) / 100;
            v = v < 1 ? 1 : v > 255 ? 255 : v;
            w->q[t][i] = (uint8_t) v;
            w->recip[t][i] = 65536 / (8 * v);
        }
        huff_codes(dc_bits[t], dc_vals, w->dc_code[t], w->dc_len[t]);
        huff_codes(ac_bits[t], ac_vals[t], w->ac_code[t], w->ac_len[t]);
    }
    w->f = fos_port_open(path, 1);
    if (!w->f) { fos_port_free(w); return 0; }

    put16(w, 0xFFD8);
    /* JFIF */
    put16(w, 0xFFE0); put16(w, 16);
    static const uint8_t jfif[14] = { 'J', 'F', 'I', 'F', 0, 1, 1, 0, 0, 1, 0, 1, 0, 0 };
    for (int i = 0; i < 14; i++) put8(w, jfif[i]);
    /* EXIF */
    if (meta)
    {
        uint8_t ex[512];
        memset(ex, 0, sizeof(ex));
        int n = exif(ex, meta);
        put16(w, 0xFFE1); put16(w, n + 8);
        static const uint8_t tag[6] = { 'E', 'x', 'i', 'f', 0, 0 };
        for (int i = 0; i < 6; i++) put8(w, tag[i]);
        for (int i = 0; i < n; i++) put8(w, ex[i]);
        /* XMP: the film as the caption and as keywords, where photo apps
         * (Lightroom, Photos, Bridge) look for them; EXIF's ImageDescription
         * above is the same caption for the ones that read only EXIF */
        if (meta->desc && *meta->desc)
        {
            static char x[1024];
            int k = xmp(x, sizeof(x), meta->desc);
            static const char ns[] = "http://ns.adobe.com/xap/1.0/";
            put16(w, 0xFFE1); put16(w, 2 + (int) sizeof(ns) + k);
            for (int i = 0; i < (int) sizeof(ns); i++) put8(w, (uint8_t) ns[i]);    /* with its 0 */
            for (int i = 0; i < k; i++) put8(w, (uint8_t) x[i]);
        }
    }
    /* quantisation, frame, Huffman tables */
    for (int t = 0; t < 2; t++)
    {
        put16(w, 0xFFDB); put16(w, 67); put8(w, t);
        for (int i = 0; i < 64; i++) put8(w, w->q[t][i]);
    }
    put16(w, 0xFFC0); put16(w, 17); put8(w, 8); put16(w, height); put16(w, width); put8(w, 3);
    put8(w, 1); put8(w, 0x22); put8(w, 0);
    put8(w, 2); put8(w, 0x11); put8(w, 1);
    put8(w, 3); put8(w, 0x11); put8(w, 1);
    for (int t = 0; t < 2; t++)
    {
        int nd = 0, na = 0;
        for (int i = 0; i < 16; i++) { nd += dc_bits[t][i]; na += ac_bits[t][i]; }
        put16(w, 0xFFC4); put16(w, 2 + 17 + nd + 17 + na);
        put8(w, t);
        for (int i = 0; i < 16; i++) put8(w, dc_bits[t][i]);
        for (int i = 0; i < nd; i++) put8(w, dc_vals[i]);
        put8(w, 0x10 | t);
        for (int i = 0; i < 16; i++) put8(w, ac_bits[t][i]);
        for (int i = 0; i < na; i++) put8(w, ac_vals[t][i]);
    }
    put16(w, 0xFFDA); put16(w, 12); put8(w, 3);
    put8(w, 1); put8(w, 0x00); put8(w, 2); put8(w, 0x11); put8(w, 3); put8(w, 0x11);
    put8(w, 0); put8(w, 63); put8(w, 0);

    /* the picture, 16 x 16 at a time: four luma blocks, then Cb and Cr
     * from the means of 2 x 2 pixels; the edges repeat the last pixel */
    int dc[3] = { 0, 0, 0 };
#ifdef CONFIG_R
    struct ring g;
    memset(&g, 0, sizeof(g));
    g.w = w;
    g.rgb = rgb; g.stride = stride; g.rows = rows; g.x0 = x0;
    g.width = width; g.height = height;
    g.mcus = (width + 15) / 16;
    g.nrows = (height + 15) / 16;
    g.calm = width >= CALM_WIDTH ? CALM_R : 0;
    calm_tables();
    int *ring_mem = 0;
    for (g.slots = RING_SLOTS; g.slots && !ring_mem; g.slots /= 2)
    {
        ring_mem = fos_port_alloc(g.slots * (int) sizeof(int) + g.slots * g.mcus * MCU_COEFFS * (int) sizeof(int16_t));
        if (ring_mem) break;
    }
    if (!ring_mem || !chroma_alloc(&g, &g.chroma[0]))
    {
        fos_port_free(ring_mem);
        fos_port_close(w->f);
        fos_port_free(w);
        return 0;
    }
    memset(ring_mem, 0, g.slots * sizeof(int));
    g.ready = ring_mem;
    g.coeffs = (int16_t *) (ring_mem + g.slots);
    __atomic_store_n(&g.busy, 1, __ATOMIC_RELEASE);
    if (!chroma_alloc(&g, &g.chroma[1]) || !fos_port_other_core(ring_other_core, &g)) __atomic_store_n(&g.busy, 0, __ATOMIC_RELEASE);
    for (int r = 0; r < g.nrows; )
    {
        if (__atomic_load_n(&g.ready[r % g.slots], __ATOMIC_ACQUIRE) == r + 1)
        {
            const int16_t *q = g.coeffs + (r % g.slots) * g.mcus * MCU_COEFFS;
            for (int m = 0; m < g.mcus; m++, q += MCU_COEFFS)
            {
                for (int b = 0; b < 4; b++) block_write(w, q + b * 64, 0, &dc[0]);
                block_write(w, q + 4 * 64, 1, &dc[1]);
                block_write(w, q + 5 * 64, 1, &dc[2]);
            }
            __atomic_store_n(&g.written, ++r, __ATOMIC_RELEASE);
            continue;
        }
        const int c = ring_claim(&g);
        if (c >= 0) prepare_row(&g, c, &g.chroma[0]);
    }
    while (__atomic_load_n(&g.busy, __ATOMIC_ACQUIRE)) fos_port_sleep(1);
    fos_port_free(ring_mem);
    fos_port_free(g.chroma[0].cb);
    fos_port_free(g.chroma[1].cb);
    {
        char msg[64];
        snprintf(msg, sizeof(msg), "dev: other core: %d of %d rows of the JPEG", g.other_rows, g.nrows);
        fos_port_log(msg);
    }
#else
    int32_t blk[64], cb[64], cr[64];
    for (int my = 0; my < height; my += 16)
        for (int mx = 0; mx < width; mx += 16)
        {
            memset(cb, 0, sizeof(cb));
            memset(cr, 0, sizeof(cr));
            for (int b = 0; b < 4; b++)
            {
                int bx = mx + (b & 1) * 8, by = my + (b >> 1) * 8;
                for (int y = 0; y < 8; y++)
                {
                    const uint8_t *row = rgb + (by + y < height ? by + y : height - 1) * stride;
                    for (int x = 0; x < 8; x++)
                    {
                        const uint8_t *px = row + 3 * (bx + x < width ? bx + x : width - 1);
                        int r = px[0], g = px[1], bl = px[2];
                        blk[y * 8 + x] = ((19595 * r + 38470 * g + 7471 * bl + 32768) >> 16) - 128;
                        int k = ((b >> 1) * 4 + (y >> 1)) * 8 + (b & 1) * 4 + (x >> 1);
                        cb[k] += -11059 * r - 21709 * g + 32768 * bl;
                        cr[k] += 32768 * r - 27439 * g - 5329 * bl;
                    }
                }
                block(w, blk, 0, &dc[0]);
            }
            for (int i = 0; i < 64; i++) { cb[i] = (cb[i] + 131072) >> 18; cr[i] = (cr[i] + 131072) >> 18; }
            block(w, cb, 1, &dc[1]);
            block(w, cr, 1, &dc[2]);
        }
#endif
    if (w->nbits) put_bits(w, 0x7F, 8 - w->nbits);     /* pad with ones */
    put16(w, 0xFFD9);
    flush(w);
    fos_port_close(w->f);
    int ok = w->ok;
    fos_port_free(w);
    return ok;
}

#ifdef CONFIG_R
int fos_jpeg_write(const char *path, const uint8_t *rgb, int stride, int width, int height,
                   int quality, const struct fos_jpeg_meta *meta)
{
    return jpeg_write(path, rgb, stride, 0, 0, width, height, quality, meta);
}

int fos_jpeg_write_rows(const char *path, void *const *rows, int x0, int width, int height,
                        int quality, const struct fos_jpeg_meta *meta)
{
    return jpeg_write(path, 0, 0, rows, x0, width, height, quality, meta);
}
#endif
