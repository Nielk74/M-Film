"""CR3 decoder checks against LibRaw, on real EOS R files from raw.pixls.us (CC0).

FOS_CR3_DIR: a folder that already has the samples. Otherwise they are fetched
into ~/.cache/mfilm-cr3 and checked against the hashes below. Without them the
tests skip, unless FOS_CR3_REQUIRED is set (CI), when they fail.
"""

import hashlib
import os
from pathlib import Path
import random
import re
import subprocess
import tempfile
import unittest
import urllib.parse
import urllib.request

import numpy
from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
SIM = ROOT / "ml-dev/filmos/sim"
BASE = "https://raw.pixls.us/data/Canon/EOS R/"
FINE_GRAIN = "gold200"
SAMPLES = {
    "Canon_EOS_R_RAW_ISO_100.CR3": "89bd55532b0cceb5efa360e97827ba35c2076bce0de4675f3a4413a883dac880",
    "Canon_EOS_R_CRAW_ISO_100.CR3": "1b3efe0664a28a0decb53e225cbfecdbc0dd27d85abdc4683fc2a4add5129b4d",
}


def run(*args, cwd=ROOT, timeout=600):
    return subprocess.run([str(a) for a in args], cwd=cwd, text=True, capture_output=True, timeout=timeout)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sample(name):
    """The sample's path, fetched if need be."""
    folder = Path(os.environ.get("FOS_CR3_DIR", Path.home() / ".cache/mfilm-cr3"))
    path = folder / name
    if not path.exists() and "FOS_CR3_DIR" not in os.environ:
        folder.mkdir(parents=True, exist_ok=True)
        url = urllib.parse.quote(BASE + name, safe=":/")
        try:
            urllib.request.urlretrieve(url, path)
        except OSError as e:
            path.unlink(missing_ok=True)
            return unavailable(f"can't fetch {name}: {e}")
    if not path.exists():
        return unavailable(f"no {name} in {folder}")
    if sha256(path) != SAMPLES[name]:
        raise AssertionError(f"{path} is not the expected file")
    return path


def unavailable(why):
    if os.environ.get("FOS_CR3_REQUIRED"):
        raise AssertionError(why)
    raise unittest.SkipTest(why)


class Cr3DecoderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = run("make", "-C", SIM, "-j4", "out/devtest-r")
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.work = Path(cls.tmp.name)
        cls.addClassCleanup(cls.tmp.cleanup)
        result = run("gcc", "-O2", SIM / "tools/rawdump.c", "-lraw", "-o", cls.work / "rawdump")
        if result.returncode:
            unavailable("no LibRaw: " + result.stderr)

    def check_every_sample(self, name):
        cr3 = sample(name)
        truth = self.work / (name + ".u16")
        result = run(self.work / "rawdump", cr3, truth)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run(SIM / "out/devtest-r", "-r", truth, cr3, self.work / (name + ".FOP"), cwd=SIM)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(" 0 wrong", result.stdout)

    def test_raw_samples_match_libraw(self):
        self.check_every_sample("Canon_EOS_R_RAW_ISO_100.CR3")

    def test_craw_is_refused_with_a_clear_reason(self):
        cr3 = sample("Canon_EOS_R_CRAW_ISO_100.CR3")
        result = run(SIM / "out/devtest-r", cr3, self.work / "craw.FOP", cwd=SIM)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("C-RAW isn't supported yet", result.stderr)

    def test_latent_image_comes_up_from_the_top(self):
        # Decoding goes row by row, all planes together: 44 % through, the top
        # half is coming up and nothing of the bottom half has been read.
        cr3 = sample("Canon_EOS_R_RAW_ISO_100.CR3")
        result = run(SIM / "out/devtest-r", "-P", "350", cr3, self.work / "preview.FOP", cwd=SIM)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        match = re.search(r"preview at \d+: (\d+) top, (\d+) bottom, of (\d+) each", result.stdout)
        self.assertIsNotNone(match, result.stdout)
        top, bottom, each = (int(g) for g in match.groups())
        self.assertGreater(top, each // 4, result.stdout)  # the brightest parts look like paper
        self.assertEqual(bottom, 0, result.stdout)


class BigJpegTests(unittest.TestCase):
    """The EOS R's JPEG is bigger than the print on its screen."""

    @classmethod
    def setUpClass(cls):
        result = run("make", "-C", SIM, "-j4", "out/devtest-r")
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.work = Path(cls.tmp.name)
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.cr3 = sample("Canon_EOS_R_RAW_ISO_100.CR3")

    def develop(self, cr3, name, env=None, extra=()):
        jpeg, ppm = self.work / (name + ".jpg"), self.work / (name + ".ppm")
        result = subprocess.run(
            [str(SIM / "out/devtest-r"), *extra, "-j", str(jpeg), str(cr3), str(self.work / (name + ".FOP")), str(ppm)],
            cwd=SIM, text=True, capture_output=True, timeout=300, env={**os.environ, **(env or {})})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return jpeg, ppm, result

    def test_jpeg_is_half_the_sensor_and_print_stays_720_by_480(self):
        jpeg, ppm, result = self.develop(self.cr3, "big")
        self.assertEqual(Image.open(jpeg).size, (3360, 2240))
        self.assertIn("developing at 3360 x 2240", result.stderr)
        self.assertEqual(Image.open(ppm).size, (720, 480))
        self.assertEqual((self.work / "big.FOP").stat().st_size, 375542)

    def test_jpeg_really_has_more_detail_than_the_small_one(self):
        # Against the print-size JPEG enlarged: not just the same picture, bigger.
        # (Not the print itself: its palette's dither reads as detail.) Grain would
        # too, so this film, which has the least of it, and only the sharpest edges.
        jpeg, _, _ = self.develop(self.cr3, "detail", extra=("-f", FINE_GRAIN))
        small_jpeg, _, _ = self.develop(self.cr3, "detail-small", env={"FOS_ALLOC_MAX": "8000000"},
                                        extra=("-f", FINE_GRAIN))
        big = numpy.asarray(Image.open(jpeg).convert("L"), dtype=float)
        small = numpy.asarray(Image.open(small_jpeg).convert("L").resize((3360, 2240), Image.BILINEAR), dtype=float)
        def sharpest(a):
            return numpy.percentile(numpy.abs(numpy.diff(a, axis=1)), 99.5)
        self.assertGreater(sharpest(big), sharpest(small) * 1.5)

    def test_print_and_jpeg_look_the_same(self):
        jpeg, ppm, _ = self.develop(self.cr3, "same")
        big = numpy.asarray(Image.open(jpeg).convert("RGB").resize((720, 480), Image.BOX), dtype=float)
        small = numpy.asarray(Image.open(ppm).convert("RGB"), dtype=float)
        self.assertLess(numpy.abs(big - small).mean(), 8)
        self.assertLess(numpy.abs(big.mean(axis=(0, 1)) - small.mean(axis=(0, 1))).max(), 3)

    def test_film_look_does_not_change_with_the_size(self):
        # the same frame at both sizes: exposure, colour, vignette and halation agree
        jpeg, _, _ = self.develop(self.cr3, "look-big")
        small_jpeg, _, result = self.develop(self.cr3, "look-small", env={"FOS_ALLOC_MAX": "8000000"})
        self.assertEqual(Image.open(small_jpeg).size, (720, 480), result.stderr)
        big = numpy.asarray(Image.open(jpeg).convert("RGB").resize((720, 480), Image.BOX), dtype=float)
        small = numpy.asarray(Image.open(small_jpeg).convert("RGB"), dtype=float)
        self.assertLess(numpy.abs(big.mean(axis=(0, 1)) - small.mean(axis=(0, 1))).max(), 3)
        self.assertLess(numpy.abs(big - small).mean(), 8)

    def test_no_single_big_block_is_needed(self):
        # a real EOS R refused one block of 40 MB, and gave 21 MB
        jpeg, ppm, result = self.develop(self.cr3, "pieces", env={"FOS_ALLOC_MAX": "21000000"})
        self.assertEqual(Image.open(jpeg).size, (3360, 2240), result.stderr)
        self.assertEqual(Image.open(ppm).size, (720, 480))

    def test_pieces_join_without_a_seam(self):
        # the same picture as one developed in other pieces: nothing lost between them
        jpeg, _, _ = self.develop(self.cr3, "seam")
        pixels = numpy.asarray(Image.open(jpeg).convert("L"), dtype=float)
        # rows either side of each piece's edge differ no more than other neighbours do
        steps = numpy.abs(numpy.diff(pixels, axis=0)).mean(axis=1)
        self.assertLess(steps.max(), numpy.median(steps) * 4)

    def test_log_says_where_the_time_went(self):
        _, _, result = self.develop(self.cr3, "timing")
        self.assertRegex(result.stderr, r"dev: \d+ ms decoding \(\d+ reading the card\), \d+ film, \d+ JPEG, \d+ print")

    def test_it_steps_down_to_the_size_memory_allows(self):
        for limit, size in ((60000000, (3360, 2240)), (45000000, (2240, 1494)), (30000000, (1620, 1080))):
            with self.subTest(limit=limit):
                jpeg, ppm, result = self.develop(self.cr3, f"step{limit}", env={"FOS_ALLOC_TOTAL": str(limit)})
                self.assertEqual(Image.open(jpeg).size, size, result.stderr)
                self.assertEqual(Image.open(ppm).size, (720, 480))
                self.assertIn(f"developing at {size[0]} x {size[1]}", result.stderr)

    def test_short_of_memory_it_still_develops_at_print_size(self):
        jpeg, ppm, result = self.develop(self.cr3, "short", env={"FOS_ALLOC_MAX": "8000000"})
        self.assertEqual(Image.open(jpeg).size, (720, 480))
        self.assertEqual(Image.open(ppm).size, (720, 480))
        self.assertIn("720 x 480", result.stderr)

    def test_vertical_frame(self):
        # the sample with its orientation set to 6 (rotated), in CMT1's IFD
        data = bytearray(self.cr3.read_bytes())
        base = data.find(b"CMT1") + 4
        ifd = int.from_bytes(data[base + 4:base + 8], "little")
        count = int.from_bytes(data[base + ifd:base + ifd + 2], "little")
        for i in range(count):
            entry = base + ifd + 2 + 12 * i
            if int.from_bytes(data[entry:entry + 2], "little") == 0x0112:
                data[entry + 8] = 6
                break
        else:
            self.fail("no orientation tag")
        path = self.work / "vertical.CR3"
        path.write_bytes(data)
        jpeg, ppm, _ = self.develop(path, "vertical")
        self.assertEqual(Image.open(jpeg).size, (1493, 2240))
        shown = numpy.asarray(Image.open(ppm).convert("L"), dtype=float)
        self.assertEqual(shown.shape, (480, 720))
        self.assertLess(shown[:, :190].mean(), 40)      # beside the picture: the darkroom's black
        self.assertGreater(shown[:, 210:510].mean(), 40)

    def test_latent_image_still_comes_up_at_the_bigger_size(self):
        _, _, result = self.develop(self.cr3, "preview", extra=("-P", "350"))
        match = re.search(r"preview at \d+: (\d+) top, (\d+) bottom, of (\d+) each", result.stdout)
        self.assertIsNotNone(match, result.stdout)
        top, bottom, each = (int(g) for g in match.groups())
        self.assertGreater(top, each // 4, result.stdout)
        self.assertEqual(bottom, 0, result.stdout)


class SameOutputTests(unittest.TestCase):
    """Speed work may not change one byte of what comes out."""

    # sha256 (first 20) of the JPEG and the print, as developed before any speed work.
    # They came out the same from this PC's build and from emulated ARM.
    EXPECTED = [
        ("portra400", {}, False, "f6e4d6c018186ef7b67f", "7030e1245fa37c7393c5"),
        ("gold200", {}, False, "11fe93c1d274020367e3", "40bebf7bc36140659ca3"),
        ("cinestill800t", {}, False, "ae3a332999e9ffe3fafc", "af40a97e6b2181dfd0de"),
        ("portra400", {}, True, "baf2b5ec2ce965cc74d8", "fd7160439334bf2a08c1"),
        ("portra400", {"FOS_ALLOC_TOTAL": "45000000"}, False, "f78ff99654be701172c2", None),
        ("portra400", {"FOS_ALLOC_TOTAL": "30000000"}, False, "b262b454a62d0e2a2d67", None),
        ("portra400", {"FOS_ALLOC_TOTAL": "8000000"}, False, "d7b8beab7dcf9156ffbf", None),
    ]

    def test_every_size_and_film_is_byte_identical(self):
        result = run("make", "-C", SIM, "-j4", "out/devtest-r")
        self.assertEqual(result.returncode, 0, result.stderr)
        good = sample("Canon_EOS_R_RAW_ISO_100.CR3").read_bytes()
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            (work / "wide.CR3").write_bytes(good)
            tall = bytearray(good)
            base = tall.find(b"CMT1") + 4
            ifd = int.from_bytes(tall[base + 4:base + 8], "little")
            for i in range(int.from_bytes(tall[base + ifd:base + ifd + 2], "little")):
                entry = base + ifd + 2 + 12 * i
                if int.from_bytes(tall[entry:entry + 2], "little") == 0x0112:
                    tall[entry + 8] = 6
            (work / "tall.CR3").write_bytes(tall)
            for film, env, vertical, jpeg_hash, print_hash in self.EXPECTED:
                with self.subTest(film=film, env=env, vertical=vertical):
                    source = work / ("tall.CR3" if vertical else "wide.CR3")
                    done = subprocess.run(
                        [str(SIM / "out/devtest-r"), "-f", film, "-j", str(work / "o.jpg"), str(source), str(work / "o.FOP")],
                        cwd=SIM, text=True, capture_output=True, timeout=300, env={**os.environ, **env})
                    self.assertEqual(done.returncode, 0, done.stderr)
                    self.assertEqual(sha256(work / "o.jpg")[:20], jpeg_hash)
                    if print_hash:
                        self.assertEqual(sha256(work / "o.FOP")[:20], print_hash)


class Cr3DamageTests(unittest.TestCase):
    """The camera must report a damaged file, never run off its buffers."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.work = Path(cls.tmp.name)
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.good = sample("Canon_EOS_R_RAW_ISO_100.CR3").read_bytes()
        result = run("make", "-C", SIM, "out/FOSFILMS.BIN")
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        sources = ["devtest.c", "port_files.c", "films.c"] + [
            f"../{n}.c" for n in ("fos_dev", "fos_crx", "fos_print", "fos_looks", "fos_film", "fos_gfx", "fos_jpeg")]
        version = (ROOT / "VERSION").read_text().strip()
        result = run("gcc", "-std=gnu99", "-O1", "-g", "-fsanitize=address", "-fno-omit-frame-pointer",
                     "-DFOS_DEV_TEST", "-DCONFIG_R", f'-DMFILM_VERSION="{version}"', *sources, "-lm",
                     "-o", cls.work / "devtest-asan", cwd=SIM)
        if result.returncode:
            raise RuntimeError(result.stderr)

    def develop(self, data, name):
        path = self.work / name
        path.write_bytes(data)
        result = run(self.work / "devtest-asan", "-j", self.work / (name + ".jpg"), path, self.work / (name + ".FOP"),
                     cwd=SIM, timeout=300)
        self.assertNotIn("AddressSanitizer", result.stderr, name)
        self.assertIn(result.returncode, (0, 1), name + ": " + result.stderr[-500:])
        return result

    def test_untouched_file_develops(self):
        self.assertEqual(self.develop(self.good, "good.CR3").returncode, 0)

    def test_every_size_stays_inside_its_buffers(self):
        for limit in ("999999999", "60000000", "45000000", "30000000", "8000000"):
            with self.subTest(limit=limit, vertical=False):
                path = self.work / "sizes.CR3"
                path.write_bytes(self.good)
                result = subprocess.run(
                    [str(self.work / "devtest-asan"), "-j", str(self.work / "sizes.jpg"), str(path), str(self.work / "sizes.FOP")],
                    cwd=SIM, text=True, capture_output=True, timeout=300, env={**os.environ, "FOS_ALLOC_TOTAL": limit})
                self.assertNotIn("AddressSanitizer", result.stderr)
                self.assertEqual(result.returncode, 0, result.stderr[-500:])

    def test_truncated_files_are_refused(self):
        for keep in (16, 40, 2000, 30000, 330000, 3400000, 20000000):
            with self.subTest(keep=keep):
                result = self.develop(self.good[:keep], f"cut{keep}.CR3")
                self.assertEqual(result.returncode, 1, result.stdout)

    def test_flipped_bytes_never_overrun(self):
        # In the boxes, the CRX header, and the coded planes
        rng = random.Random(1234)
        spans = [(24, 31000), (0x32f058, 0x32f058 + 216), (0x32f058 + 216, 0x32f058 + 3000000)]
        for trial in range(30):
            data = bytearray(self.good)
            low, high = spans[trial % len(spans)]
            for _ in range(1 + trial % 12):
                data[rng.randrange(low, high)] = rng.randrange(256)
            with self.subTest(trial=trial):
                self.develop(bytes(data), f"bad{trial}.CR3")


class RollLogTests(unittest.TestCase):
    """The roll log's file name, against what is really on the card."""

    def frames(self, logged, *files):
        with tempfile.TemporaryDirectory() as work:
            card = Path(work)
            folder = card / "DCIM/100EOS_R"
            folder.mkdir(parents=True)
            (folder / "ROLL_001.TXT").write_text(
                "roll = 001\nfilm = portra400\nexposures = 36\nloaded = 2026-11-02 01:28:29\n\n"
                f"1  {logged}  2026-11-02 01:29:13  f/2.8\n", encoding="ascii")
            for name in files:
                (folder / name).write_bytes(b"x")
            program = card / "lab.c"
            program.write_text('''#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "fos.h"
#include "fos_dev.h"
void *fos_port_alloc(int n) { return malloc(n); }
void fos_port_free(void *p) { free(p); }
void fos_port_log(const char *m) { (void) m; }
void fos_port_clock(char *b, int n) { snprintf(b, n, "00:00"); }
void fos_port_roll(struct fos_roll *r) { memset(r, 0, sizeof(*r)); }
void sim_films(void);
int main(void) {
    static struct fos_lab_frame f[FOS_MAX_FRAMES];
    sim_films();
    int n = fos_lab_frames(1, f, FOS_MAX_FRAMES);
    for (int i = 0; i < n; i++) printf("%s missing=%d\\n", strrchr(f[i].cr2, '/') + 1, f[i].missing);
    return 0;
}
''', encoding="utf-8", newline="\n")
            parent = SIM.parent
            version = (ROOT / "VERSION").read_text().strip()
            result = run("gcc", "-std=gnu99", "-O1", f'-DMFILM_VERSION="{version}"', "-DCONFIG_R", "-Wno-implicit-function-declaration",
                         "-I", parent, "-I", SIM, program, SIM / "port_files.c", SIM / "films.c", parent / "fos_lab.c",
                         parent / "fos_film.c", parent / "fos_gfx.c", parent / "fos_print.c", "-lm",
                         "-o", card / "lab", cwd=SIM)
            self.assertEqual(result.returncode, 0, result.stderr)
            env_card = str(card) + "/"
            result = subprocess.run([str(card / "lab")], cwd=SIM, text=True, capture_output=True, timeout=60,
                                    env={**os.environ, "FOS_CARD": env_card})
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.strip()

    def test_log_name_on_the_card_is_used(self):
        self.assertEqual(self.frames("IMG_8714.CR3", "IMG_8714.CR3"), "IMG_8714.CR3 missing=0")

    def test_wrong_prefix_finds_the_file_by_its_number(self):
        # the real log from an EOS R, before the prefix was fixed
        self.assertEqual(self.frames("495A8714.CR3", "IMG_8714.CR3", "IMG_8715.CR3", "IMG_8714.JPG"),
                         "IMG_8714.CR3 missing=0")

    def test_adobe_rgb_name_is_still_found(self):
        self.assertEqual(self.frames("IMG_8714.CR3", "_MG_8714.CR3"), "_MG_8714.CR3 missing=0")

    def test_really_missing_frame_stays_missing(self):
        self.assertIn("missing=1", self.frames("IMG_8714.CR3", "IMG_8715.CR3"))


class OtherCamerasTests(unittest.TestCase):
    def test_cr3_code_stays_out_of_other_cameras_builds(self):
        # Their boot memory is tight: without CONFIG_R nothing may need fos_crx.
        with tempfile.TemporaryDirectory() as work:
            obj = Path(work) / "fos_dev.o"
            result = run("gcc", "-std=gnu99", "-O1", '-DMFILM_VERSION="x"', "-I", SIM.parent,
                         "-c", SIM.parent / "fos_dev.c", "-o", obj)
            self.assertEqual(result.returncode, 0, result.stderr)
            symbols = run("nm", obj).stdout
            self.assertNotIn("fos_crx", symbols)
            self.assertNotIn("fos_cr3", symbols)


if __name__ == "__main__":
    unittest.main()
