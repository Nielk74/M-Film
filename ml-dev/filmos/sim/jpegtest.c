/* jpegtest: the EOS R's JPEG writer alone, on a picture from a test.
 *
 *   ./jpegtest in.rgb width height out.jpg
 */

#include <stdio.h>
#include <stdlib.h>
#include "../fos.h"
#include "../fos_jpeg.h"

extern int fos_port_writes;

void *fos_port_alloc(int size) { return malloc(size); }
void fos_port_free(void *p) { free(p); }
void fos_port_sleep(int ms) {}
void fos_port_log(const char *msg) { fprintf(stderr, "%s\n", msg); }

int main(int argc, char **argv)
{
    if (argc != 5) return 2;
    const int width = atoi(argv[2]), height = atoi(argv[3]);
    uint8_t *rgb = malloc(3 * width * height);
    FILE *f = fopen(argv[1], "rb");
    if (!rgb || !f || fread(rgb, 3, width * height, f) != (size_t) (width * height)) return 2;
    fclose(f);
    if (!fos_jpeg_write(argv[4], rgb, 3 * width, width, height, 92, 0)) return 1;
    printf("writes %d\n", fos_port_writes);
    return 0;
}
