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

    def test_eos_r_rejects_cr2_darkroom_before_any_edits(self):
        before = self.snapshot()
        result = self.patch("--experimental-eos-r")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.snapshot(), before)

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
        function = c_function(source, "static void apply_film(int index)")
        program = self.src / "picstyle_check.c"
        program.write_text('''#include <assert.h>
#include "fos.h"
#define CONFIG_R
#define COERCE(x, lo, hi) ((x) < (lo) ? (lo) : (x) > (hi) ? (hi) : (x))
#define PROP_PICTURE_STYLE 1
#define msleep(...) ((void)0)
#define NotifyBox(...) ((void)0)
#define dbg(...) ((void)0)
static struct fos_film stock = { .picstyle = PS_STANDARD };
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
const uint32_t * volatile filmroll_rgba_palette;
''' + dispatch + '''
int main(void) {
    uint32_t base[256] = { [1] = 0xffffff, [200] = 0xffffff, [254] = 0xffffff };
    uint32_t colors[256];
    fos_rgba_build_palette(colors, base, 0, 0, 256, 0, 0, 0, 0);
    filmroll_rgba_palette = colors;
    assert(indexed2rgb(0) == 0);
    assert(indexed2rgb(1) == 0xffffffff);
    assert(indexed2rgb(2) == 0xff000000);
    assert(indexed2rgb(200) == 0xffffffff);
    assert(indexed2rgb(254) == 0xffffffff);
    assert(indexed2rgb(255) == 0);
    const uint8_t photo[1][3] = { {255, 255, 255} };
    fos_rgba_build_palette(colors, base, photo, 1, 128, 0, 0, 0, 0);
    uint32_t mid = indexed2rgb(1) & 255;
    assert(mid >= 125 && mid <= 129);
    fos_rgba_build_palette(colors, base, photo, 1, 0, 0, 0, 0, 0);
    assert(indexed2rgb(1) == 0xff000000);
    assert(indexed2rgb(0) == 0);
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
