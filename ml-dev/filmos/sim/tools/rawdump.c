/* rawdump: LibRaw's sensor samples for a RAW file, the ground truth for our
 * decoders: gcc rawdump.c -lraw && ./a.out in.CR3 out.u16 */

#include <libraw/libraw.h>
#include <stdio.h>

int main(int argc, char **argv)
{
    if (argc != 3) { fprintf(stderr, "usage: rawdump in out.u16\n"); return 2; }
    libraw_data_t *r = libraw_init(0);
    int e = libraw_open_file(r, argv[1]);
    if (!e) e = libraw_unpack(r);
    if (e) { fprintf(stderr, "%s: %s\n", argv[1], libraw_strerror(e)); return 1; }
    if (!r->rawdata.raw_image) { fprintf(stderr, "%s: no mosaic\n", argv[1]); return 1; }
    FILE *f = fopen(argv[2], "wb");
    if (!f) { perror(argv[2]); return 1; }
    fwrite(r->rawdata.raw_image, 2, (size_t) r->sizes.raw_width * r->sizes.raw_height, f);
    fclose(f);
    printf("%d %d\n", r->sizes.raw_width, r->sizes.raw_height);
    printf("cam_mul %f %f %f %f\n", r->color.cam_mul[0], r->color.cam_mul[1], r->color.cam_mul[2], r->color.cam_mul[3]);
    libraw_close(r);
    return 0;
}
