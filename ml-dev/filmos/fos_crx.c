/* fos_crx: Canon's CR3 RAW, for developing in the camera.
 *
 * The coding follows LibRaw's decoder (src/decoders/crx.cpp, LGPL 2.1, by
 * Alexey Danilchenko and Alex Tutubalin; see THIRD_PARTY.md) and the format
 * notes in github.com/lclevy/canon_cr3, written here to run a line at a time
 * in the camera's small memory. A plane's line depends only on the line
 * before it, so two lines and a read buffer are all it needs.
 */

#include "fos.h"
#include "fos_crx.h"
#include <string.h>

/* ML builds for size; this is the camera's hottest code, and only whole numbers */
#pragma GCC optimize ("O2")

#define READ_SIZE   (128 * 1024)        /* each plane's, read ahead from the card */
#define READ_TOTAL  (2 * 1024 * 1024)   /* in all, with many tiles */

static int be16(const uint8_t *p) { return p[0] << 8 | p[1]; }
static uint32_t be32(const uint8_t *p) { return (uint32_t) p[0] << 24 | p[1] << 16 | p[2] << 8 | p[3]; }
static int le16(const uint8_t *p) { return p[0] | p[1] << 8; }
static uint32_t le32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t) p[3] << 24; }

/* ---------------------------------------------------------------- the file's boxes */

static int box_is(const uint8_t *b, const char *name) { return !memcmp(b + 4, name, 4); }

/* the value of a tag in the TIFF block at t (of size n), or -1; *cnt its count */
static long tiff_value(const uint8_t *t, long n, int tag, int *cnt)
{
    if (n < 8 || t[0] != 'I' || t[1] != 'I') return -1;
    uint32_t ifd = le32(t + 4);
    if (ifd + 2 > (uint32_t) n) return -1;
    int count = le16(t + ifd);
    for (int i = 0; i < count && ifd + 14 + 12 * i <= (uint32_t) n; i++)
    {
        const uint8_t *e = t + ifd + 2 + 12 * i;
        if (le16(e) != tag) continue;
        if (cnt) *cnt = (int) le32(e + 4);
        return le16(e + 2) == 3 && le32(e + 4) <= 2 ? le16(e + 8) : (long) le32(e + 8);
    }
    return -1;
}

static void parse_meta(struct fos_cr3 *c, const uint8_t *b, long n)
{
    for (long o = 0; o + 8 <= n; )
    {
        long size = be32(b + o);
        if (size < 8 || o + size > n) return;
        const uint8_t *t = b + o + 8;
        long tn = size - 8;
        if (box_is(b + o, "CMT1"))
        {
            long v = tiff_value(t, tn, 0x0112, 0);
            if (v > 0) c->orient = (int) v;
        }
        else if (box_is(b + o, "CMT3"))
        {
            int cnt = 0;
            long si = tiff_value(t, tn, 0x00E0, &cnt);
            if (si > 0 && cnt >= 13 && si + 26 <= tn)
            {
                const uint8_t *s = t + si;
                c->crop_l = le16(s + 10); c->crop_t = le16(s + 12);
                c->crop_w = le16(s + 14) - c->crop_l + 1; c->crop_h = le16(s + 16) - c->crop_t + 1;
                c->ob_l = le16(s + 18); c->ob_r = le16(s + 22);
            }
        }
        o += size;
    }
}

/* a track: the first CRAW in its sample table, with its CMP1 */
static void parse_track(struct fos_cr3 *c, const uint8_t *b, long n, int depth)
{
    static const char *const inner[] = { "trak", "mdia", "minf", "stbl" };
    int width = 0, height = 0;
    const uint8_t *cmp = 0;
    long offset = -1;
    for (long o = 0; o + 8 <= n; )
    {
        long size = be32(b + o);
        if (size < 8 || o + size > n) return;
        if (depth < 4 && box_is(b + o, inner[depth])) parse_track(c, b + o + 8, size - 8, depth + 1);
        if (depth == 4 && box_is(b + o, "stsd") && size > 16 + 0x5A && box_is(b + o + 16, "CRAW"))
        {
            /* the sample entry: its size and name, then 0x52 bytes with the
             * picture's width and height, then its boxes */
            const uint8_t *e = b + o + 16;
            long en = be32(e);
            if (en > size - 16) return;
            width = be16(e + 8 + 24);
            height = be16(e + 8 + 26);
            for (long p = 8 + 0x52; p + 8 <= en; )
            {
                long ps = be32(e + p);
                if (ps < 8 || p + ps > en) break;
                if (box_is(e + p, "CMP1") && ps >= 8 + 32) cmp = e + p + 8;
                p += ps;
            }
        }
        if (depth == 4 && box_is(b + o, "co64") && size >= 24 && be32(b + o + 12) == 1 && !be32(b + o + 16))
            offset = (long) be32(b + o + 20);
        o += size;
    }
    if (depth != 4 || !cmp || offset < 0) return;
    /* the largest: the sensor's mosaic, not the small one beside it */
    if (width * height <= c->raw_w * c->raw_h) return;
    c->raw_w = (int) be32(cmp + 8);
    c->raw_h = (int) be32(cmp + 12);
    c->tile_w = (int) be32(cmp + 16);
    c->tile_h = (int) be32(cmp + 20);
    c->version = be16(cmp + 4);
    c->bits = cmp[24];
    c->planes = cmp[25] >> 4;
    c->cfa = cmp[25] & 15;
    c->enc = cmp[26] >> 4;
    c->levels = cmp[26] & 15;
    c->hdr_size = (int) be32(cmp + 28);
    c->hdr_offset = (int) offset;
}

int fos_cr3_parse(void *f, struct fos_cr3 *c, const char **why)
{
    uint8_t h[24 + 8];
    memset(c, 0, sizeof(*c));
    c->orient = 1;
    c->crop_l = -1;
    if (fos_port_seek(f, 0) < 0 || fos_port_read(f, h, sizeof(h)) != (int) sizeof(h)) return 0;
    if (memcmp(h + 4, "ftypcrx ", 8)) return 0;
    long top = be32(h);
    if (top < 16 || top > 256) { *why = "damaged CR3 file"; return -1; }
    if (fos_port_seek(f, (int) top) < 0 || fos_port_read(f, h, 8) != 8 || !box_is(h, "moov"))
    {
        *why = "no moov box in this CR3";
        return -1;
    }
    long size = be32(h) - 8;
    if (size < 8 || size > 1024 * 1024) { *why = "damaged CR3 file"; return -1; }
    uint8_t *moov = fos_port_alloc((int) size);
    if (!moov) { *why = "out of memory (CR3 header)"; return -1; }
    if (fos_port_read(f, moov, (int) size) != size)
    {
        fos_port_free(moov);
        *why = "can't read the CR3 header";
        return -1;
    }
    for (long o = 0; o + 8 <= size; )
    {
        long n = be32(moov + o);
        if (n < 8 || o + n > size) break;
        if (box_is(moov + o, "uuid") && n > 24) parse_meta(c, moov + o + 24, n - 24);
        if (box_is(moov + o, "trak")) parse_track(c, moov + o + 8, n - 8, 1);
        o += n;
    }
    fos_port_free(moov);
    if (!c->raw_w) { *why = "no RAW in this CR3"; return -1; }
    if (c->version != 0x100 || c->planes != 4 || c->enc || c->cfa || c->bits < 10 || c->bits > 14
        || (c->raw_w & 1) || (c->raw_h & 1) || (c->tile_w & 1) || (c->tile_h & 1) || c->tile_w < 44 || c->tile_h < 44
        || c->tile_w > c->raw_w || c->tile_h > c->raw_h || c->raw_w > 16384 || c->raw_h > 16384 || c->hdr_size < 12)
    {
        *why = "this CR3's coding isn't supported";
        return -1;
    }
    if (c->levels) { *why = "C-RAW isn't supported yet: shoot RAW"; return -1; }
    return 1;
}

/* ---------------------------------------------------------------- reading bits */

struct bits
{
    void *f;
    uint8_t *buf;
    int cap, pos, len;
    int file_pos, file_left;
    uint32_t data;                      /* top aligned */
    int left;                           /* bits in it */
    int failed;
    int read_ms;
};

static void bits_start(struct bits *s, int offset, int size)
{
    s->pos = s->len = 0;
    s->file_pos = offset;
    s->file_left = size;
    s->data = 0;
    s->left = 0;
    s->failed = 0;
}

/* 1 when there are more bytes in the buffer; a read that fails is noted */
static __attribute__((noinline)) int refill(struct bits *s)
{
    int n = s->file_left < s->cap ? s->file_left : s->cap;
    if (n <= 0) return 0;
    const int t0 = fos_port_ms();
    if (fos_port_seek(s->f, s->file_pos) < 0 || fos_port_read(s->f, s->buf, n) != n)
    {
        s->failed = 1;
        return 0;
    }
    s->read_ms += fos_port_ms() - t0;
    s->file_pos += n;
    s->file_left -= n;
    s->pos = 0;
    s->len = n;
    return 1;
}

/* A plane's last word may be cut short: its last bits are all that is used,
 * so the rest reads as 0. Only a word with nothing left in it is an error. */
static __attribute__((noinline)) uint32_t word_slow(struct bits *s)
{
    uint32_t w = 0;
    int got = 0;
    for (int i = 0; i < 4; i++)
    {
        if (s->pos >= s->len && !refill(s)) continue;
        w |= (uint32_t) s->buf[s->pos++] << (24 - 8 * i);
        got++;
    }
    if (!got) s->failed = 1;
    return w;
}

FOS_INLINE uint32_t next_word(struct bits *s)
{
    if (s->pos + 4 > s->len) return word_slow(s);
    const uint8_t *p = s->buf + s->pos;
    s->pos += 4;
    return (uint32_t) p[0] << 24 | p[1] << 16 | p[2] << 8 | p[3];
}

FOS_INLINE uint32_t get_bits(struct bits *s, int n)
{
    if (s->left < n)
    {
        uint32_t w = next_word(s);
        int have = s->left, more = n - have;
        uint32_t r = (s->data | w >> have) >> (32 - n);
        s->data = w << more;
        s->left = 32 - more;
        return r;
    }
    uint32_t r = s->data >> (32 - n);
    s->data <<= n;
    s->left -= n;
    return r;
}

/* the zeros before the next 1, which goes too */
static __attribute__((noinline)) int zeros_slow(struct bits *s)
{
    int count = s->left;
    for (;;)
    {
        uint32_t w = next_word(s);
        if (s->failed) return 0;
        if (w)
        {
            int z = __builtin_clz(w);
            s->data = z < 31 ? w << (z + 1) : 0;
            s->left = 31 - z;
            return count + z;
        }
        count += 32;
    }
}

FOS_INLINE int get_zeros(struct bits *s)
{
    if (s->data)
    {
        int z = __builtin_clz(s->data);
        s->data <<= z + 1;              /* left < 32: z < 31 */
        s->left -= z + 1;
        return z;
    }
    return zeros_slow(s);
}

/* ---------------------------------------------------------------- a plane's lines */

struct band
{
    struct bits *bs;
    int width, height, line;
    int k, s;                           /* the Rice parameter, the run parameter */
    int32_t *lines, *l0, *l1;           /* the line before, this line; 1 before their first sample */
};

static const uint8_t RUN_STEP[32] = { 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3,
                                      4, 4, 5, 5, 6, 6, 7, 7, 8, 9, 10, 11, 12, 13, 14, 15 };
#define RUN_SIZE(s) (1 << RUN_STEP[s])

FOS_INLINE int next_k(int k, int code)
{
    int n = k - (code < (1 << k >> 1)) + ((code >> k) > 2) + ((code >> k) > 5);
    return n < 15 ? n : 15;
}

FOS_INLINE uint32_t get_code(struct bits *s, int k)
{
    uint32_t code = (uint32_t) get_zeros(s);
    if (code >= 41) return get_bits(s, 21);
    return k ? get_bits(s, k) | code << k : code;
}

FOS_INLINE int32_t code_value(uint32_t code)
{
    return -(int32_t) (code & 1) ^ (int32_t) (code >> 1);
}

/* how many samples repeat, of at most length; < 0 damaged */
static int get_run(struct band *b, int length)
{
    struct bits *bs = b->bs;
    if (!get_bits(bs, 1)) return 0;
    int n = 1;
    while (get_bits(bs, 1))
    {
        n += RUN_SIZE(b->s);
        if (n > length) return length;
        if (b->s < 31) b->s++;
        if (n == length) return length;
    }
    if (RUN_STEP[b->s]) n += (int) get_bits(bs, RUN_STEP[b->s]);
    if (b->s > 0) b->s--;
    return n > length ? -1 : n;
}

/* the first line: each sample from the one before it */
static int line_top(struct band *b)
{
    struct bits *bs = b->bs;
    int32_t *p = b->l1;
    int k = b->k;
    int length = b->width;
    p[0] = 0;
    for (; length > 1; length--)
    {
        if (p[0])
            p[1] = p[0];
        else
        {
            int n = get_run(b, length);
            if (n < 0) return -1;
            length -= n;
            while (n-- > 0) { p[1] = p[0]; p++; }
            if (length <= 0) break;
            p[1] = 0;
        }
        uint32_t code = get_code(bs, k);
        p[1] += code_value(code);
        k = next_k(k, (int) code);
        p++;
    }
    if (length == 1)
    {
        p[1] = p[0];
        uint32_t code = get_code(bs, k);
        p[1] += code_value(code);
        k = next_k(k, (int) code);
        p++;
    }
    p[1] = p[0] + 1;
    b->k = k;
    return 0;
}

/* a sample from the line before and the one to its left: p0, p1 at the
 * left neighbours; ahead: there is a sample after it, to help choose k */
#define SYMBOL(predict, ahead) do { \
        int32_t pred = p0[1]; \
        if (predict) \
        { \
            int32_t delta = p0[1] - p0[0], left = p1[0]; \
            int neg = delta < 0; \
            int i = ((p0[0] < left) ^ neg) << 1 | ((left < p0[1]) ^ neg); \
            pred = i < 2 ? left + delta : i == 2 ? left : p0[1]; \
        } \
        uint32_t code = get_code(bs, k); \
        p1[1] = pred + code_value(code); \
        if (ahead) \
        { \
            int32_t d = (p0[2] - p0[1]) * 2; \
            code = (code + (uint32_t) (d < 0 ? -d : d)) >> 1; \
            p0++; \
        } \
        k = next_k(k, (int) code); \
        p1++; \
    } while (0)

/* Nearly all the time goes here, a sample at a time: the reader's state stays in
 * registers (from memory for each bit read, this was far slower) and goes back to
 * its struct only for the rare slower paths. The buffer is read in fours from its
 * start, so a word is on a word's boundary. */
typedef uint32_t __attribute__((may_alias)) aliased_word;

#define BITS_SAVE() (bs->data = data, bs->left = left, bs->pos = (int) (rp - bs->buf))
#define BITS_LOAD() (data = bs->data, left = bs->left, rp = bs->buf + bs->pos, re = bs->buf + bs->len)

#define GET_BITS(out, n) do { \
        if (left < (n)) \
        { \
            uint32_t w; \
            if (rp + 4 <= re) { w = __builtin_bswap32(*(const aliased_word *) rp); rp += 4; } \
            else { BITS_SAVE(); w = word_slow(bs); BITS_LOAD(); } \
            const int more = (n) - left; \
            (out) = (data | w >> left) >> (32 - (n)); \
            data = w << more; \
            left = 32 - more; \
        } \
        else \
        { \
            (out) = data >> (32 - (n)); \
            data <<= (n); \
            left -= (n); \
        } \
    } while (0)

/* get_code, on the registers */
#define GET_CODE(code) do { \
        if (data) \
        { \
            const int z = __builtin_clz(data); \
            data <<= z + 1; \
            left -= z + 1; \
            (code) = (uint32_t) z; \
        } \
        else { BITS_SAVE(); (code) = (uint32_t) zeros_slow(bs); BITS_LOAD(); } \
        if ((code) >= 41) GET_BITS(code, 21); \
        else if (k) { uint32_t low_; GET_BITS(low_, k); (code) = low_ | (code) << k; } \
    } while (0)

/* a sample from the line before and the one to its left, as SYMBOL */
#define SYMBOL_FAST(predict, ahead) do { \
        int32_t pred = p0[1]; \
        if (predict) \
        { \
            int32_t delta = p0[1] - p0[0], left_ = p1[0]; \
            int neg = delta < 0; \
            int i = ((p0[0] < left_) ^ neg) << 1 | ((left_ < p0[1]) ^ neg); \
            pred = i < 2 ? left_ + delta : i == 2 ? left_ : p0[1]; \
        } \
        uint32_t code; \
        GET_CODE(code); \
        p1[1] = pred + code_value(code); \
        if (ahead) \
        { \
            int32_t d = (p0[2] - p0[1]) * 2; \
            code = (code + (uint32_t) (d < 0 ? -d : d)) >> 1; \
            p0++; \
        } \
        k = next_k(k, (int) code); \
        p1++; \
    } while (0)

static int line_next(struct band *b)
{
    struct bits *bs = b->bs;
    int32_t *p0 = b->l0, *p1 = b->l1;
    int k = b->k;
    int length = b->width;
    uint32_t data;
    int left;
    const uint8_t *rp, *re;
    BITS_LOAD();
    p1[0] = p0[1];
    for (; length > 1; length--)
    {
        if (p1[0] != p0[1] || p1[0] != p0[2])
            SYMBOL_FAST(1, 1);
        else
        {
            BITS_SAVE();
            int n = get_run(b, length);
            BITS_LOAD();
            if (n < 0) return -1;
            length -= n;
            p0 += n;
            while (n-- > 0) { p1[1] = p1[0]; p1++; }
            if (length > 0) SYMBOL_FAST(0, length > 1);
        }
    }
    if (length == 1) SYMBOL_FAST(1, 0);
    p1[1] = p1[0] + 1;
    BITS_SAVE();
    b->k = k;
    return 0;
}

static void band_start(struct band *b, int width, int height)
{
    b->width = width;
    b->height = height;
    b->line = 0;
    b->k = b->s = 0;
    memset(b->lines, 0, (size_t) (width + 2) * 2 * sizeof(int32_t));
}

/* the band's next line, or 0 if damaged */
static const int32_t *band_line(struct band *b)
{
    int32_t *a = b->lines, *z = b->lines + b->width + 2;
    b->l0 = b->line & 1 ? z : a;
    b->l1 = b->line & 1 ? a : z;
    if ((b->line ? line_next(b) : line_top(b)) < 0 || b->bs->failed) return 0;
    b->line++;
    return (b->line & 1 ? z : a) + 1;
}

/* ---------------------------------------------------------------- the frame */

struct tile
{
    int x0, y0, w, h;                   /* in a plane */
    int offset[4], size[4];             /* each plane's coded lines */
};

/* Each tile's plane is coded on its own and they lie one after the other in the
 * file, but they are decoded side by side, a row of all of them at a time. One after
 * another would sweep the whole frame in memory once for each of them. */
struct stream
{
    struct bits bs;
    struct band band;
};

struct fos_crx
{
    struct fos_cr3 c;
    struct tile *tiles;
    int ntiles, cols, tile_rows;
    struct stream *streams;
    int tile_row, row, col, plane;      /* the next line */
    const char *err;
    uint16_t *out;
    int32_t *lines;
    uint8_t *buf;
};

static int fail_open(struct fos_crx *x, const char **why, const char *msg)
{
    *why = msg;
    fos_crx_close(x);
    return 0;
}

/* tile 0.., each: its header, then its planes' */
static int parse_headers(struct fos_crx *x, const uint8_t *h, int size)
{
    int pw = x->c.raw_w / 2, ph = x->c.raw_h / 2, tw = x->c.tile_w / 2, th = x->c.tile_h / 2;
    int cols = (pw + tw - 1) / tw, rows = (ph + th - 1) / th;
    x->ntiles = cols * rows;
    x->cols = cols;
    x->tile_rows = rows;
    if (x->ntiles > 64) return 0;
    x->tiles = fos_port_alloc(x->ntiles * (int) sizeof(struct tile));
    if (!x->tiles) return 0;
    int p = 0, base = x->c.hdr_offset + x->c.hdr_size, tile_at = 0;
    for (int i = 0; i < x->ntiles; i++)
    {
        struct tile *t = &x->tiles[i];
        int col = i % cols, row = i / cols;
        t->x0 = col * tw; t->y0 = row * th;
        t->w = col == cols - 1 ? pw - tw * (cols - 1) : tw;
        t->h = row == rows - 1 ? ph - th * (rows - 1) : th;
        if (p + 12 > size || be16(h + p) != 0xFF01 || be16(h + p + 2) != 8 || be16(h + p + 8) != i) return 0;
        int tile_size = (int) be32(h + p + 4);
        p += 12;
        int plane_at = tile_at;
        for (int c = 0; c < x->c.planes; c++)
        {
            if (p + 24 > size || be16(h + p) != 0xFF02 || be16(h + p + 2) != 8 || h[p + 8] >> 4 != c) return 0;
            int plane_size = (int) be32(h + p + 4), flags = h[p + 8];
            if (!(flags & 8) || (flags >> 1 & 3)) return 0;     /* other codings: not on this camera */
            p += 12;
            if (be16(h + p) != 0xFF03 || be16(h + p + 2) != 8 || h[p + 8] >> 4) return 0;
            int band_size = (int) be32(h + p + 4);
            uint32_t bits = be32(h + p + 8);
            t->offset[c] = base + plane_at;
            t->size[c] = band_size - (int) (bits & 0x7FFFF);
            p += 12;
            plane_at += plane_size;
        }
        tile_at += tile_size;
    }
    return 1;
}

struct fos_crx *fos_crx_open(void *f, const struct fos_cr3 *c, const char **why)
{
    struct fos_crx *x = fos_port_alloc(sizeof(*x));
    if (!x) { *why = "out of memory (CR3)"; return 0; }
    memset(x, 0, sizeof(*x));
    x->c = *c;
    uint8_t *h = fos_port_alloc(c->hdr_size);
    if (!h) { fail_open(x, why, "out of memory (CR3 header)"); return 0; }
    int ok = fos_port_seek(f, c->hdr_offset) >= 0 && fos_port_read(f, h, c->hdr_size) == c->hdr_size
             && parse_headers(x, h, c->hdr_size);
    fos_port_free(h);
    if (!ok) { fail_open(x, why, "damaged CR3 tile headers"); return 0; }
    int width = 0;
    for (int i = 0; i < x->ntiles; i++)
        if (x->tiles[i].w > width) width = x->tiles[i].w;
    const int nstreams = x->ntiles * c->planes, nlines = (width + 2) * 2;
    int cap = READ_TOTAL / nstreams;
    cap = (cap < READ_SIZE ? cap : READ_SIZE) & ~3;     /* whole words: see line_next */
    x->streams = fos_port_alloc(nstreams * (int) sizeof(struct stream));
    x->lines = fos_port_alloc(nstreams * nlines * (int) sizeof(int32_t));
    x->out = fos_port_alloc(width * (int) sizeof(uint16_t));
    x->buf = fos_port_alloc(nstreams * cap);
    if (!x->streams || !x->lines || !x->out || !x->buf) { fail_open(x, why, "out of memory (CR3 lines)"); return 0; }
    memset(x->streams, 0, nstreams * sizeof(struct stream));
    for (int i = 0; i < nstreams; i++)
    {
        struct stream *st = &x->streams[i];
        const struct tile *t = &x->tiles[i / c->planes];
        st->bs.f = f;
        st->bs.buf = x->buf + i * cap;
        st->bs.cap = cap;
        st->band.bs = &st->bs;
        st->band.lines = x->lines + i * nlines;
        bits_start(&st->bs, t->offset[i % c->planes], t->size[i % c->planes]);
        band_start(&st->band, t->w, t->h);
    }
    return x;
}

int fos_crx_next(struct fos_crx *x, struct fos_crx_line *line)
{
    if (x->tile_row >= x->tile_rows) return 0;
    const int tile = x->tile_row * x->cols + x->col;
    const struct tile *t = &x->tiles[tile];
    const int32_t *in = band_line(&x->streams[tile * x->c.planes + x->plane].band);
    if (!in) { x->err = "damaged CR3 data"; return -1; }
    const int bits = x->c.bits, median = 1 << (bits - 1), top = (1 << bits) - 1;
    uint16_t *out = x->out;
    for (int i = 0; i < t->w; i++)
    {
        int v = median + in[i];
        out[i] = (uint16_t) (v < 0 ? 0 : v > top ? top : v);
    }
    /* planes 0..3 sit at R G / G B of the mosaic's 2 x 2 */
    line->v = out;
    line->n = t->w;
    line->x0 = 2 * t->x0 + (x->plane & 1);
    line->y = 2 * (t->y0 + x->row) + (x->plane >> 1);
    if (++x->plane == x->c.planes)
    {
        x->plane = 0;
        if (++x->col == x->cols)
        {
            x->col = 0;
            if (++x->row == t->h)
            {
                x->row = 0;
                x->tile_row++;
            }
        }
    }
    return 1;
}

int fos_crx_read_ms(const struct fos_crx *x)
{
    int ms = 0;
    for (int i = 0; i < x->ntiles * x->c.planes; i++) ms += x->streams[i].bs.read_ms;
    return ms;
}

const char *fos_crx_error(const struct fos_crx *x)
{
    return x->err ? x->err : "damaged CR3 data";
}

void fos_crx_close(struct fos_crx *x)
{
    if (!x) return;
    fos_port_free(x->tiles);
    fos_port_free(x->streams);
    fos_port_free(x->lines);
    fos_port_free(x->out);
    fos_port_free(x->buf);
    fos_port_free(x);
}
