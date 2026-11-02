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
static struct fos_film stock = { .key = "gold200", .picstyle = PS_STANDARD, .iso = 400, .kelvin = 5500, .wbs_ba = 2, .wbs_gm = -1 };
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
    assert(strstr(notice, "ISO 400") != 0 && strstr(notice, "WB 5500K") != 0);
    assert(strstr(notice, "BA 2 GM -1") != 0);
    /* NotifyBox draws one unwrapped line of large text */
    assert(strlen(notice) <= 44);
    /* Portra 400 develops at the shot's white balance: nothing may ask for another one */
    stock.key = "portra400";
    apply_film(0);
    assert(strstr(notice, "ISO 400") != 0 && strstr(notice, "5500") == 0 && strstr(notice, "BA") == 0);
    assert(strlen(notice) <= 44);
    writes = 4;
    stock.key = "gold200";
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


if __name__ == "__main__":
    unittest.main()
