/* the film OS's card on a PC: a folder (FOS_CARD, else out/card/) and stdio */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dirent.h>
#include <sys/stat.h>
#include <unistd.h>
#include "../fos.h"

void *fos_port_open(const char *path, int write) { return fopen(path, write ? "wb" : "rb"); }
/* FOS_SLOW_CARD: microseconds a read takes */
int  fos_port_read(void *f, void *buf, int size)
{
    const char *slow = getenv("FOS_SLOW_CARD");
    if (slow) usleep(atoi(slow));
    return (int) fread(buf, 1, size, f);
}
int  fos_port_writes;
int  fos_port_write(void *f, const void *buf, int size) { fos_port_writes++; return (int) fwrite(buf, 1, size, f); }
int  fos_port_seek(void *f, int offset) { return fseek(f, offset, SEEK_SET); }
void fos_port_close(void *f) { if (f) fclose(f); }

#ifdef CONFIG_R
#include <pthread.h>

struct other_core { void (*fn)(void *); void *arg; };

static void *other_core_thread(void *p)
{
    const struct other_core c = *(struct other_core *) p;
    free(p);
    c.fn(c.arg);
    return 0;
}

/* FOS_ONE_CORE: as when the task can't be had */
int fos_port_other_core(void (*fn)(void *), void *arg)
{
    if (getenv("FOS_ONE_CORE")) return 0;
    struct other_core *c = malloc(sizeof(*c));
    pthread_t t;
    if (!c) return 0;
    c->fn = fn;
    c->arg = arg;
    if (pthread_create(&t, 0, other_core_thread, c)) { free(c); return 0; }
    pthread_detach(t);
    return 1;
}
#endif

const char *fos_port_model() { return "Canon EOS M"; }

const char *fos_port_card()
{
    const char *c = getenv("FOS_CARD");
    return c ? c : "out/card/";
}

int fos_port_list(const char *dir, int (*fn)(void *ctx, const char *name, int is_dir), void *ctx)
{
    /* FOS_LIST_LIMIT: as a camera whose listings run out, which is what the test body seemed to do */
    static int calls;
    const char *limit = getenv("FOS_LIST_LIMIT");
    if (limit && ++calls > atoi(limit)) return -1;
    DIR *d = opendir(dir);
    if (!d) return -1;
    struct dirent *e;
    int n = 0;
    while ((e = readdir(d)))
    {
        if (e->d_name[0] == '.') continue;
        char path[512];
        struct stat st;
        snprintf(path, sizeof(path), "%s/%s", dir, e->d_name);
        int is_dir = !stat(path, &st) && S_ISDIR(st.st_mode);
        n++;
        if (!fn(ctx, e->d_name, is_dir)) break;
    }
    closedir(d);
    return n;
}

int fos_port_mkdir(const char *path)
{
    struct stat st;
    if (!stat(path, &st)) return S_ISDIR(st.st_mode) ? 0 : -1;
    /* FOS_SHORT_NAMES: a card that takes 8.3 names only */
    const char *name = strrchr(path, '/');
    name = name ? name + 1 : path;
    if (getenv("FOS_SHORT_NAMES") && (strlen(name) > 8 || strchr(name, ' '))) return -1;
    return mkdir(path, 0777);
}
