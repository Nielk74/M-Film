"""Host checks: ML_TEST_SRC=/path/to/pinned/ML python3 -m unittest discover -s ml-dev/tests -v.

Uses the real upstream files for the patcher and its generated display code.
No camera, Canon firmware dump, or property writes are involved.
"""

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SIM = ROOT / "ml-dev/filmos/sim"
PATCHER = ROOT / "ml-dev/fork/apply_fork.py"


def run(*args, cwd=ROOT, **kwargs):
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, timeout=120, **kwargs)


def c_function(source, signature):
    start = source.index(signature)
    end = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


class EosRPatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.src = Path(self.tmp.name)
        upstream = Path(os.environ["ML_TEST_SRC"])
        for rel in ("src/gui-common.c", "src/zebra.c", "src/menu.c", "src/bmp.c",
                    "src/picstyle.h", "src/boot-d45-am.c", "modules/Makefile",
                    "platform/R.180/features.h", "platform/R.180/Makefile",
                    "platform/R.180/modules.included"):
            dest = self.src / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(upstream / rel, dest)

    def patch(self, *flags):
        return run("python3", str(PATCHER), str(self.src), "R.180", *flags)

    def snapshot(self):
        return {str(p.relative_to(self.src)): p.read_bytes()
                for p in self.src.rglob("*") if p.is_file()}

    def test_eos_r_requires_explicit_experimental_flag_before_any_edits(self):
        before = self.snapshot()
        result = self.patch("--no-darkroom")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_eos_r_darkroom_links_the_cr3_decoder(self):
        result = self.patch("--experimental-eos-r")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        makefile = (self.src / "platform/R.180/Makefile").read_text(encoding="utf-8")
        self.assertIn("fos_crx.o", makefile)
        self.assertIn("fos_dev.o", makefile)

    def test_eos_r_without_darkroom_has_no_cr3_decoder(self):
        result = self.patch("--experimental-eos-r", "--no-darkroom")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        makefile = (self.src / "platform/R.180/Makefile").read_text(encoding="utf-8")
        self.assertNotIn("fos_crx.o", makefile)

    def test_eos_r_rejects_old_picture_style_api_before_any_edits(self):
        (self.src / "src/picstyle.h").write_text("/* older ML API */\n", encoding="utf-8")
        before = self.snapshot()
        result = self.patch("--experimental-eos-r", "--no-darkroom")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_patch_is_idempotent(self):
        first = self.patch("--experimental-eos-r", "--no-darkroom")
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        before = self.snapshot()
        second = self.patch("--experimental-eos-r", "--no-darkroom")
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_picture_style_timeout_does_not_modify_previous_style(self):
        source = (ROOT / "ml-dev/fork/src/filmroll.c").read_text(encoding="utf-8")
        function = (c_function(source, "static void manual_settings_prompt(int index)") + "\n"
                    + c_function(source, "static void apply_film(int index)"))
        program = self.src / "picstyle_check.c"
        program.write_text('''#include <assert.h>
#include "fos.h"
#define CONFIG_R
#define COERCE(x, lo, hi) ((x) < (lo) ? (lo) : (x) > (hi) ? (hi) : (x))
#define PROP_PICTURE_STYLE 1
#define msleep(...) ((void)0)
#define NotifyBox(timeout, ...) snprintf(notice, sizeof(notice), __VA_ARGS__)
#define dbg(...) ((void)0)
static char notice[192];
static struct fos_film stock = { .picstyle = PS_STANDARD, .iso = 400, .kelvin = 5500, .wbs_ba = 2, .wbs_gm = -1 };
const struct fos_film *fos_films = &stock;
int fos_nfilms = 1;
static int acknowledged, writes;
static int prop_request_change_wait(int prop, const void *value, int size, int timeout) {
    (void)prop; (void)value; (void)size; (void)timeout;
    return acknowledged;
}
static void picstyle_set_current_contrast(int v) { (void)v; writes++; }
static void picstyle_set_current_sharpness(int v) { (void)v; writes++; }
static void picstyle_set_current_saturation(int v) { (void)v; writes++; }
static void picstyle_set_current_color_tone(int v) { (void)v; writes++; }
''' + function + '''
int main(void) {
    acknowledged = 0;
    apply_film(0);
    assert(writes == 0);
    acknowledged = 1;
    apply_film(0);
    assert(writes == 4);
    /* the films develop at the shot's white balance: nothing may ask for another one */
    assert(strstr(notice, "ISO 400") != 0 && strstr(notice, "5500") == 0 && strstr(notice, "BA") == 0);
    /* NotifyBox draws one unwrapped line of large text */
    assert(strlen(notice) <= 44);
    stock.picstyle = PS_MONO;
    apply_film(0);
    assert(writes == 6);
    return 0;
}
''', encoding="utf-8", newline="\n")
        binary = self.src / "picstyle_check"
        result = run("gcc", "-std=gnu99", "-Wall", "-Wextra", "-Werror", "-I", str(SIM.parent), str(program), "-o", str(binary))
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run(str(binary))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_capture_check_reports_its_notice_so_the_settings_prompt_is_repeated(self):
        # ui_task re-shows the ISO/WB prompt only when this returns 1.
        source = (ROOT / "ml-dev/fork/src/filmroll.c").read_text(encoding="utf-8")
        function = c_function(source, "static int film_capture_check()")
        program = self.src / "capture_check.c"
        program.write_text('''#include <assert.h>
#include <stdio.h>
#define PICQ_RAW 0x4060000
#define NotifyBox(timeout, ...) (notices++)
#define dbg(...) ((void)0)
static int roll_loaded, pic_quality, image_review_time, notices;
''' + function + '''
int main(void) {
    roll_loaded = 0;
    assert(film_capture_check() == 0 && notices == 0);
    roll_loaded = 1;
    pic_quality = PICQ_RAW;
    image_review_time = 0;
    assert(film_capture_check() == 0 && notices == 0);
    image_review_time = 2;
    assert(film_capture_check() == 1 && notices == 1);
    pic_quality = 0;
    image_review_time = 0;
    assert(film_capture_check() == 1 && notices == 2);
    return 0;
}
''', encoding="utf-8", newline="\n")
        binary = self.src / "capture_check"
        result = run("gcc", "-std=gnu99", "-Wall", "-Wextra", "-Werror", str(program), "-o", str(binary))
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run(str(binary))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_real_eos_r_quality_value_does_not_ask_for_raw(self):
        # RAW+JPEG read 0x4030700 on a real EOS R: the older cameras' test
        # took that for no RAW and put up a false notice.
        source = (ROOT / "ml-dev/fork/src/filmroll.c").read_text(encoding="utf-8")
        function = c_function(source, "static int film_capture_check()")
        program = self.src / "capture_r_check.c"
        program.write_text('''#include <assert.h>
#include <stdio.h>
#define CONFIG_R
#define PICQ_RAW 0x4060000
#define NotifyBox(timeout, ...) (notices++)
#define dbg(...) ((void)0)
static int roll_loaded = 1, pic_quality = 0x4030700, image_review_time, notices;
''' + function + '''
int main(void) {
    (void) pic_quality;
    assert(film_capture_check() == 0 && notices == 0);
    image_review_time = 2;
    assert(film_capture_check() == 1 && notices == 1);
    return 0;
}
''', encoding="utf-8", newline="\n")
        binary = self.src / "capture_r_check"
        result = run("gcc", "-std=gnu99", "-Wall", "-Wextra", "-Werror", str(program), "-o", str(binary))
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run(str(binary))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unknown_aperture_uses_the_dialled_one(self):
        # A real EOS R logged f/0.95: lens_info.aperture stayed 0
        source = (ROOT / "ml-dev/fork/src/filmroll.c").read_text(encoding="utf-8")
        function = c_function(source, "static int current_fstop()")
        program = self.src / "fstop_check.c"
        program.write_text('''#include <assert.h>
#define CONFIG_R
#define ABS(x) ((x) < 0 ? -(x) : (x))
#define COERCE(x, lo, hi) ((x) < (lo) ? (lo) : (x) > (hi) ? (hi) : (x))
static const int fos_fstops[] = { 95, 100, 120, 140, 180, 200, 280 };
enum { fos_nfstops = 7 };
static struct { int aperture; } lens_info;
static int manual_fstop = 4, lens_widest = 2, manual;
static int lens_is_manual(void) { return manual; }
''' + function + '''
int main(void) {
    lens_info.aperture = 0;
    assert(current_fstop() == 4);
    lens_info.aperture = 28;
    assert(current_fstop() == 6);
    manual = 1;
    assert(current_fstop() == 4);
    return 0;
}
''', encoding="utf-8", newline="\n")
        binary = self.src / "fstop_check"
        result = run("gcc", "-std=gnu99", "-Wall", "-Wextra", "-Werror", str(program), "-o", str(binary))
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run(str(binary))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_roll_log_names_the_file_without_mls_prefix_on_eos_r(self):
        # get_file_prefix() read "495A" on a real EOS R; the file was IMG_8714.CR3
        source = (ROOT / "ml-dev/fork/src/filmroll.c").read_text(encoding="utf-8")
        function = c_function(source, "static void record_frame(int file_number)")
        result = subprocess.run(["gcc", "-E", "-P", "-DCONFIG_R", "-x", "c", "-"], input=function, text=True,
                                capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("get_file_prefix", result.stdout)
        self.assertIn("IMG_%04d.CR3", result.stdout)

    def test_actual_display_dispatch_uses_all_256_colors_and_restores_ml_palette(self):
        result = self.patch("--experimental-eos-r", "--no-darkroom")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        bmp = (self.src / "src/bmp.c").read_text(encoding="utf-8")
        match = re.search(r"uint32_t indexed2rgb\(uint8_t color\)\s*\{", bmp)
        self.assertIsNotNone(match)
        end, depth = match.end(), 1
        while depth:
            depth += (bmp[end] == "{") - (bmp[end] == "}")
            end += 1
        dispatch = bmp[match.start():end]
        program = self.src / "palette_check.c"
        program.write_text('''#include <assert.h>
#include <stdint.h>
#include "fos_rgba.h"
#define FEATURE_FILMROLL
#define RGB_LUT_SIZE 80
static uint32_t indexed2rgbLUT[80] = { [1] = 0xffebebeb, [4] = 0xffa33800 };
static const uint32_t * volatile live_palette;
static int simulate_close, palette_reads;
/* Model a UI close on a second shared-pointer load. A local snapshot
 * stays valid because the palette banks have static storage duration. */
static const uint32_t * volatile *palette_slot(void) {
    if (simulate_close && ++palette_reads == 2) live_palette = 0;
    return &live_palette;
}
#define filmroll_rgba_palette (*palette_slot())
''' + dispatch + '''
int main(void) {
    uint32_t base[256] = { [1] = 0xffffff, [200] = 0xffffff, [254] = 0xffffff };
    uint32_t colors[256];
    fos_rgba_build_palette(colors, base, 0, 0, 256, 0, 0, 0, 0);
    filmroll_rgba_palette = colors;
    /* transparent, yet non-zero so ML's refresh still writes the pixel */
    assert(FOS_RGBA_CLEAR != 0 && FOS_RGBA_CLEAR >> 24 == 0);
    assert(indexed2rgb(0) == FOS_RGBA_CLEAR);
    assert(indexed2rgb(1) == 0xffffffff);
    assert(indexed2rgb(2) == 0xff000000);
    assert(indexed2rgb(200) == 0xffffffff);
    assert(indexed2rgb(254) == 0xffffffff);
    assert(indexed2rgb(255) == FOS_RGBA_CLEAR);
    const uint8_t photo[1][3] = { {255, 255, 255} };
    fos_rgba_build_palette(colors, base, photo, 1, 128, 0, 0, 0, 0);
    uint32_t mid = indexed2rgb(1) & 255;
    assert(mid >= 125 && mid <= 129);
    fos_rgba_build_palette(colors, base, photo, 1, 0, 0, 0, 0, 0);
    assert(indexed2rgb(1) == 0xff000000);
    assert(indexed2rgb(0) == FOS_RGBA_CLEAR);
    palette_reads = 0;
    simulate_close = 1;
    assert(indexed2rgb(1) == 0xff000000);
    assert(palette_reads == 1);
    simulate_close = 0;
    filmroll_rgba_palette = 0;
    assert(indexed2rgb(1) == 0xffebebeb);
    assert(indexed2rgb(200) == 0xffa33800);
    return 0;
}
''', encoding="utf-8", newline="\n")
        binary = self.src / "palette_check"
        compiled = run("gcc", "-std=gnu99", "-Wall", "-Wextra", "-Werror",
                       "-ffunction-sections", "-Wl,--gc-sections", "-I", str(SIM.parent),
                       str(program), str(SIM.parent / "fos_rgba.c"),
                       str(SIM.parent / "fos_gfx.c"), "-lm", "-o", str(binary))
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        checked = run(str(binary))
        self.assertEqual(checked.returncode, 0, checked.stderr)


class FilmOsRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = run("make", "-C", str(SIM), "-j4", "out/sim", "out/devtest", "out/devtest-arm")
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)

    def test_all_existing_scripted_scenes_finish(self):
        for scene in sorted((SIM / "scenes").glob("*.txt")):
            with self.subTest(scene=scene.name):
                result = run(str(SIM / "out/sim"), str(scene), "-", cwd=SIM)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_cr2_decoder_and_arm_alignment_still_match_known_sensor_samples(self):
        with tempfile.TemporaryDirectory() as work:
            dest = Path(work)
            tool = dest / "mkcr2"
            result = run("gcc", "-O2", str(SIM / "tools/mkcr2.c"), "-lm", "-o", str(tool))
            self.assertEqual(result.returncode, 0, result.stderr)
            for channels in (2, 4):
                with self.subTest(channels=channels):
                    cr2, raw = dest / "frame.CR2", dest / "frame.u16"
                    result = run(str(tool), str(cr2), "--small", "--clrs", str(channels), "--raw", str(raw))
                    self.assertEqual(result.returncode, 0, result.stderr)
                    for decoder in ("devtest", "devtest-arm"):
                        result = run(str(SIM / "out" / decoder), "-r", str(raw), str(cr2), str(dest / (decoder + ".FOP")), cwd=SIM)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual((dest / "devtest.FOP").read_bytes(), (dest / "devtest-arm.FOP").read_bytes())


class DarkroomTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.src = Path(self.tmp.name)

    def test_making_a_folder_never_trusts_a_listing(self):
        # is_dir lists. When listing failed on the test body, existing folders looked missing and
        # Canon's CreateDirectory made them again: six ML/DARKROOM on the card.
        source = (ROOT / "ml-dev/fork/src/filmroll.c").read_text(encoding="utf-8")
        function = c_function(source, "int fos_port_mkdir(const char *path)")
        program = self.src / "mkdir.c"
        program.write_text("""#include <assert.h>
#include <stdio.h>
#include <string.h>
#define CONFIG_R
#define FIO_MAX_PATH_LENGTH 128
typedef int FILE_;
static FILE_ a_file;
static int dir_there, made, removed, listed, created;
static int is_dir(const char *p) { listed++; return 0; }          /* listing is failing */
static void FIO_CreateDirectory(const char *p) { made++; dir_there = 1; }
/* as Magic Lantern's: makes the folder itself if it isn't there, and never another of it */
static void *FIO_CreateFile(const char *p) { assert(strstr(p, "/")); if (!dir_there) { dir_there = 1; created++; } return &a_file; }
static void FIO_CloseFile(void *f) {}
static int FIO_RemoveFile(const char *p) { removed++; return 0; }
""" + function + """
int main(void) {
    /* there already: nothing made, nothing left behind */
    dir_there = 1;
    assert(fos_port_mkdir("B:/ML/DARKROOM") == 0);
    assert(made == 0 && created == 0 && removed == 1 && listed == 0);
    /* not there: made once */
    dir_there = 0;
    assert(fos_port_mkdir("B:/ML/DARKROOM/R001") == 0);
    assert(made == 0 && created == 1 && removed == 2 && listed == 0);
    return 0;
}
""", encoding="utf-8", newline="\n")
        binary = self.src / "mkdir"
        result = run("gcc", "-std=gnu99", "-w", str(program), "-o", str(binary))
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run(str(binary))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_developing_goes_on_when_canon_turns_the_display_off(self):
        # On the test body Canon's power saving closed the darkroom about 190 s in, every run:
        # only the first roll of "develop all" got done. It can't be stopped on this body
        # (powersave_prohibit is a property write the whitelist refuses).
        source = (ROOT / "ml-dev/fork/src/filmroll.c").read_text(encoding="utf-8")
        function = c_function(source, "int fos_port_abort()")
        program = self.src / "abort.c"
        program.write_text("""#include <assert.h>
#define CONFIG_R
#define GUIMODE_ML_MENU 104
#define dbg(...) ((void)0)
static int ui_abort, ml_shutdown_requested, DISPLAY_IS_ON = 1, halfshutter, CURRENT_GUI_MODE = GUIMODE_ML_MENU, dev_busy, clock_ms = 1000;
static int get_halfshutter_pressed(void) { return halfshutter; }
static int get_ms_clock(void) { return clock_ms; }
""" + function + """
static int settled(void) { fos_port_abort(); clock_ms += 2000; return fos_port_abort(); }
int main(void) {
    assert(!fos_port_abort());
    /* not developing: as ever */
    DISPLAY_IS_ON = 0; assert(fos_port_abort()); DISPLAY_IS_ON = 1;
    CURRENT_GUI_MODE = 129; assert(settled()); CURRENT_GUI_MODE = GUIMODE_ML_MENU; fos_port_abort();
    /* developing: on through the display going off */
    dev_busy = 1;
    DISPLAY_IS_ON = 0; assert(!fos_port_abort()); DISPLAY_IS_ON = 1;
    CURRENT_GUI_MODE = 129; assert(!settled());
    /* but a half press stops it, and so does shutting down, and any other Canon screen */
    halfshutter = 1; assert(fos_port_abort()); halfshutter = 0;
    ml_shutdown_requested = 1; assert(fos_port_abort()); ml_shutdown_requested = 0;
    CURRENT_GUI_MODE = 3; assert(settled());
    return 0;
}
""", encoding="utf-8", newline="\n")
        binary = self.src / "abort"
        result = run("gcc", "-std=gnu99", "-w", str(program), "-o", str(binary))
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run(str(binary))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_scan_that_finds_nothing_keeps_the_rolls_it_knew(self):
        source = (ROOT / "ml-dev/filmos/fos_dark.c").read_text(encoding="utf-8")
        function = c_function(source, "static void rescan()")
        program = self.src / "rescan.c"
        program.write_text("""#include <assert.h>
#define CONFIG_R
#define MAX_ROLLS 48
struct fos_lab_roll { int number; };
static struct { struct fos_lab_roll rolls[MAX_ROLLS]; } mem, *dm = &mem;
#define rolls (dm->rolls)
static int nrolls, found, logged;
static int fos_lab_scan(struct fos_lab_roll *r, int max) { return found; }
static void fos_port_log(const char *m) { logged++; }
""" + function + """
int main(void) {
    found = 8; rescan(); assert(nrolls == 8);
    found = 0; rescan(); assert(nrolls == 8 && logged == 1);
    found = 3; rescan(); assert(nrolls == 3);
    return 0;
}
""", encoding="utf-8", newline="\n")
        binary = self.src / "rescan"
        result = run("gcc", "-std=gnu99", "-w", str(program), "-o", str(binary))
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run(str(binary))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_roll_whose_log_cant_be_read_keeps_its_contact_sheet(self):
        # On the test body a second developing run couldn't list the roll logs: it found no
        # frames and wrote blank contact sheets over two rolls.
        source = (ROOT / "ml-dev/filmos/fos_dark.c").read_text(encoding="utf-8")
        function = c_function(source, "static void next_job()")
        program = self.src / "next_job.c"
        program.write_text("""#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "fos.h"
#include "fos_dev.h"
#define CONFIG_R
#define T_MDASH "-"
#define FA_BUSY 1
static struct { struct fos_lab_roll rolls[4]; int queue[4]; struct fos_lab_frame frames[FOS_MAX_FRAMES]; char paths[FOS_MAX_FRAMES][64]; } mem, *dm = &mem;
#define rolls (dm->rolls)
#define queue (dm->queue)
static int nqueue, qi, nframes, fi, roll_ready, cur_roll, cur_film, cur_frame, panel_dirty, finished, failed_frames, done_frames, rolls_done;
static char dcim_dir[128], last_error[72];
static float ghost_t, ghost_final, frame_t0, ui_now;
#define GHOST_W 1
#define GHOST_H 1
static unsigned char ghost[1];
static struct fos_dev *dev;
static int frames_found, sheets, prepared, no_folder, started;
const struct fos_film *fos_films;
static struct fos_film one;
static int roll_index(int number) { return 0; }
static int file_exists(const char *path) { return !no_folder; }
int fos_lab_frames(int roll, struct fos_lab_frame *f, int max) { if (frames_found) { memset(f, 0, sizeof(*f)); f->no = 1; snprintf(f->cr2, sizeof(f->cr2), "IMG_1.CR3"); } return frames_found; }
int fos_lab_prepare(int roll) { prepared++; return 0; }
int fos_lab_dcim_dir(char *buf, int size, int roll, int film, const char *when) { if (no_folder) return -1; snprintf(buf, size, "d"); return 0; }
void fos_lab_print_path(char *buf, int size, int roll, int frame) { snprintf(buf, size, "p%d", frame); }
int fos_dev_sheet(const char *sheet, int film, const char *const *prints, int n) { sheets++; return 1; }
void fos_port_log(const char *m) {}
void fos_port_action(int a, int b, int c) {}
struct fos_dev *fos_dev_start(const char *cr2, const char *print, int film, int frame, const char *when, const char *fstop) { started++; return 0; }
void fos_dev_jpeg(struct fos_dev *d, const char *path) {}
void fos_dev_release() {}
""" + function + """
int main(void) {
    fos_films = &one;
    nqueue = 1; queue[0] = 1;
    frames_found = 0;
    next_job();
    assert(sheets == 0 && prepared == 0);
    assert(failed_frames == 1 && rolls_done == 0 && finished == 1 && last_error[0]);
    /* and a roll that can be read still gets its sheet */
    qi = 0; finished = 0; roll_ready = 0;
    frames_found = 1;
    next_job();
    assert(sheets == 1 && prepared == 1 && rolls_done == 1 && finished == 1);
    /* no folder for its JPEGs: nothing is developed that would only half arrive */
    qi = 0; finished = 0; roll_ready = 0; failed_frames = 0; sheets = 0;
    no_folder = 1;
    next_job();
    assert(started == 0 && sheets == 0 && failed_frames == 1 && finished == 1);
    return 0;
}
""", encoding="utf-8", newline="\n")
        binary = self.src / "next_job"
        result = run("gcc", "-std=gnu99", "-w", "-I", str(SIM.parent), str(program), "-o", str(binary))
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run(str(binary))
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
