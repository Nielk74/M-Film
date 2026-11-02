/* filmroll: a Canon camera as a film camera (built into this ML fork).
 * Made on the EOS M; 100D, 650D, 700D and others build from the same code.
 *
 * The camera side of the film OS (ml-dev/filmos): rolls, the roll log the
 * PC lab reads, film settings, the viewfinder HUD, and the fos_port_*
 * functions through which the film OS takes the screen and the buttons.
 *
 *  - the film OS is the camera's own interface: the film shelf at start
 *    up, home and its settings pages instead of the Magic Lantern menu.
 *    The ML menu is gone: what used to open it (two-finger tap, long
 *    press on DOWN, modules) opens home.
 *  - an SLR-viewfinder HUD in LiveView: aperture scale with meter needle,
 *    red LED readout that lights on half-shutter, frame counter, wind
 *  - PLAY opens the prints the camera has developed, never Canon's
 *    playback of undeveloped frames
 *  - a finished roll is developed in the camera (fos_dev.c): its RAWs
 *    become prints in ML/DARKROOM, shown on their own palettes
 *  - manual lenses: the f-stop is dialled in and logged for the lab
 *
 * Loading a roll sets picture style, contrast / saturation / colour tone,
 * white balance and ISO for that stock, and starts a roll log next to the
 * images (DCIM/xxxCANON/ROLL_nnn.TXT) that the PC film lab reads
 * (the desktop darkroom, Darkrooms). Film keys must match films/films.py.
 *
 * Shots are detected by watching the file number, so every frame is
 * logged even in burst mode.
 */

#include <dryos.h>
#include <module.h>
#include <property.h>
#include <menu.h>
#include <config.h>
#include <lens.h>
#include <picstyle.h>
#include <beep.h>
#include <bmp.h>
#include <fio-ml.h>
#include <fileprefix.h>
#include <notify_box.h>
#include <zebra.h>
#include <propvalues.h>
#include <gui-common.h>
#include <tasks.h>
#include <mem.h>
#include <powersave.h>
#include "filmroll.h"
#include "fos.h"
#include "fos_dev.h"
#ifdef FEATURE_VRAM_RGBA
#include "fos_rgba.h"
#endif

#include <stdarg.h>

extern int ml_started;
extern void CancelDateTimer();

/* user settings */
static CONFIG_INT("filmroll.film",      film_sel,         0);
static CONFIG_INT("filmroll.exp",       exp_sel,          0);
#ifdef CONFIG_R
static CONFIG_INT("filmroll.hud",       hud_enabled,      0);
#else
static CONFIG_INT("filmroll.hud",       hud_enabled,      1);
#endif
static CONFIG_INT("filmroll.focus",     focus_screen,     1);
static CONFIG_INT("filmroll.leds",      leds_on_half,     1);
static CONFIG_INT("filmroll.wind",      wind_mode,        1);   /* 0 off, 1 reminder, 2 block shutter */
#ifdef CONFIG_R
static CONFIG_INT("filmroll.playlock",  play_lock,        0); /* still experimental: keep Canon's playback */
#else
static CONFIG_INT("filmroll.playlock",  play_lock,        1);
#endif
#ifdef CONFIG_R
static CONFIG_INT("filmroll.startup",   startup_picker,   0);
static CONFIG_INT("filmroll.bootanim",  boot_anim,        0);
#else
static CONFIG_INT("filmroll.startup",   startup_picker,   2);   /* 0 off, 1 when no film, 2 always */
static CONFIG_INT("filmroll.bootanim",  boot_anim,        1);
#endif
static CONFIG_INT("filmroll.debug",     debug_log,        1);
static CONFIG_INT("filmroll.mode",      film_mode,        0);   /* 0 Basic, 1 Realistic (FS_MODE) */
static CONFIG_INT("filmroll.lens_wide", lens_widest,      6);   /* index into fos_fstops: f/1.8 */
static CONFIG_INT("filmroll.fstop",     manual_fstop,     6);
static CONFIG_INT("filmroll.ap_keys",   aperture_keys,    0);   /* 0 wheel in Av, 1 up/down, 2 menu only */
static CONFIG_INT("filmroll.scr_std",   scr_std,          0);   /* screen colour: 0 Canon, 1 Rec.709 */
static CONFIG_INT("filmroll.scr_sat",   scr_sat,          0);   /* -6..6 */
static CONFIG_INT("filmroll.scr_warm",  scr_warm,         0);
static CONFIG_INT("filmroll.scr_bright", scr_bright,      0);

/* roll state, kept across power cycles */
static CONFIG_INT("filmroll.loaded",    roll_loaded,      0);
static CONFIG_INT("filmroll.roll",      roll_number,      0);
static CONFIG_INT("filmroll.roll_film", roll_film,        0);
static CONFIG_INT("filmroll.roll_exp",  roll_exposures,   36);
static CONFIG_INT("filmroll.shot",      frames_shot,      0);
static CONFIG_INT("filmroll.lastfile",  last_file_number, -1);
static CONFIG_INT("filmroll.logdir",    roll_folder,      0);
static CONFIG_INT("filmroll.needwind",  need_wind,        0);

static char log_path[64];

static volatile int leds_until = 0;     /* keep LEDs lit until this ms clock */
static volatile int hud_dirty = 1;

#define ROLL_FINISHED (roll_loaded && frames_shot >= roll_exposures)
#define ROLL_IN_PROGRESS (roll_loaded && frames_shot < roll_exposures)

static volatile int end_due = 0;        /* the roll was just finished: ms when to ask about it */
static volatile int dev_busy = 0;       /* developing: keep the camera awake */

/* the last frames, for the roll page (also read back from the log) */
static struct { int no; char time[6]; char fstop[6]; } recent[FOS_RECENT];
static int nrecent = 0;
static char loaded_at[20];
static int recent_valid = 0;

/* ---------------------------------------------------------------- debug log */

/* field-test trace in ML/FILMROLL.LOG; turn off in System once things work */
static void dbg(const char *fmt, ...)
{
    if (!debug_log)
        return;

    char buf[192];
    int n = snprintf(buf, sizeof(buf), "%7d  ", get_ms_clock());
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf + n, sizeof(buf) - n, fmt, ap);
    va_end(ap);

    FILE *f = FIO_CreateFileOrAppend("ML/FILMROLL.LOG");
    if (!f)
        return;
    my_fprintf(f, "%s\n", buf);
    FIO_CloseFile(f);
}

/* The camera waiting for a picture, the screen the film OS opens from.
 * On the EOS M that is LiveView; a DSLR (100D, 650D, 700D ...) is mostly
 * shot through its optical viewfinder, so there it is also Canon's shooting
 * screen with LiveView off. */
#if defined(CONFIG_EOSM) || defined(CONFIG_R)
#define FOS_LV_ONLY 1
#else
#define FOS_LV_ONLY 0
#endif
static int shooting_screen()
{
    return (lv || !FOS_LV_ONLY) && display_idle() && !is_movie_mode();
}

#define DBG_STATE(what) dbg("%s: ml=%d lv=%d idle=%d movie=%d mode=%d gui=%d", what, \
                            ml_started, lv, display_idle(), is_movie_mode(), shooting_mode, CURRENT_GUI_MODE)

/* ---------------------------------------------------------------- log file */

static void update_log_path()
{
    snprintf(log_path, sizeof(log_path), "%s:/DCIM/%03d%s/ROLL_%03d.TXT",
             get_shooting_card()->drive_letter, roll_folder, get_dcim_dir_suffix(),
             roll_number);
}

static void log_append(const char *text)
{
    FILE *f = FIO_CreateFileOrAppend(log_path);
    if (!f)
    {
        NotifyBox(3000, "filmroll: can't write %s", log_path);
        return;
    }
    my_fprintf(f, "%s", text);
    FIO_CloseFile(f);
}

static void format_now(char *buf, int size)
{
    struct tm now;
    LoadCalendarFromRTC(&now);
    snprintf(buf, size, "%04d-%02d-%02d %02d:%02d:%02d",
             now.tm_year + 1900, now.tm_mon + 1, now.tm_mday,
             now.tm_hour, now.tm_min, now.tm_sec);
}

static void write_header(const char *note)
{
    char when[32];
    char header[320];
    format_now(when, sizeof(when));
    snprintf(header, sizeof(header),
             "# ML film mode roll log (filmroll module)%s\n"
             "roll = %03d\nfilm = %s\nexposures = %d\nloaded = %s\n\n",
             note, roll_number, fos_films[COERCE(roll_film, 0, fos_nfilms - 1)].key, roll_exposures, when);
    log_append(header);
}

/* the roll page wants the loading time and the last frames: after a power
 * cycle they only exist in the log, so read them back once */
static void read_back_log()
{
    recent_valid = 1;
    nrecent = 0;
    loaded_at[0] = 0;
    int size = 0;
    char *buf = (char *) read_entire_file(log_path, &size);
    if (!buf)
        return;
    for (char *line = buf; line && *line; )
    {
        char *next = strchr(line, '\n');
        if (next) *next++ = 0;
        if (!strncmp(line, "loaded = ", 9))
            fos_copy(loaded_at, sizeof(loaded_at), line + 9, 16);
        else if (line[0] >= '0' && line[0] <= '9')
        {
            /* "12  IMG_0012.CR2  2026-08-08 09:15:15  f/2.8" */
            char *tok[5];
            int n = 0;
            for (char *p = line; *p && n < 5; )
            {
                while (*p == ' ') p++;
                if (!*p) break;
                tok[n++] = p;
                while (*p && *p != ' ') p++;
                if (*p) *p++ = 0;
            }
            if (n >= 4)
            {
                /* newest first, keep FOS_RECENT */
                for (int i = MIN(nrecent, FOS_RECENT - 1); i > 0; i--)
                    recent[i] = recent[i - 1];
                recent[0].no = atoi(tok[0]);
                fos_copy(recent[0].time, sizeof(recent[0].time), tok[3], 5);
                snprintf(recent[0].fstop, sizeof(recent[0].fstop), "%s",
                         n >= 5 && !strncmp(tok[4], "f/", 2) ? tok[4] + 2 : "?");
                nrecent = MIN(nrecent + 1, FOS_RECENT);
            }
        }
        line = next;
    }
    fio_free(buf);
}

/* ---------------------------------------------------------------- camera settings */

static int iso_to_raw(int iso)
{
    int raw = 72;                       /* ISO 100 */
    for (int i = 100; i < iso; i *= 2)
        raw += 8;                       /* one stop */
    return raw;
}

#ifdef CONFIG_R
/* The EOS R core can't write ISO or white balance: ask for them.
 * One short line, since NotifyBox neither wraps nor takes a newline. */
static void manual_settings_prompt(int index)
{
    const struct fos_film *f = &fos_films[COERCE(index, 0, fos_nfilms - 1)];
    /* the films develop at the shot's white balance (ml-dev/films/eos_r): any will do */
    NotifyBox(6000, "Canon menu: ISO %d, any WB", f->iso);
}

/* NotifyBox shows one message: when the capture check's notice replaced
 * the prompt, show it again at this time (0: nothing pending) */
static int manual_prompt_due = 0;
#endif

static void apply_film(int index)
{
    const struct fos_film *f = &fos_films[COERCE(index, 0, fos_nfilms - 1)];

    int ps = f->picstyle;
#ifdef CONFIG_R
    if (!prop_request_change_wait(PROP_PICTURE_STYLE, &ps, 4, 1000))
    {
        dbg("film: Picture Style request timed out; preview controls not changed");
        NotifyBox(5000, "Film preview not applied: Picture Style change timed out");
        return;
    }
#else
    prop_request_change_wait(PROP_PICTURE_STYLE, &ps, 4, 1000);
#endif
    msleep(100);

    /* these change the current picture style, so it must be selected first */
#ifdef CONFIG_R
    /* The newer EOS R core handles the six-slider DIGIC 8 properties.
     * Keep its existing property whitelist; ISO and WB remain manual. */
    picstyle_set_current_contrast(f->contrast);
    msleep(50);
    picstyle_set_current_sharpness(f->sharpness);
    msleep(50);
    if (f->picstyle != PS_MONO)
    {
        picstyle_set_current_saturation(f->saturation);
        msleep(50);
        picstyle_set_current_color_tone(f->color_tone);
        msleep(50);
    }
    manual_settings_prompt(index);
#else
    lens_set_contrast(f->contrast);
    msleep(50);
    lens_set_sharpness(f->sharpness);
    msleep(50);
    if (f->picstyle != PS_MONO)
    {
        lens_set_saturation(f->saturation);
        msleep(50);
        lens_set_color_tone(f->color_tone);
        msleep(50);
    }

    lens_set_kelvin(f->kelvin);
    lens_set_wbs_gm(f->wbs_gm);
    lens_set_wbs_ba(f->wbs_ba);
    lens_set_rawiso(iso_to_raw(f->iso));
#endif
}

/* Film is shot blind: the darkroom develops the RAWs, and there is no
 * image review to peek at. ML doesn't change these Canon settings itself
 * (writing the picture quality property can upset Canon's firmware), so
 * it asks for them. Returns 1 when it put up a notice. */
static int film_capture_check()
{
    if (!roll_loaded) return 0;
#ifdef CONFIG_R
    /* its quality property is not the older cameras' (RAW+JPEG reads
     * 0x4030700): the darkroom says so if a frame can't be developed */
    int raw = 1;
#else
    int raw = (pic_quality & 0xFE00FF) == (PICQ_RAW & 0xFE00FF);
#endif
    int review_off = image_review_time == 0;
    if (raw && review_off) return 0;
    dbg("capture: quality %x, review %d", pic_quality, image_review_time);
    NotifyBox(6000, "Film: in Canon's menu set %s%s%s", raw ? "" : "Image quality RAW",
              !raw && !review_off ? " and " : "", review_off ? "" : "Image review Off");
    return 1;
}

/* ---------------------------------------------------------------- aperture */

/* manual (adapted) lenses report no aperture; the user dials it in instead */
static int lens_is_manual()
{
    return !lens_info.raw_aperture;
}

/* index into fos_fstops for the aperture in use */
static int current_fstop()
{
    if (lens_is_manual())
        return COERCE(manual_fstop, lens_widest, fos_nfstops - 1);
#ifdef CONFIG_R
    /* not always reported here: below any real lens, and f/0.95 in the log */
    if (lens_info.aperture < 9)
        return COERCE(manual_fstop, lens_widest, fos_nfstops - 1);
#endif

    int f100 = lens_info.aperture * 10;         /* lens_info.aperture is f x10 */
    int best = 0;
    for (int i = 1; i < fos_nfstops; i++)
        if (ABS(fos_fstops[i] - f100) < ABS(fos_fstops[best] - f100))
            best = i;
    return best;
}

static void step_manual_fstop(int dir)
{
    manual_fstop = COERCE(current_fstop() + dir, lens_widest, fos_nfstops - 1);
}

/* ---------------------------------------------------------------- rolls */

/* start a roll: state and log now, camera settings once the film OS has
 * let go of the screen (apply_film blocks for a while) */
static void load_roll(int film, int exposures)
{
    roll_film = COERCE(film, 0, fos_nfilms - 1);
    roll_exposures = exposures;
    roll_folder = get_shooting_card()->folder_number;

    /* next roll number whose log doesn't exist yet, so a reset counter
     * (new settings file, card from another camera) never mixes two rolls */
    for (int tries = 0; tries < 999; tries++)
    {
        roll_number = roll_number % 999 + 1;
        update_log_path();
        if (!is_file(log_path))
            break;
    }
    frames_shot = 0;
    need_wind = 0;
    last_file_number = get_shooting_card()->file_number;
    roll_loaded = 1;
    update_log_path();
    write_header("");

    char when[32];
    format_now(when, sizeof(when));
    fos_copy(loaded_at, sizeof(loaded_at), when, 16);
    nrecent = 0;
    recent_valid = 1;

    hud_dirty = 1;
    dbg("roll %03d loaded: %s, %d exp", roll_number, fos_films[COERCE(roll_film, 0, fos_nfilms - 1)].key, roll_exposures);
}

static void rewind_roll()
{
    if (!roll_loaded) return;
    char when[32], line[80];
    format_now(when, sizeof(when));
    snprintf(line, sizeof(line), "\n# rewound %s after %d of %d frames\n", when, frames_shot, roll_exposures);
    log_append(line);
    roll_loaded = 0;
    need_wind = 0;
    hud_dirty = 1;
    dbg("roll %03d rewound at %d/%d", roll_number, frames_shot, roll_exposures);
}

static void record_frame(int file_number)
{
    if (frames_shot >= roll_exposures)
    {
        beep();
        return;
    }

    frames_shot++;

    char when[32];
    char aperture[8];
    char line[112];
    format_now(when, sizeof(when));
    fos_format_fstop(aperture, sizeof(aperture), current_fstop());
#ifdef CONFIG_R
    /* get_file_prefix() reads wrong here ("495A"): the darkroom goes by the
     * number if Canon's own name differs (_MG_, a custom prefix) */
    snprintf(line, sizeof(line), "%d  IMG_%04d.CR3  %s  f/%s\n",
             frames_shot, file_number, when, aperture);
#else
    snprintf(line, sizeof(line), "%d  %s%04d.CR2  %s  f/%s\n",
             frames_shot, get_file_prefix(), file_number, when, aperture);
#endif
    log_append(line);

    for (int i = MIN(nrecent, FOS_RECENT - 1); i > 0; i--)
        recent[i] = recent[i - 1];
    recent[0].no = frames_shot;
    fos_copy(recent[0].time, sizeof(recent[0].time), when + 11, 5);
    snprintf(recent[0].fstop, sizeof(recent[0].fstop), "%s", aperture);
    nrecent = MIN(nrecent + 1, FOS_RECENT);

    leds_until = get_ms_clock() + 2500;
    hud_dirty = 1;

    if (frames_shot == roll_exposures)
    {
        need_wind = 0;
        beep_times(2);
        end_due = get_ms_clock() + 1500;        /* then ask: develop it now? */
        return;
    }

    if (wind_mode)
        need_wind = 1;
}

/* log every new file number since the last poll */
static void poll_frames()
{
    if (!roll_loaded)
        return;

    int now = get_shooting_card()->file_number;
    if (now == last_file_number)
        return;

    int prev = last_file_number;
    last_file_number = now;

    int count = now - prev;
    if (count < 0)
        count += 9999;                  /* IMG_9999 -> IMG_0001 */
    if (prev < 0 || count > 50)
        return;                         /* card swap or counter reset: just resync */

    if (get_shooting_card()->folder_number != roll_folder)
    {
        /* Canon started a new folder; start a log there too so each folder
         * develops on its own (frame numbers keep counting) */
        roll_folder = get_shooting_card()->folder_number;
        update_log_path();
        write_header(" - continued from the previous DCIM folder");
    }

    dbg("frames: file %d -> %d, logging %d", prev, now, count);
    for (int i = 1; i <= count; i++)
        record_frame((prev + i - 1) % 9999 + 1);
}

/* ---------------------------------------------------------------- the film OS host */

static volatile int ui_running = 0;     /* the film OS owns the screen and the buttons */
static volatile int ui_abort = 0;
static int ui_after = 0;                /* what to do once it has let go */
static int ui_last_mode_poke = 0;
static int ui_last_palette = 0;

enum { AFTER_APPLY = 1, AFTER_CANON_MENU = 2 };

/* buttons, from the GUI task to the film OS task */
#define KEYQ 32
static volatile int keyq[KEYQ];
static volatile int keyq_in = 0, keyq_out = 0;

static void keyq_push(int k)
{
    int next = (keyq_in + 1) % KEYQ;
    if (next != keyq_out)
    {
        keyq[keyq_in] = k;
        keyq_in = next;
    }
}

/* Our palette is fos_pal (RGB); the bitmap overlay wants Y, Cb, Cr and
 * an opacity code, which we copy from Canon's own white. One entry is
 * remembered so we notice when other code puts Canon's palette back. */
#define PALETTE_PROBE 200
#ifdef FEATURE_VRAM_RGBA
const uint32_t * volatile filmroll_rgba_palette = 0;
#else
static uint32_t palette_probe = 0;
#endif

/* a photograph on the screen: its colours in 1..photo_n, at photo_k/256 */
static int photo_mode = 0, photo_n = 0, photo_k = 256;
static uint8_t photo_pal[FOS_PRINT_COLOURS][3];

static void palette_ours()
{
    if (!DISPLAY_IS_ON) return;
#ifdef FEATURE_VRAM_RGBA
    /* Publish a complete table in one pointer store. The display task
     * must never dereference the EOS R's deliberately NULL LCD_Palette. */
    static uint32_t tables[2][256];
    static unsigned bank;
    bank ^= 1;
    fos_rgba_build_palette(tables[bank], fos_pal,
        photo_mode ? photo_pal : 0, photo_n, photo_k,
        scr_std, scr_sat, scr_warm, scr_bright);
    filmroll_rgba_palette = tables[bank];
    ml_refresh_display_needed = 1;
#else
    /* Canon's white tells the opacity and where white sits in luma
     * (its own conversion puts it at 235, not 255) */
    uint32_t white = LCD_Palette[3 * COLOR_WHITE + 2];
    uint32_t opaque = white & 0xFF000000;
    if ((opaque >> 24) < 2) opaque = 0x03000000;
    int white_y = (white >> 16) & 0xFF;
    if (white_y < 180) white_y = 235;
    for (int i = 1; i < 255; i++)
    {
        uint32_t entry;
        if (!photo_mode)
            entry = opaque | fos_screen_yuv(i, white_y, scr_std, scr_sat, scr_warm, scr_bright);
        else
        {
            const uint8_t *c = i <= photo_n ? photo_pal[i - 1] :
                               i >= FOS_FIXED_BASE && i < FOS_FIXED_BASE + FOS_FIXED_N ? fos_fixed_rgb[i - FOS_FIXED_BASE] : 0;
            uint32_t rgb = c ? (uint32_t) (c[0] * photo_k >> 8) << 16 | (uint32_t) (c[1] * photo_k >> 8) << 8 | (uint32_t) (c[2] * photo_k >> 8) : 0;
            entry = opaque | fos_screen_yuv_rgb(rgb, white_y, scr_std, scr_sat, scr_warm, scr_bright);
        }
        if (i == PALETTE_PROBE) palette_probe = entry;
        EngDrvOut(LCD_Palette[3 * i], entry);
        EngDrvOut(LCD_Palette[3 * i + 0x300], entry);
    }
#endif
}

static void palette_canon()
{
#ifdef FEATURE_VRAM_RGBA
    clrscr();
    filmroll_rgba_palette = 0;
    ml_refresh_display_needed = 1;
#else
    for (int i = 1; i < 255; i++)
    {
        EngDrvOut(LCD_Palette[3 * i], LCD_Palette[3 * i + 2]);
        EngDrvOut(LCD_Palette[3 * i + 0x300], LCD_Palette[3 * i + 2]);
    }
#endif
}

/* Take the display from Canon's GUI the same way the ML menu does:
 * switch Canon to the ML menu GUI mode (LiveView stops drawing its
 * overlays and stops handling keys), stop Canon from flipping its own
 * bitmap buffer, then turn the bitmap layer back on for us. */
static void film_ui_open()
{
    NotifyBoxHide();
    if (lv) bmp_off();
    SetGUIRequestMode(GUIMODE_ML_MENU);
    msleep(200);
#ifndef FEATURE_VRAM_RGBA
    canon_gui_disable_front_buffer();
    CancelDateTimer();
#endif
    clrscr();
    bmp_on();
    ui_last_mode_poke = get_ms_clock();
}

static void film_ui_close()
{
    palette_canon();
    if (lv) bmp_off();
    SetGUIRequestMode(0);
    msleep(100);
#ifndef FEATURE_VRAM_RGBA
    canon_gui_enable_front_buffer(0);
#endif
    clrscr();
    redraw();
    if (lv) bmp_on();
}

/* frame times are kept while the film OS runs and logged when it closes:
 * writing the card in the middle of an animation would stall it */
static struct fos_stat stat_all;

static void ui_task(int open)
{
    keyq_in = keyq_out = 0;
    ui_abort = 0;
    ui_after = 0;
    film_ui_open();
    DBG_STATE("film os: open");
    memset(&stat_all, 0, sizeof(stat_all));

    fos_ui_run(open);

    film_ui_close();
    ui_running = 0;
    hud_dirty = 1;
    DBG_STATE("film os: closed");
    if (stat_all.frames)
    {
        struct fos_stat *s = &stat_all;
        int n = s->frames, total = s->draw_ms + s->present_ms + s->rest_ms;
        dbg("film os: %d frames in %d ms = %d/s; per frame %d ms draw (%d canisters) + %d present + %d rest, worst %d, open %d ms, quality %d",
            n, total, total ? n * 1000 / total : 0, s->draw_ms / n, s->cans_ms / n, s->present_ms / n, s->rest_ms / n,
            s->worst_ms, s->open_ms, fos_can_quality);
    }

    if (ui_after & AFTER_APPLY)
    {
        apply_film(roll_film);
        beep();
#ifdef CONFIG_R
        /* the settings prompt again once this notice has had its 6 s */
        manual_prompt_due = film_capture_check() ? get_ms_clock() + 6500 : 0;
#else
        film_capture_check();
#endif
    }
    if (dev_busy)
    {
        /* left in the middle of developing */
        dev_busy = 0;
        powersave_permit();
    }
    if (ui_after & AFTER_CANON_MENU)
        fake_simple_button(BGMT_MENU);
}

static void open_ui(int screen)
{
    if (ui_running)
        return;
    if (!fos_fonts_ready())
    {
        NotifyBox(5000, "Film OS: ML/DATA/" FOS_FONT_FILE " is missing");
        return;
    }
    ui_running = 1;
    /* the same priority as ML's own menu redraw: Canon's tasks still come
     * first, and the film OS rests between frames */
    task_create("film_os", 0x1a, 0x4000, ui_task, (void *) screen);
}

/* ---- what the film OS asks of the camera (see fos.h) */

int fos_port_ms()
{
    return get_ms_clock();
}

void fos_port_sleep(int ms)
{
    msleep(ms);
    int now = get_ms_clock();

    /* the Canon dialog under us times out unless poked (CONFIG_MENU_TIMEOUT_FIX) */
    if (lv && now - ui_last_mode_poke > 2000)
    {
        SetGUIRequestMode(GUIMODE_ML_MENU);
        ui_last_mode_poke = now;
    }
    /* other code (ML's powersave, clear overlays) may put Canon's palette
     * back: check one entry every frame, rewrite all of them now and then */
    static int last_wake = 0;
    if (dev_busy && now - last_wake > 5000)
    {
        idle_wakeup_reset_counters(-10);
        last_wake = now;
    }
#ifndef FEATURE_VRAM_RGBA
    uint32_t probe = shamem_read(LCD_Palette[3 * PALETTE_PROBE]);
    if ((probe && probe != palette_probe) || now - ui_last_palette > 1000)
    {
        palette_ours();
        ui_last_palette = now;
    }
#endif
}

int fos_port_key()
{
    if (keyq_out == keyq_in)
        return FK_NONE;
    int k = keyq[keyq_out];
    keyq_out = (keyq_out + 1) % KEYQ;
    return k;
}

int fos_port_abort()
{
    if (ui_abort || ml_shutdown_requested || get_halfshutter_pressed())
        return 1;
    static int mismatch_since = 0;
#ifdef CONFIG_R
    /* Canon's power saving turns the display off about 3 minutes in, and on this body it can't
     * be held off (powersave_prohibit is a property write the whitelist refuses): developing
     * goes on with the screen dark. 129 is the GUI mode seen as that happens. */
    if (dev_busy && (!DISPLAY_IS_ON || CURRENT_GUI_MODE == 129))
    {
        static int told = 0;
        if (!told) dbg("film os: display off (GUI mode %d): developing goes on", CURRENT_GUI_MODE);
        told = 1;
        mismatch_since = 0;
        return 0;
    }
#endif
    if (!DISPLAY_IS_ON)
        return 1;
    /* a Canon dialog took over (low battery, card door...): give the mode
     * request time to land, then let go if Canon keeps something else up */
    int now = get_ms_clock();
    if (CURRENT_GUI_MODE == GUIMODE_ML_MENU)
        mismatch_since = 0;
    else if (!mismatch_since)
        mismatch_since = now;
    else if (now - mismatch_since > 1500)
    {
        dbg("film os: Canon GUI mode %d, leaving", CURRENT_GUI_MODE);
        mismatch_since = 0;
        return 1;
    }
    return 0;
}

void fos_port_present(const uint8_t *rows, int pitch, int y0, int y1)
{
#ifdef FEATURE_VRAM_RGBA
    uint8_t *vram = bmp_vram(); /* ML's 8-bit staging canvas, not Canon's RGBA surface */
#else
    uint8_t *vram = bmp_vram_real();
#endif
    if (!vram || !DISPLAY_IS_ON)
        return;
    y0 = COERCE(y0, 0, FOS_H);
    y1 = COERCE(y1, y0, FOS_H);
    for (int y = y0; y < y1; y++)
        memcpy(vram + y * BMPPITCH, rows + (y - y0) * pitch, FOS_W);
#ifdef FEATURE_VRAM_RGBA
    ml_refresh_display_needed = 1;
#endif
}

void fos_port_palette()
{
#ifndef FEATURE_VRAM_RGBA
    /* once: Canon's own entries, to check the colour conversion against */
    static int logged = 0;
    if (!logged && debug_log)
    {
        logged = 1;
        dbg("canon palette 1-7: %08x %08x %08x %08x %08x %08x %08x",
            LCD_Palette[3 * 1 + 2], LCD_Palette[3 * 2 + 2], LCD_Palette[3 * 3 + 2], LCD_Palette[3 * 4 + 2],
            LCD_Palette[3 * 5 + 2], LCD_Palette[3 * 6 + 2], LCD_Palette[3 * 7 + 2]);
        dbg("canon palette 8-15: %08x %08x %08x %08x %08x %08x %08x %08x",
            LCD_Palette[3 * 8 + 2], LCD_Palette[3 * 9 + 2], LCD_Palette[3 * 10 + 2], LCD_Palette[3 * 11 + 2],
            LCD_Palette[3 * 12 + 2], LCD_Palette[3 * 13 + 2], LCD_Palette[3 * 14 + 2], LCD_Palette[3 * 15 + 2]);
    }
#endif
    photo_mode = 0;
    palette_ours();
    ui_last_palette = get_ms_clock();
}

void fos_port_photo(const uint8_t (*pal)[3], int n, int k)
{
    photo_n = COERCE(n, 0, FOS_PRINT_COLOURS);
    memcpy(photo_pal, pal, photo_n * 3);
    photo_k = COERCE(k, 0, 256);
    photo_mode = 1;
    palette_ours();
    ui_last_palette = get_ms_clock();
}

void fos_port_log(const char *msg)
{
    dbg("%s", msg);
}

int fos_port_dev_cap()
{
    return 0;
}

/* ---- the card */

const char *fos_port_card()
{
    static char root[8];
    snprintf(root, sizeof(root), "%s:/", get_shooting_card()->drive_letter);
    return root;
}

void *fos_port_open(const char *path, int write)
{
    return write ? FIO_CreateFile(path) : FIO_OpenFile(path, O_RDONLY | O_SYNC);
}

/* Canon reads files by DMA: into memory the CPU caches, it goes through
 * an uncached buffer. Taken at start (card_buffer), while there is memory
 * for it: on a 5D Mark II, asked for only when the darkroom first read a
 * roll's log, it wasn't there, and every read came back empty (#10). */
#define BOUNCE (64 * 1024)
static uint8_t *bounce = 0;
static int bounce_size = 0;

static int card_buffer()
{
    for (int size = BOUNCE; !bounce && size >= 4096; size /= 4)
    {
        bounce = fio_malloc(size);
        if (bounce) bounce_size = size;
        else dbg("read: no %d-byte buffer for card reads", size);
    }
    return bounce != 0;
}

int fos_port_read(void *f, void *buf, int size)
{
    if (buf != CACHEABLE(buf))
        return FIO_ReadFile(f, buf, size);
    if (!card_buffer()) return -1;
    int done = 0;
    while (done < size)
    {
        int n = MIN(size - done, bounce_size);
        int got = FIO_ReadFile(f, UNCACHEABLE(bounce), n);
        if (got <= 0) break;
        memcpy((uint8_t *) buf + done, UNCACHEABLE(bounce), got);
        done += got;
        if (got < n) break;
    }
    return done;
}

int fos_port_write(void *f, const void *buf, int size)
{
    return FIO_WriteFile(f, buf, size);
}

int fos_port_seek(void *f, int offset)
{
    return FIO_SeekSkipFile(f, offset, SEEK_SET) == offset ? 0 : -1;
}

void fos_port_close(void *f)
{
    if (f) FIO_CloseFile(f);
}

int fos_port_list(const char *dir, int (*fn)(void *ctx, const char *name, int is_dir), void *ctx)
{
    char path[FIO_MAX_PATH_LENGTH];
    int len = snprintf(path, sizeof(path), "%s", dir);
    if (len > 0 && len < (int) sizeof(path) - 1 && path[len - 1] != '/') strcat(path, "/");
    struct fio_file file;
    struct fio_dirent *d = FIO_FindFirstEx(path, &file);
#ifdef CONFIG_R
    static int listings;
    listings++;
#endif
    if (IS_ERROR(d))
    {
#ifdef CONFIG_R
        dbg("list: %s failed (%d), listing %d", path, (int) d, listings);
#endif
        return -1;
    }
    int n = 0;
    do
    {
        if (file.name[0] == '.') continue;
        n++;
        if (!fn(ctx, file.name, (file.mode & ATTR_DIRECTORY) != 0)) break;
    }
    while (FIO_FindNextEx(d, &file) == 0);
    FIO_FindClose(d);
    return n;
}

int fos_port_mkdir(const char *path)
{
#ifdef CONFIG_R
    /* is_dir lists, and listing stops working part way through a power-on: folders that were
     * there looked missing, and Canon's CreateDirectory makes another of the same name.
     * FIO_CreateFile only makes the folder if the file can't be created. */
    char probe[FIO_MAX_PATH_LENGTH];
    snprintf(probe, sizeof(probe), "%s/MFILM.TMP", path);
    void *f = FIO_CreateFile(probe);
    if (!f) return -1;
    FIO_CloseFile(f);
    FIO_RemoveFile(probe);
    return 0;
#else
    if (is_dir(path)) return 0;
    FIO_CreateDirectory(path);
    return is_dir(path) ? 0 : -1;
#endif
}

void *fos_port_alloc(int size)
{
    void *p = tmp_malloc(size);
    if (!p) dbg("alloc: %d bytes failed", size);
    return p;
}

void fos_port_free(void *p)
{
    if (p) free(p);
}

void fos_port_roll(struct fos_roll *r)
{
    if (roll_loaded && !recent_valid)
    {
        update_log_path();
        read_back_log();
    }
    memset(r, 0, sizeof(*r));
    r->loaded = roll_loaded;
    r->number = roll_number;
    r->film = COERCE(roll_film, 0, fos_nfilms - 1);
    r->exposures = roll_exposures;
    r->shot = frames_shot;
    r->need_wind = need_wind;
    snprintf(r->loaded_at, sizeof(r->loaded_at), "%s", loaded_at);
    r->nrecent = nrecent;
    for (int i = 0; i < nrecent && i < FOS_RECENT; i++)
    {
        r->recent[i].no = recent[i].no;
        memcpy(r->recent[i].time, recent[i].time, sizeof(r->recent[i].time));
        memcpy(r->recent[i].fstop, recent[i].fstop, sizeof(r->recent[i].fstop));
    }
}

static int *setting_var(int s)
{
    switch (s)
    {
        case FS_HUD:        return &hud_enabled;
        case FS_LEDS:       return &leds_on_half;
        case FS_FOCUS:      return &focus_screen;
        case FS_WIND:       return &wind_mode;
        case FS_APKEYS:     return &aperture_keys;
        case FS_LENS_WIDE:  return &lens_widest;
        case FS_FSTOP:      return &manual_fstop;
        case FS_STARTUP:    return &startup_picker;
        case FS_PLAYLOCK:   return &play_lock;
        case FS_BOOTANIM:   return &boot_anim;
        case FS_DEBUG:      return &debug_log;
        case FS_MODE:       return &film_mode;
        case FS_FILM:       return &film_sel;
        case FS_EXP:        return &exp_sel;
        case FS_SCR_STD:    return &scr_std;
        case FS_SCR_SAT:    return &scr_sat;
        case FS_SCR_WARM:   return &scr_warm;
        case FS_SCR_BRIGHT: return &scr_bright;
    }
    return 0;
}

int fos_port_get(int s)
{
    int *v = setting_var(s);
    if (s == FS_FSTOP) return current_fstop();
    return v ? *v : 0;
}

void fos_port_set(int s, int value)
{
    int *v = setting_var(s);
    if (v) *v = value;
    hud_dirty = 1;
    /* screen colour: the whole film OS changes at once */
    if (s == FS_SCR_STD || s == FS_SCR_SAT || s == FS_SCR_WARM || s == FS_SCR_BRIGHT)
    {
        palette_ours();
        ui_last_palette = get_ms_clock();
    }
}

void fos_port_action(int action, int a, int b)
{
    switch (action)
    {
        case FA_LOAD:
            if (ROLL_IN_PROGRESS) rewind_roll();
            load_roll(a, fos_exposure_choices[COERCE(b, 0, FOS_NEXP - 1)]);
            ui_after |= AFTER_APPLY;
            break;
        case FA_REWIND:
            rewind_roll();
            break;
        case FA_BUSY:
            if (a && !dev_busy) powersave_prohibit();
            if (!a && dev_busy) powersave_permit();
            dev_busy = a;
            dbg("darkroom: %s", a ? "developing" : "done");
            break;
        case FA_REAPPLY:
            if (roll_loaded) ui_after |= AFTER_APPLY;
            break;
        case FA_CANON_MENU:
            ui_after |= AFTER_CANON_MENU;
            break;
    }
}

void fos_port_clock(char *buf, int size)
{
    /* screens ask every frame; the RTC is read once a second */
    static char cached[8];
    static int last = -100000;
    int now_ms = get_ms_clock();
    if (now_ms - last > 1000)
    {
        struct tm now;
        LoadCalendarFromRTC(&now);
        snprintf(cached, sizeof(cached), "%02d:%02d", now.tm_hour, now.tm_min);
        last = now_ms;
    }
    snprintf(buf, size, "%s", cached);
}

int fos_port_lens_manual()
{
    return lens_is_manual();
}

const char *fos_port_lens_name()
{
    return lens_info.name[0] ? lens_info.name : "Lens";
}

/* M-Film's version (VERSION at the top of the repository; build_fork.sh writes fos_version.h) */
#if defined(__has_include)
#if __has_include("fos_version.h")
#include "fos_version.h"
#endif
#endif
#ifndef MFILM_VERSION
#define MFILM_VERSION "dev"
#endif

const char *fos_port_model()
{
    /* "Canon EOS 650D": as the camera reports itself */
    return camera_model[0] ? camera_model : "Canon camera";
}

const char *fos_port_version()
{
    return MFILM_VERSION;
}


void fos_port_stat(const struct fos_stat *s)
{
    stat_all.frames += s->frames;
    stat_all.draw_ms += s->draw_ms;
    stat_all.present_ms += s->present_ms;
    stat_all.rest_ms += s->rest_ms;
    stat_all.cans_ms += s->cans_ms;
    if (s->worst_ms > stat_all.worst_ms) stat_all.worst_ms = s->worst_ms;
    if (s->open_ms) stat_all.open_ms = s->open_ms;
}

/* ---------------------------------------------------------------- viewfinder HUD */

static volatile int hud_showing = 0;    /* HUD drawn on the LiveView screen */

static int hud_visible()
{
    return hud_enabled && roll_loaded && lv && display_idle() && !is_movie_mode() && !ui_running;
}

/* a DSLR shooting through the viewfinder: the back screen shows Canon's
 * settings display, and the top plate (fos_hud_plate) goes over it */
static int plate_visible()
{
    return !FOS_LV_ONLY && hud_enabled && roll_loaded && !lv && display_is_on() && display_idle()
        && !is_movie_mode() && !ui_running;
}

/* Magic Lantern's own LiveView overlays never draw: the film OS (or
 * Canon's clean screen) is all you see */
int filmroll_overlays_active()
{
    return 1;
}

/* the HUD draws with Canon's palette */
static struct fos_hud_palette hud_pal;

static void hud_palette_init()
{
    hud_pal.clear = COLOR_EMPTY;
    hud_pal.black = COLOR_BLACK;
    hud_pal.white = COLOR_WHITE;
    hud_pal.red = COLOR_RED;
    hud_pal.dark_red = COLOR_DARK_RED;
    for (int i = 0; i <= 100; i++)
        hud_pal.gray[i] = COLOR_GRAY(i);
}

/* the shutter speed as the LEDs show it: 1/125 is 125, slow ones keep the " */
static void shutter_led(char *buf, int size)
{
    if (!lens_info.raw_shutter)
    {
        snprintf(buf, size, "--");
        return;
    }
    const char *s = lens_format_shutter(lens_info.raw_shutter);
    int n = 0;
    for (; *s && n < size - 1; s++)
        if ((*s >= '0' && *s <= '9') || *s == '.' || *s == '"')
            buf[n++] = *s;
    buf[n] = 0;
}

static void hud_draw(int lit, int blink, int plate)
{
    struct fos_hud h;
    memset(&h, 0, sizeof(h));
    h.lit = lit;
    h.blink = blink;
    h.fstop = current_fstop();
    h.widest = lens_is_manual() ? lens_widest : 0;
#ifdef AE_VALUE
    if (lit)
    {
        int ev8 = get_ae_value();               /* meter reading, 1/8 EV, + = too bright */
        h.meter = 1;
        h.want = h.fstop + (ev8 * 3 + (ev8 >= 0 ? 4 : -4)) / 8;
    }
#endif
    shutter_led(h.shutter, sizeof(h.shutter));
    h.frame = frames_shot + 1;
    h.exposures = roll_exposures;
    h.finished = ROLL_FINISHED;
    h.need_wind = need_wind;
    h.focus_screen = focus_screen;
    h.film = fos_films[COERCE(roll_film, 0, fos_nfilms - 1)].name;
    h.iso = fos_films[COERCE(roll_film, 0, fos_nfilms - 1)].box_iso;
    uint8_t *vram = bmp_vram();
#if !FOS_LV_ONLY
    if (vram && plate) fos_hud_plate(vram, BMPPITCH, &h, &hud_pal);
    else
#endif
    if (vram) fos_hud_draw(vram, BMPPITCH, &h, &hud_pal);
#ifdef FEATURE_VRAM_RGBA
    ml_refresh_display_needed = 1;
#endif
}

static void hud_clear()
{
    uint8_t *vram = bmp_vram();
    if (vram)
        fos_hud_clear(vram, BMPPITCH, &hud_pal);
#ifdef FEATURE_VRAM_RGBA
    ml_refresh_display_needed = 1;
#endif
}

/* one HUD refresh; called from the main loop */
static void hud_step()
{
    static int last_sig = -1;
    static int last_draw = 0;
    static int drawn = 0;
    static int was_visible = -1;
    static int plate_drawn = 0;

    int plate = plate_visible();
    int visible = hud_visible() || plate;
    if (visible * 2 + plate != was_visible)
    {
        dbg("hud: visible %d plate %d (enabled %d roll %d lv %d movie %d ui %d)",
            visible, plate, hud_enabled, roll_loaded, lv, is_movie_mode(), ui_running);
        was_visible = visible * 2 + plate;
    }
    hud_showing = visible;

#if !FOS_LV_ONLY
    /* On a DSLR Canon keeps drawing its own screen (the settings display, or
     * its LiveView overlays) and flipping it over ours: the two flicker in
     * turn, and a change of aperture or shutter brings Canon's back. While the
     * HUD or the plate is up, stop Canon flipping its buffer, as the ML menu
     * does, and wipe what it left. Canon gets the screen back as soon as ours
     * goes (a Canon menu, playback, the display off). The film OS's own pages
     * take the buffer themselves (film_ui_open). */
    static int canon_held = 0;
    if (visible && !canon_held)
    {
        canon_gui_disable_front_buffer();
        clrscr();
        canon_held = 1;
        hud_dirty = 1;
        dbg("hud: Canon's screen held");
    }
    else if (!visible && canon_held)
    {
        canon_held = 0;
        if (!ui_running) canon_gui_enable_front_buffer(1);
        dbg("hud: Canon's screen given back%s", ui_running ? " (to the film OS)" : "");
    }
#endif

    /* the plate off (LiveView on, or a Canon menu over it): hand the screen back */
    if (plate_drawn && !plate)
    {
#if !FOS_LV_ONLY
        uint8_t *vram = bmp_vram();
        if (vram && display_idle() && !ui_running) fos_hud_plate_clear(vram, BMPPITCH, &hud_pal);
#endif
        plate_drawn = 0;
        last_sig = -1;
    }

    if (!visible)
    {
        /* HUD switched off or roll rewound: wipe it once we're back on the LiveView screen */
        if (drawn && (!hud_enabled || !roll_loaded) && lv && display_idle() && !ui_running)
        {
            hud_clear();
            drawn = 0;
        }
        last_sig = -1;
        return;
    }
    drawn = 1;

    int now = get_ms_clock();
    int lit = !leds_on_half || get_halfshutter_pressed() || now < leds_until;
    int blink = (now / 400) % 2;
#ifdef AE_VALUE
    int ev8 = lit ? get_ae_value() : 0;
#else
    int ev8 = 0; /* EOS R has no verified AE_VALUE source: no meter needle. */
#endif

    int sig = lit | blink << 1 | need_wind << 2 | (frames_shot & 0xFF) << 3
            | lens_info.raw_shutter << 11 | (ev8 & 0xFF) << 19 | (current_fstop() & 0x1F) << 27;

    /* redraw on change, and every second to win over Canon's overlays */
    if (sig != last_sig || hud_dirty || now - last_draw > 1000)
    {
        hud_dirty = 0;
        last_sig = sig;
        last_draw = now;
        hud_draw(lit, blink, plate);
        plate_drawn = plate;
    }
}

/* ---------------------------------------------------------------- keys */

/* while the film OS is up it gets the dial and the buttons; anything it
 * doesn't use goes on to Canon, like the ML menu does */
static int ui_key(struct event *event, int key)
{
    int dir = 0;
    switch (key)
    {
        case MODULE_KEY_PRESS_HALFSHUTTER:
        case MODULE_KEY_PRESS_FULLSHUTTER:
        case MODULE_KEY_REC:
            ui_abort = 1;               /* back to the camera, ready to shoot */
            return 1;
        case MODULE_KEY_UNPRESS_HALFSHUTTER:
        case MODULE_KEY_UNPRESS_FULLSHUTTER:
            return 1;                   /* Canon must see the shutter let go */

        case MODULE_KEY_WHEEL_LEFT:     dir = FK_WHEEL_L; break;
        case MODULE_KEY_WHEEL_RIGHT:    dir = FK_WHEEL_R; break;
        case MODULE_KEY_WHEEL_UP:       dir = FK_WHEEL_L; break;
        case MODULE_KEY_WHEEL_DOWN:     dir = FK_WHEEL_R; break;

        case MODULE_KEY_PRESS_LEFT:     keyq_push(FK_LEFT); return 0;
        case MODULE_KEY_PRESS_RIGHT:    keyq_push(FK_RIGHT); return 0;
        case MODULE_KEY_PRESS_UP:       keyq_push(FK_UP); return 0;
        case MODULE_KEY_PRESS_DOWN:     keyq_push(FK_DOWN); return 0;
        case MODULE_KEY_UNPRESS_UDLR:
            keyq_push(FK_RELEASE | FK_LEFT);
            keyq_push(FK_RELEASE | FK_RIGHT);
            keyq_push(FK_RELEASE | FK_UP);
            keyq_push(FK_RELEASE | FK_DOWN);
            return 0;

        case MODULE_KEY_PRESS_SET:
        case MODULE_KEY_Q:
        case MODULE_KEY_JOY_CENTER:     keyq_push(FK_SET); return 0;
        case MODULE_KEY_UNPRESS_SET:    return 0;
        case MODULE_KEY_MENU:           keyq_push(FK_MENU); return 0;
        case MODULE_KEY_INFO:           keyq_push(FK_INFO); return 0;
        case MODULE_KEY_PLAY:           keyq_push(FK_PLAY); return 0;
        case MODULE_KEY_TRASH:
        case MODULE_KEY_TOUCH_2_FINGER: keyq_push(FK_HOME); return 0;

        case 0:
            return 1;                   /* not a button: Canon's business */
        default:
            return 0;
    }

    /* newer firmware may group several wheel clicks into one event */
    int count = MAX(1, MIN(event->arg, 8));
    for (int i = 0; i < count; i++)
        keyq_push(dir);
    return 0;
}

static unsigned int lv_key(unsigned int key)
{
    /* what used to open the Magic Lantern menu opens home instead */
    if (key == MODULE_KEY_TRASH || key == MODULE_KEY_TOUCH_2_FINGER)
    {
        if (shooting_screen())
        {
            dbg("key: home");
            open_ui(FOS_OPEN_HOME);
        }
        return 0;
    }

    /* PLAY: the developed prints, never Canon's playback of what the
     * darkroom hasn't seen yet */
    if (key == MODULE_KEY_PLAY && play_lock && shooting_screen())
    {
        dbg("key: PLAY -> prints");
        open_ui(FOS_OPEN_PRINTS);
        return 0;
    }

    /* only on the plain LiveView screen: never steal keys from Canon's menus or dialogs */
    if (!lv || !roll_loaded || !display_idle())
        return 1;

    if (key == MODULE_KEY_PRESS_HALFSHUTTER || key == MODULE_KEY_UNPRESS_HALFSHUTTER)
        hud_dirty = 1;

    /* aperture ring for manual lenses: the wheel in Av mode (where it has
     * nothing to adjust without a lens), or up/down if the user prefers */
    if (lens_is_manual() && !ROLL_FINISHED)
    {
        int dir = 0;
        if (aperture_keys == 0 && shooting_mode == SHOOTMODE_AV)
            dir = (key == MODULE_KEY_WHEEL_RIGHT) ? 1 : (key == MODULE_KEY_WHEEL_LEFT) ? -1 : 0;
        else if (aperture_keys == 1)
            dir = (key == MODULE_KEY_PRESS_UP) ? 1 : (key == MODULE_KEY_PRESS_DOWN) ? -1 : 0;
        if (dir)
        {
            step_manual_fstop(dir);
            leds_until = get_ms_clock() + 2000;
            hud_dirty = 1;
            return 0;
        }
    }

    if (ROLL_FINISHED)
    {
        if (key == MODULE_KEY_PRESS_SET)
        {
            open_ui(FOS_OPEN_END);
            return 0;
        }
        return 1;
    }

    if (need_wind)
    {
        if (key == MODULE_KEY_PRESS_SET)
        {
            need_wind = 0;
            leds_until = get_ms_clock() + 1500;
            hud_dirty = 1;
            return 0;
        }
        if (wind_mode == 2 && (key == MODULE_KEY_PRESS_HALFSHUTTER || key == MODULE_KEY_PRESS_FULLSHUTTER))
            return 0;
    }

    return 1;
}

int handle_filmroll_keys(struct event *event)
{
    int key = module_translate_key(event->param, MODULE_KEY_PORTABLE);
    if (ui_running)
    {
#ifdef BGMT_UNPRESS_LEFT
        /* the EOS M reports each direction's release on its own, and the
         * portable key codes have no name for those */
        switch (event->param)
        {
            case BGMT_UNPRESS_LEFT:  keyq_push(FK_RELEASE | FK_LEFT);  return 0;
            case BGMT_UNPRESS_RIGHT: keyq_push(FK_RELEASE | FK_RIGHT); return 0;
            case BGMT_UNPRESS_UP:    keyq_push(FK_RELEASE | FK_UP);    return 0;
            case BGMT_UNPRESS_DOWN:  keyq_push(FK_RELEASE | FK_DOWN);  return 0;
        }
#endif
        return ui_key(event, key);
    }

    if (!key)
        return 1;

    /* newer firmware may group several wheel clicks into one event */
    int count = 1;
    if (key == MODULE_KEY_WHEEL_LEFT || key == MODULE_KEY_WHEEL_RIGHT)
        count = MAX(1, event->arg);

    int pass = 1;
    for (int i = 0; i < count; i++)
        pass = lv_key(key);
    return pass;
}

/* ---------------------------------------------------------------- the ML menu, replaced */

void filmroll_menu_requested()
{
    if (shooting_screen())
        open_ui(FOS_OPEN_HOME);
}

static void filmroll_init()
{
    if (roll_loaded)
        update_log_path();
    fos_ui_init();
    hud_palette_init();
    dbg("init: M-Film " MFILM_VERSION ", fork build %s %s, roll %d loaded %d shot %d/%d, startup %d hud %d",
        __DATE__, __TIME__, roll_number, roll_loaded, frames_shot, roll_exposures,
        startup_picker, hud_enabled);
}

/* The film OS's fonts are a file on the card (see fos_gfx.h): kept in
 * memory taken after boot, not in autoexec.bin */
static void load_fonts()
{
    int size = 0;
    uint8_t *raw = read_entire_file("ML/DATA/" FOS_FONT_FILE, &size);
    if (!raw)
    {
        dbg("fonts: ML/DATA/%s not found", FOS_FONT_FILE);
        return;
    }
    uint8_t *blob = malloc(size);       /* cached memory, for drawing */
    if (blob)
    {
        memcpy(blob, raw, size);
        if (!fos_fonts_load(blob, size))
        {
            free(blob);
            blob = 0;
        }
    }
    fio_free(raw);
    dbg("fonts: %d bytes, %s", size, blob ? "loaded" : "not usable");
}

/* the films beyond the built-in ones, from the card; the file stays in
 * memory (the films point into it) */
static void load_films()
{
    int size = 0;
    uint8_t *raw = read_entire_file("ML/DATA/" FOS_FILM_FILE, &size);
    if (!raw)
    {
        dbg("films: ML/DATA/%s not found, the built-in ones only", FOS_FILM_FILE);
        return;
    }
    uint8_t *blob = malloc(size);
    if (blob)
    {
        memcpy(blob, raw, size);
        if (!fos_films_load(blob, size))
        {
            free(blob);
            blob = 0;
        }
    }
    fio_free(raw);
    dbg("films: %d (%d bytes, %s)", fos_nfilms, size, blob ? "loaded" : "not usable");
}

/* the film OS is the camera's start screen */
static void startup_task()
{
    /* wait (up to ~15 s) for ML to finish booting and LiveView to settle */
    for (int i = 0; i < 150; i++)
    {
        if (ml_started && shooting_screen())
            break;
        if (i % 10 == 0 && ml_started)
            DBG_STATE("startup waiting");
        msleep(100);
    }
    msleep(300);

    DBG_STATE("startup check");
    if (!ml_started || !shooting_screen())
    {
        dbg("startup: not on the shooting screen, film OS skipped");
        return;
    }
    int shelf = startup_picker == 2 || (startup_picker == 1 && !ROLL_IN_PROGRESS);
    if (boot_anim && startup_picker)
        open_ui(shelf ? FOS_OPEN_BOOT : FOS_OPEN_BOOT_LV);
    else if (shelf)
        open_ui(FOS_OPEN_SHELF);
}

/* Everything runs from one task that waits for ML to finish booting, so
 * nothing touches Canon's GUI state before it exists: start screen first,
 * then frame logging and the HUD. */
static void filmroll_task()
{
    while (!ml_started)
        msleep(100);
    dbg("main: started");

    load_fonts();
    load_films();
    dbg("read: %s", card_buffer() ? "card buffer ready" : "no card buffer: the darkroom can't read the card");
    startup_task();
    film_capture_check();

    int last_alive = 0;
    TASK_LOOP
    {
        poll_frames();

        /* the roll has just been finished: once the camera is quiet, ask
         * whether to develop it now or load a new one first */
        if (end_due && get_ms_clock() > end_due && ROLL_FINISHED)
        {
            if (!ui_running && shooting_screen() && !get_halfshutter_pressed() && job_state_ready_to_take_pic())
            {
                end_due = 0;
                dbg("roll %03d finished: asking", roll_number);
                open_ui(FOS_OPEN_END);
            }
        }
        else if (end_due && !ROLL_FINISHED) end_due = 0;
        if (k % 2 == 0)
            hud_step();                     /* every 100 ms */

        int now = get_ms_clock();
#ifdef CONFIG_R
        if (manual_prompt_due && now > manual_prompt_due && !ui_running)
        {
            manual_prompt_due = 0;
            manual_settings_prompt(roll_film);
        }
#endif
        if (now - last_alive > 60000)
        {
            dbg("main: alive, roll %d loaded %d shot %d/%d file %d",
                roll_number, roll_loaded, frames_shot, roll_exposures, last_file_number);
            last_alive = now;
        }
        msleep(50);
    }
}

INIT_FUNC("filmroll", filmroll_init);
TASK_CREATE("film_main", filmroll_task, 0, 0x1e, 0x2000);
