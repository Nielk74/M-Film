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
SHADOW_WARMTH = -3.0     # red less blue along the grey ramp: the table leans cool, against warm light
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


def install_tables(folder):
    """The EOS R's films, as its build puts them on the card; only those from recipes, unless folder has others."""
    folder.mkdir(exist_ok=True)
    result = run("python3", ROOT / "ml-dev/films/eos_r/install.py", folder)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)


class EosRFilmTests(unittest.TestCase):
    """The EOS R's films: the shot's white balance, a quarter of the scanner, their tables, film grain."""

    TABLES = {"portra400": "d5246fdb5ef9fe35", "gold200": "de1be4c0d476c648", "cinestill800t": "2a5ebccccc1addd8"}
    GRAIN = "5589986eed795fe7"

    @classmethod
    def setUpClass(cls):
        result = run("make", "-C", SIM, "-j4", "out/devtest-r")
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.work = Path(cls.tmp.name)
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.cr3 = sample("Canon_EOS_R_RAW_ISO_100.CR3")

    def develop(self, film, tables=None, source=None):
        env = {**os.environ}
        if tables:
            env["FOS_LUT_DIR"] = str(tables)
        return subprocess.run(
            [str(SIM / "out/devtest-r"), "-f", film, "-j", str(self.work / "o.jpg"), str(source or self.cr3), str(self.work / "o.FOP")],
            cwd=SIM, text=True, capture_output=True, timeout=300, env=env)

    def test_white_balance_is_libraws(self):
        result = run("gcc", "-O2", SIM / "tools/rawdump.c", "-lraw", "-o", self.work / "rawdump")
        if result.returncode:
            unavailable("no LibRaw: " + result.stderr)
        result = run(self.work / "rawdump", self.cr3, self.work / "o.u16")
        self.assertEqual(result.returncode, 0, result.stderr)
        r, g, b, _ = (float(v) for v in re.search(r"cam_mul (\S+) (\S+) (\S+) (\S+)", result.stdout).groups())
        result = self.develop("portra400")
        self.assertEqual(result.returncode, 0, result.stderr)
        got = re.search(r"as shot: (\S+) (\S+)", result.stderr)
        self.assertIsNotNone(got, result.stderr)
        self.assertAlmostEqual(float(got.group(1)), r / g, places=3)
        self.assertAlmostEqual(float(got.group(2)), b / g, places=3)

    def test_every_film_takes_the_shots_white_balance(self):
        for film in ("portra400", "cinestill800t", "velvia50", "hp5"):
            result = self.develop(film)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("as shot:", result.stderr)

    def test_a_file_without_it_develops_at_daylight(self):
        # another version of Canon's colour data: its numbers may be elsewhere
        data = bytearray(self.cr3.read_bytes())
        data[len(data) - 41680 + 1884 + 46] = 99
        path = self.work / "other.CR3"
        path.write_bytes(data)
        result = self.develop("portra400", source=path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("as shot:", result.stderr)

    def test_the_tables_are_the_approved_ones(self):
        for film, expected in self.TABLES.items():
            table = ROOT / f"ml-dev/films/eos_r/{film}.lut"
            self.assertEqual(table.stat().st_size, 33 ** 3 * 3, film)
            self.assertEqual(sha256(table)[:16], expected, film)

    def test_every_other_film_is_rebased_once(self):
        # the plain tables, as the build mixes them for any camera
        sources = ["devtest.c", "port_files.c", "films.c"] + [
            f"../{n}.c" for n in ("fos_dev", "fos_print", "fos_looks", "fos_film", "fos_gfx", "fos_jpeg")]
        plain = self.work / "devtest-plain"
        result = run("gcc", "-O2", "-std=gnu99", "-DFOS_DEV_TEST", "-I..", *sources, "-lm", "-o", plain, cwd=SIM)
        self.assertEqual(result.returncode, 0, result.stderr)
        tables = self.work / "all"
        tables.mkdir()
        result = run(plain, "-T", tables, cwd=SIM)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list(tables.glob("LOOK*.LUT"))), 72)
        before = (tables / "LOOK02.LUT").read_bytes()
        install_tables(tables)
        after = (tables / "LOOK02.LUT").read_bytes()
        self.assertNotEqual(before, after)
        self.assertEqual(before[4:8], bytes(a ^ b for a, b in zip(after[4:8], b"\x46\0\0\0")))
        install_tables(tables)
        self.assertEqual((tables / "LOOK02.LUT").read_bytes(), after, "installing twice changes nothing")
        for film, slide_or_mono in (("ektar100", ""), ("velvia50", "slide"), ("hp5", "mono"), ("kodak2383", "")):
            with self.subTest(film=film):
                result = self.develop(film, tables)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("isn't on the card", result.stdout + result.stderr)
                picture = numpy.asarray(Image.open(self.work / "o.jpg").convert("RGB")).astype(int)
                luma = picture.mean(axis=2)
                # no film may come out flat, or without a white or a black
                self.assertGreater(numpy.percentile(luma, 99), 170, film)
                self.assertLess(numpy.percentile(luma, 2), 40, film)
                colour = numpy.abs(picture[..., 0] - picture[..., 2]).mean()
                if slide_or_mono == "mono":
                    self.assertLess(colour, 1.5, film)
                else:
                    self.assertGreater(colour, 3, film)

    def test_the_card_table_is_used_and_an_old_one_is_not(self):
        tables = self.work / "tables"
        install_tables(tables)
        self.assertEqual((tables / "LOOK00.LUT").stat().st_size, 8 + 33 ** 3 * 3)
        self.assertEqual((tables / "LOOK00.LUT").read_bytes()[8:], (ROOT / "ml-dev/films/eos_r/portra400.lut").read_bytes())
        result = self.develop("portra400", tables)
        self.assertEqual(result.returncode, 0, result.stderr)
        with_table = sha256(self.work / "o.jpg")

        # a card's table from before, the same numbers under the look's old mark, isn't taken for it
        old = self.work / "old"
        old.mkdir()
        made = (tables / "LOOK00.LUT").read_bytes()
        (old / "LOOK00.LUT").write_bytes(made[:4] + bytes([made[4] ^ 0x46]) + made[5:])
        result = self.develop("portra400", old)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(sha256(self.work / "o.jpg"), with_table)

    def test_the_grain_is_the_cards_and_it_can_go_missing(self):
        grain = ROOT / "ml-dev/films/eos_r/grain.bin"
        self.assertEqual(grain.stat().st_size, 2 * (2 * 128 * 128 + 256))
        self.assertEqual(sha256(grain)[:16], self.GRAIN)
        tables = self.work / "grain"
        install_tables(tables)
        self.assertEqual(self.develop("portra400", tables).returncode, 0)
        film = sha256(self.work / "o.jpg")
        self.assertEqual(self.develop("gold200", tables).returncode, 0)
        path = tables / "GRAIN.BIN"
        whole = path.read_bytes()
        outputs = set()
        for damaged in (b"", whole[:1000], b"XXXX" + whole[4:]):
            path.write_bytes(damaged)
            result = self.develop("portra400", tables)
            self.assertEqual(result.returncode, 0, result.stderr)
            outputs.add(sha256(self.work / "o.jpg"))
        self.assertEqual(len(outputs), 1, "all of these fall back to the same noise")
        self.assertNotIn(film, outputs)

    def grain_tile(self, at):
        grain = (ROOT / "ml-dev/films/eos_r/grain.bin").read_bytes()
        return numpy.frombuffer(grain, numpy.int8, 128 * 128, at).reshape(128, 128).astype(float)

    def test_the_grain_is_uneven_and_has_no_regular_structure(self):
        for name, at, strength in (("fine", 0, 41.6), ("coarse", 2 * 128 * 128 + 256, 38.4)):
            tile = self.grain_tile(at)
            self.assertAlmostEqual(tile.std(), strength, delta=0.6, msg=name)
            z = (tile - tile.mean()) / tile.std()
            for lag in (2, 3, 4):
                correlation = (z * numpy.roll(z, lag, 1)).mean()
                self.assertGreater(correlation, -0.02, f"{name}: a dip at {lag} pixels reads as a regular pattern")
            self.assertGreater((z ** 4).mean() - 3, 0.2, f"{name}: real grain has its stronger specks")
            # the tile wraps: 8 x 8 patches, from all the tile's positions
            patches = numpy.stack([numpy.roll(numpy.roll(z * z, -y, 0), -x, 1)[:8, :8].mean()
                                   for y in range(0, 128, 8) for x in range(0, 128, 8)]) ** 0.5
            self.assertGreater(patches.std() / patches.mean(), 0.15, f"{name}: some places busier than others")

    def test_the_grain_fades_out_only_right_at_black(self):
        grain = (ROOT / "ml-dev/films/eos_r/grain.bin").read_bytes()
        for name, at, at_30 in (("fine", 2 * 128 * 128, 133), ("coarse", 2 * 128 * 128 + 256 + 2 * 128 * 128, 150)):
            tone = grain[at:at + 256]
            self.assertLess(tone[3], 5, name)
            self.assertGreater(tone[12], 50, name)
            self.assertEqual(tone[30], at_30, name)
            self.assertEqual(tone[128], 128, name)

    def test_portra_shadows_are_less_warm(self):
        table = numpy.frombuffer((ROOT / "ml-dev/films/eos_r/portra400.lut").read_bytes(), numpy.uint8).reshape(33, 33, 33, 3).astype(float)
        grey = numpy.stack([table[i, i, i] for i in range(33)])
        luma = grey @ numpy.array((0.299, 0.587, 0.114))
        shadows = (luma >= 20) & (luma < 60)
        self.assertGreaterEqual(shadows.sum(), 2)
        self.assertLess((grey[:, 0] - grey[:, 2])[shadows].mean(), SHADOW_WARMTH)

    def test_mixing_without_the_table_never_takes_its_place(self):
        empty = self.work / "empty"
        empty.mkdir()
        result = self.develop("portra400", empty)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((empty / "LOOK00.LUT").exists())


class FinishedNoiseTests(unittest.TestCase):
    """A high ISO's static isn't film's. In a big frame's JPEG the colour noise is calmed, wherever the
    colour isn't really changing, and the pixel-sharp noise softened, wherever there's no edge."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.work = Path(cls.tmp.name)
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.binary = cls.work / "jpegtest"
        result = run("gcc", "-std=gnu99", "-O2", "-DCONFIG_R", "jpegtest.c", "port_files.c", "../fos_jpeg.c",
                     "-lm", "-pthread", "-o", cls.binary, cwd=SIM)
        if result.returncode:
            raise RuntimeError(result.stderr)

    def picture(self, width, height):
        """Left half dark, right half bright, both with the same noise; a red square in the dark,
        real colour stripes in the bright."""
        rng = numpy.random.default_rng(8736)
        cells = lambda sd: numpy.kron(rng.normal(0, sd, (height // 2, width // 2)), numpy.ones((2, 2)))
        y = numpy.full((height, width), 26.0)
        y[:, width // 2:] = 150
        y += rng.normal(0, 3, (height, width))
        cb, cr = cells(6), cells(6)
        rgb = numpy.stack((y + 1.402 * cr, y - 0.34414 * cb - 0.71414 * cr, y + 1.772 * cb), axis=2)
        rgb[304:496, 304:496] = (255, 30, 30)       # on the 16 x 16 grid, so no block rings past it
        for x in range(width * 3 // 4, width - 64, 64):
            rgb[height // 8:height // 4, x:x + 32] = (220, 90, 90)
        return numpy.clip(numpy.round(rgb), 0, 255).astype(numpy.uint8)

    def write(self, width, height):
        raw, out = self.work / "in.rgb", self.work / "out.jpg"
        raw.write_bytes(self.picture(width, height).tobytes())
        done = run(self.binary, raw, width, height, out, cwd=SIM)
        self.assertEqual(done.returncode, 0, done.stderr)
        writes = int(done.stdout.split()[1])
        return numpy.asarray(Image.open(out).convert("YCbCr")).astype(float), writes, out.stat().st_size

    @staticmethod
    def next_pixel(y):
        r = y - y.mean()
        return (r[:, 1:] * r[:, :-1]).mean() / (r * r).mean()

    def test_colour_noise_is_calmed_at_every_brightness(self):
        a, _, _ = self.write(2400, 1600)
        dark, bright = a[900:1300, 800:1100], a[900:1300, 1500:1900]
        for channel, name in ((1, "Cb"), (2, "Cr")):
            self.assertLess(dark[:, :, channel].std(), 2.1, f"{name}, dark")
            self.assertLess(bright[:, :, channel].std(), 2.1, f"{name}, bright")

    def test_real_colour_stays(self):
        a, _, _ = self.write(2400, 1600)
        for x in range(1800, 2300, 64):
            stripe, gap = a[250:350, x + 12:x + 20, 2].mean(), a[250:350, x + 44:x + 52, 2].mean()
            self.assertGreater(stripe - gap, 30)

    def test_static_is_softened_and_edges_stay(self):
        a, _, _ = self.write(2400, 1600)
        for region, name in ((a[900:1300, 800:1100, 0], "dark"), (a[900:1300, 1500:1700, 0], "bright")):
            self.assertLess(region.std(), 2.4, name)
            self.assertGreater(self.next_pixel(region), 0.25, name)
        edge = a[900:1300, :, 0].mean(axis=0)
        self.assertLess(edge[1198], 45)
        self.assertGreater(edge[1201], 130)

    def test_a_bright_colour_does_not_bleed_into_the_black_beside_it(self):
        a, _, _ = self.write(2400, 1600)
        beside = a[340:460, 498:501, 2].mean()
        far = a[900:1300, 800:1100, 2].mean()
        self.assertLess(abs(beside - far), 3)

    def test_small_frames_have_averaged_it_already(self):
        a, _, _ = self.write(1200, 800)
        dark = a[500:700, 400:560]
        self.assertGreater(dark[:, :, 2].std(), 3.5)
        self.assertLess(self.next_pixel(dark[:, :, 0]), 0.2)

    def test_the_file_goes_to_the_card_in_big_pieces(self):
        _, writes, size = self.write(2400, 1600)
        self.assertLessEqual(writes, size // 32768 + 2, "the camera flushes its caches for every write")


class SameOutputTests(unittest.TestCase):
    """Speed work may not change one byte of what comes out."""

    # sha256 (first 20) of the JPEG and the print, with the card's tables. They came out the same
    # from this PC's build and from emulated ARM.
    EXPECTED = [
        ("portra400", {}, False, "facf4cc498f993de63e6", "51a173f480124dfe45f8"),
        ("gold200", {}, False, "2def2254088150747a1d", "c33709bc10ec0d850d25"),
        ("cinestill800t", {}, False, "fc0cb7bc3b604199b1e2", "17ea497682ac4a21cabe"),
        ("portra400", {}, True, "f7951a534576c4e945a0", "70c393b922c249eb3e9c"),
        ("portra400", {"FOS_ALLOC_TOTAL": "45000000"}, False, "21f1b130a9d5f0856f86", None),
        ("portra400", {"FOS_ALLOC_TOTAL": "30000000"}, False, "b5119b84c9ea09a84d99", None),
        ("portra400", {"FOS_ALLOC_TOTAL": "8000000"}, False, "032a1b5cdc7d1f0dbf9f", None),
    ]

    def develop_on(self, binary, work, env):
        done = subprocess.run(
            [str(binary), "-f", "portra400", "-j", str(work / "o.jpg"), str(work / "wide.CR3"), str(work / "o.FOP")],
            cwd=SIM, text=True, capture_output=True, timeout=590,
            env={**os.environ, "FOS_LUT_DIR": str(work / "tables"), **env})
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        return done.stderr, sha256(work / "o.jpg")[:20], sha256(work / "o.FOP")[:20]

    def test_the_second_core_takes_work_and_changes_no_byte(self):
        result = run("make", "-C", SIM, "-j4", "out/devtest-r")
        self.assertEqual(result.returncode, 0, result.stderr)
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            install_tables(work / "tables")
            (work / "wide.CR3").write_bytes(sample("Canon_EOS_R_RAW_ISO_100.CR3").read_bytes())
            log, jpeg, print_ = self.develop_on(SIM / "out/devtest-r", work, {})
            self.assertEqual((jpeg, print_), self.EXPECTED[0][3:5])
            self.assertRegex(log, r"other core: [1-9]\d* of \d+ rows of film")
            self.assertRegex(log, r"other core: [1-9]\d* of \d+ rows of the JPEG")
            self.assertRegex(log, r"other core: [1-9]\d* of 18184 lines of decoding")
            alone, jpeg, print_ = self.develop_on(SIM / "out/devtest-r", work, {"FOS_ONE_CORE": "1"})
            self.assertEqual((jpeg, print_), self.EXPECTED[0][3:5])
            self.assertRegex(alone, r"other core: 0 of \d+ rows of film")
            self.assertRegex(alone, r"other core: 0 of \d+ rows of the JPEG")
            self.assertRegex(alone, r"other core: 0 of 18184 lines of decoding")

    def test_decoding_on_two_cores_copes_with_a_slow_card_and_a_busy_first_core(self):
        result = run("make", "-C", SIM, "-j4", "out/devtest-r")
        self.assertEqual(result.returncode, 0, result.stderr)
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            install_tables(work / "tables")
            (work / "wide.CR3").write_bytes(sample("Canon_EOS_R_RAW_ISO_100.CR3").read_bytes())
            # the second core has to wait for the card...
            log, jpeg, print_ = self.develop_on(SIM / "out/devtest-r", work, {"FOS_SLOW_CARD": "20000"})
            self.assertEqual((jpeg, print_), self.EXPECTED[0][3:5])
            self.assertRegex(log, r"other core: [1-9]\d* of 18184 lines of decoding.*waited [1-9]\d* for the card")
            # ...and for the first core, which does the card and the print's sums, to make room
            log, jpeg, print_ = self.develop_on(SIM / "out/devtest-r", work, {"FOS_SLOW_LINES": "500"})
            self.assertEqual((jpeg, print_), self.EXPECTED[0][3:5])
            self.assertRegex(log, r"other core: [1-9]\d* of 18184 lines of decoding.*[1-9]\d* for room")
            # ...and when it is the one held up, the first core does the rest
            log, jpeg, print_ = self.develop_on(SIM / "out/devtest-r", work, {"FOS_SLOW_OTHER": "3000"})
            self.assertEqual((jpeg, print_), self.EXPECTED[0][3:5])
            other = int(re.search(r"other core: (\d+) of 18184 lines of decoding", log).group(1))
            self.assertGreater(other, 0)
            self.assertLess(other, 18184 // 2)

    def test_short_of_memory_for_two_cores_decoding_stays_on_one(self):
        result = run("make", "-C", SIM, "-j4", "out/devtest-r")
        self.assertEqual(result.returncode, 0, result.stderr)
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            install_tables(work / "tables")
            (work / "wide.CR3").write_bytes(sample("Canon_EOS_R_RAW_ISO_100.CR3").read_bytes())
            # every size of the block it asks for, and nothing else the frame needs
            log, jpeg, print_ = self.develop_on(SIM / "out/devtest-r", work, {"FOS_ALLOC_HOLE": "1060000-1300000"})
            self.assertEqual((jpeg, print_), self.EXPECTED[0][3:5])
            self.assertRegex(log, r"other core: 0 of 18184 lines of decoding")
            self.assertRegex(log, r"other core: [1-9]\d* of \d+ rows of film")

    def test_the_two_cores_share_nothing_unguarded(self):
        sources = ["devtest.c", "port_files.c", "films.c"] + [
            f"../{n}.c" for n in ("fos_dev", "fos_crx", "fos_print", "fos_looks", "fos_film", "fos_gfx", "fos_jpeg")]
        version = (ROOT / "VERSION").read_text().strip()
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            binary = work / "devtest-tsan"
            result = run("gcc", "-std=gnu99", "-O1", "-g", "-fsanitize=thread", "-no-pie", "-DFOS_DEV_TEST", "-DCONFIG_R",
                         f'-DMFILM_VERSION="{version}"', *sources, "-lm", "-pthread", "-o", binary, cwd=SIM)
            self.assertEqual(result.returncode, 0, result.stderr)
            install_tables(work / "tables")
            (work / "wide.CR3").write_bytes(sample("Canon_EOS_R_RAW_ISO_100.CR3").read_bytes())
            def develop(*args, **env):
                # Whether it can start at all depends on where this run's memory fell: it says so at once,
                # or dies before main without a word (an empty program does too). A real crash gets a report.
                for _ in range(200):
                    done = subprocess.run(
                        [str(binary), *args, "-f", "portra400", "-j", str(work / "o.jpg"), str(work / "wide.CR3"), str(work / "o.FOP")],
                        cwd=SIM, text=True, capture_output=True, timeout=590,
                        env={**os.environ, "FOS_LUT_DIR": str(work / "tables"), "TSAN_OPTIONS": "halt_on_error=1 exitcode=66", **env})
                    if "unexpected memory mapping" not in done.stderr and not (done.returncode < 0 and not done.stderr):
                        return done
                self.assertGreaterEqual(done.returncode, 0, "it crashes every time")
                self.skipTest("ThreadSanitizer can't map its memory on this kernel")
            done = develop()
            self.assertEqual(done.returncode, 0, done.stderr[:3000])
            self.assertNotIn("ThreadSanitizer", done.stderr)
            self.assertRegex(done.stderr, r"other core: [1-9]\d* of \d+ rows of film")
            self.assertRegex(done.stderr, r"other core: [1-9]\d* of 18184 lines of decoding")
            self.assertEqual(sha256(work / "o.jpg")[:20], self.EXPECTED[0][3])
            self.assertEqual(sha256(work / "o.FOP")[:20], self.EXPECTED[0][4])
            # a card that keeps it waiting, and leaving in the middle of a frame
            for args, env in (([], {"FOS_SLOW_CARD": "20000"}), (["-Q", "300"], {})):
                done = develop(*args, **env)
                with self.subTest(args=args, env=env):
                    self.assertEqual(done.returncode, 0, done.stderr[:3000])
                    self.assertNotIn("ThreadSanitizer", done.stderr)

    def test_every_size_and_film_is_byte_identical(self):
        result = run("make", "-C", SIM, "-j4", "out/devtest-r")
        self.assertEqual(result.returncode, 0, result.stderr)
        good = sample("Canon_EOS_R_RAW_ISO_100.CR3").read_bytes()
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            install_tables(work / "tables")
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
                        cwd=SIM, text=True, capture_output=True, timeout=300,
                        env={**os.environ, "FOS_LUT_DIR": str(work / "tables"), **env})
                    self.assertEqual(done.returncode, 0, done.stderr)
                    self.assertEqual(sha256(work / "o.jpg")[:20], jpeg_hash)
                    if print_hash:
                        self.assertEqual(sha256(work / "o.FOP")[:20], print_hash)


class DisplayOffTests(unittest.TestCase):
    """With Canon's display off while developing, the darkroom draws nothing and takes no time from the frame."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.work = Path(cls.tmp.name)
        cls.addClassCleanup(cls.tmp.cleanup)
        result = run("make", "-C", SIM, "-j4", "out/sim-r")
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        cls.good = sample("Canon_EOS_R_RAW_ISO_100.CR3").read_bytes()

    def develop(self, name, script, dark=None):
        """A card with a roll of two frames, developed in the simulator: its log, the JPEGs and prints."""
        card = self.work / name
        folder = card / "DCIM/100EOS_R"
        folder.mkdir(parents=True)
        install_tables_at = card / "ML"
        install_tables_at.mkdir()
        install_tables(install_tables_at / "DARKROOM")
        for frame in (1, 2):
            (folder / f"IMG_000{frame}.CR3").write_bytes(self.good)
        (folder / "ROLL_001.TXT").write_text(
            "roll = 001\nfilm = portra400\nexposures = 36\nloaded = 2026-11-02 01:28:29\n\n"
            "1  IMG_0001.CR3  2026-11-02 01:29:13  f/2.8\n2  IMG_0002.CR3  2026-11-02 01:29:20  f/2.8\n", encoding="ascii")
        scene = self.work / (name + ".txt")
        scene.write_text("open darkroom\n" + script, encoding="ascii")
        env = {**os.environ, "FOS_CARD": str(card) + "/"}
        if dark:
            env["FOS_DARK"] = dark
        done = subprocess.run([str(SIM / "out/sim-r"), str(scene), "-"], cwd=SIM, text=True, capture_output=True,
                              timeout=300, env=env)
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        self.assertIn("busy: developing", done.stderr)
        outputs = {path.relative_to(card).as_posix(): sha256(path)
                   for path in card.rglob("*") if path.suffix in (".JPG", ".BMP") and path.parent != card / "ML/DARKROOM"}
        return done.stderr, outputs

    SCRIPT = "300 set\n800 info\n50000000 end\n"     # it ends itself, when the roll is developed

    def test_nothing_is_drawn_and_the_frames_come_out_the_same(self):
        lit, lit_out = self.develop("lit", self.SCRIPT)
        self.assertEqual(len(lit_out), 5, "two JPEGs, two prints and the sheet: " + lit[-1000:])
        dark, dark_out = self.develop("dark", self.SCRIPT, dark="0-999999999")
        self.assertRegex(dark, r"dark: 0 presents")
        self.assertEqual(dark_out, lit_out)

    def test_the_whole_screen_comes_back_with_the_display(self):
        log, outputs = self.develop("back", self.SCRIPT, dark="1500-2500")
        self.assertEqual(len(outputs), 5)
        self.assertRegex(log, r"dark: 0 presents; after it: 1, 1 of the whole screen")

    def test_leaving_still_works_while_it_is_dark(self):
        log, outputs = self.develop("leave", "300 set\n800 info\n1500 home\n50000000 end\n", dark="1000-999999999")
        self.assertRegex(log, r"dark: \d+ presents")
        self.assertLess(int(re.search(r"(\d+) frames, (\d+) ms", log).group(2)), 50000000, "it never left")
        self.assertEqual(outputs, {}, "it left before a frame was done")


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
                     "-DFOS_DEV_TEST", "-DCONFIG_R", f'-DMFILM_VERSION="{version}"', *sources, "-lm", "-pthread",
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
        install_tables(self.work / "tables")
        for limit in ("999999999", "60000000", "45000000", "30000000", "8000000"):
            with self.subTest(limit=limit, vertical=False):
                path = self.work / "sizes.CR3"
                path.write_bytes(self.good)
                result = subprocess.run(
                    [str(self.work / "devtest-asan"), "-j", str(self.work / "sizes.jpg"), str(path), str(self.work / "sizes.FOP")],
                    cwd=SIM, text=True, capture_output=True, timeout=300,
                    env={**os.environ, "FOS_ALLOC_TOTAL": limit, "FOS_LUT_DIR": str(self.work / "tables")})
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
        ctmd = len(self.good) - 41680
        spans = [(24, 31000), (0x32f058, 0x32f058 + 216), (0x32f058 + 216, 0x32f058 + 3000000), (ctmd, ctmd + 2100)]
        for trial in range(36):
            data = bytearray(self.good)
            low, high = spans[trial % len(spans)]
            for _ in range(1 + trial % 12):
                data[rng.randrange(low, high)] = rng.randrange(256)
            with self.subTest(trial=trial):
                self.develop(bytes(data), f"bad{trial}.CR3")

    def test_damage_ends_the_same_on_one_core_and_on_two(self):
        # Damaged lines decode to something or stop with an error: the same something, the same error
        rng = random.Random(8736)
        coded = 0x32f058 + 216
        cases = {f"cut{keep}": self.good[:keep] for keep in (3400000, 20000000)}
        for trial in range(6):
            data = bytearray(self.good)
            for _ in range(1 + trial * 3):
                data[rng.randrange(coded, coded + 25000000)] = rng.randrange(256)
            cases[f"flip{trial}"] = bytes(data)
        for name, data in cases.items():
            path, ends = self.work / f"same{name}.CR3", []
            path.write_bytes(data)
            for env in ({}, {"FOS_ONE_CORE": "1"}, {"FOS_SLOW_CARD": "2000"}):
                jpeg, print_ = self.work / f"same{name}.jpg", self.work / f"same{name}.FOP"
                jpeg.unlink(missing_ok=True)
                print_.unlink(missing_ok=True)
                done = subprocess.run([str(self.work / "devtest-asan"), "-j", str(jpeg), str(path), str(print_)],
                                      cwd=SIM, text=True, capture_output=True, timeout=300, env={**os.environ, **env})
                self.assertNotIn("AddressSanitizer", done.stderr, name)
                last = done.stderr.strip().splitlines()[-1] if done.returncode else ""
                ends.append((done.returncode, last, sha256(jpeg) if jpeg.exists() else None,
                             sha256(print_) if print_.exists() else None))
            with self.subTest(case=name):
                self.assertEqual(ends[1], ends[0])
                self.assertEqual(ends[2], ends[0])

    def test_leaving_in_the_middle_of_decoding_waits_for_the_other_core(self):
        path = self.work / "leave.CR3"
        path.write_bytes(self.good)
        for at in (1, 100, 400, 790):
            with self.subTest(at=at):
                # It is held up inside a line, so a core not waited for would go on in memory given back
                done = subprocess.run(
                    [str(self.work / "devtest-asan"), "-Q", str(at), "-j", str(self.work / "leave.jpg"), str(path), str(self.work / "leave.FOP")],
                    cwd=SIM, text=True, capture_output=True, timeout=300, env={**os.environ, "FOS_SLOW_OTHER": "5000"})
                self.assertNotIn("AddressSanitizer", done.stderr)
                self.assertEqual(done.returncode, 0, done.stderr[-500:])
                self.assertIn("left at", done.stdout)


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

    def test_a_scan_needs_few_listings_and_sees_every_print(self):
        # On the test body, listings ran out part way through a scan: the print folders looked empty,
        # no developed roll showed, and "develop all" went for every roll, oldest first.
        with tempfile.TemporaryDirectory() as work:
            card = Path(work)
            folder = card / "DCIM/100EOS_R"
            folder.mkdir(parents=True)
            for roll in range(1, 9):
                (folder / f"ROLL_{roll:03d}.TXT").write_text(
                    f"roll = {roll:03d}\nfilm = portra400\nexposures = 36\nloaded = 2026-11-02 01:28:{roll:02d}\n\n"
                    f"1  IMG_{roll:04d}.CR3  2026-11-02 01:29:13  f/2.8\n", encoding="ascii")
                (folder / f"IMG_{roll:04d}.CR3").write_bytes(b"x")
                if roll in (6, 7):
                    continue            # shot, not developed
                prints = card / f"ML/DARKROOM/R{roll:03d}"
                prints.mkdir(parents=True)
                (prints / "F01.BMP").write_bytes(b"x")
                (prints / "SHEET.BMP").write_bytes(b"x")
                out = card / f"DCIM/Nov1102 Film{roll}"
                out.mkdir()
                (out / f"ROLL{roll:03d}.TXT").write_bytes(b"x")
                (out / f"IMG_{roll:04d}.JPG").write_bytes(b"x")
            program = card / "lab.c"
            program.write_text("""#include <stdio.h>
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
    static struct fos_lab_roll r[48];
    sim_films();
    int n = fos_lab_scan(r, 48);
    printf("rolls %d\\n", n);
    for (int i = 0; i < n; i++)
        if (!(r[i].sheet && r[i].developed >= r[i].shot && r[i].jpegs >= r[i].developed)) printf("waiting %d\\n", r[i].number);
    return 0;
}
""", encoding="utf-8", newline="\n")
            parent = SIM.parent
            version = (ROOT / "VERSION").read_text().strip()
            result = run("gcc", "-std=gnu99", "-O1", f'-DMFILM_VERSION="{version}"', "-DCONFIG_R", "-Wno-implicit-function-declaration",
                         "-I", parent, "-I", SIM, program, SIM / "port_files.c", SIM / "films.c", parent / "fos_lab.c",
                         parent / "fos_film.c", parent / "fos_gfx.c", parent / "fos_print.c", "-lm",
                         "-o", card / "lab", cwd=SIM)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(card / "lab")], cwd=SIM, text=True, capture_output=True, timeout=60,
                                    env={**os.environ, "FOS_CARD": str(card) + "/", "FOS_LIST_LIMIT": "3"})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.split("\n")[0], "rolls 8", result.stdout)
            self.assertEqual(sorted(line for line in result.stdout.split("\n") if line.startswith("waiting")),
                             ["waiting 6", "waiting 7"], result.stdout)

    def test_rolls_stay_known_and_up_to_date_when_listing_stops_working(self):
        # On the test body, after developing, listing DCIM stopped finding the logs: rolls just
        # developed stayed marked "to develop".
        with tempfile.TemporaryDirectory() as work:
            card = Path(work)
            folder = card / "DCIM/100EOS_R"
            folder.mkdir(parents=True)
            for roll in (1, 2, 12):
                (folder / f"ROLL_{roll:03d}.TXT").write_text(
                    f"roll = {roll:03d}\nfilm = gold200\nexposures = 36\nloaded = 2026-11-02 01:28:{roll:02d}\n\n"
                    f"1  IMG_{roll:04d}.CR3  2026-11-02 01:29:13  f/2.8\n", encoding="ascii")
                (folder / f"IMG_{roll:04d}.CR3").write_bytes(b"x")
            prints = card / "ML/DARKROOM/R012"
            prints.mkdir(parents=True)
            program = card / "lab.c"
            program.write_text("""#include <stdio.h>
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
static void show(const char *when) {
    static struct fos_lab_roll r[48];
    int n = fos_lab_scan(r, 48);
    printf("%s rolls %d:", when, n);
    for (int i = 0; i < n; i++) printf(" %d/%s/%d", r[i].number, fos_films[r[i].film].key, r[i].developed);
    printf("\\n");
}
int main(int argc, char **argv) {
    static struct fos_lab_frame f[FOS_MAX_FRAMES];
    sim_films();
    show("before");
    for (int i = 0; i < 2; i++) {
        char p[256];
        snprintf(p, sizeof(p), "%sML/DARKROOM/R012/%s", getenv("FOS_CARD"), i ? "SHEET.BMP" : "F01.BMP");
        FILE *o = fopen(p, "wb");
        fputs("x", o);
        fclose(o);
    }
    show("after");
    printf("frames %d\\n", fos_lab_frames(12, f, FOS_MAX_FRAMES));
    return 0;
}
""", encoding="utf-8", newline="\n")
            parent = SIM.parent
            version = (ROOT / "VERSION").read_text().strip()
            result = run("gcc", "-std=gnu99", "-O1", f'-DMFILM_VERSION="{version}"', "-DCONFIG_R", "-Wno-implicit-function-declaration",
                         "-I", parent, "-I", SIM, program, SIM / "port_files.c", SIM / "films.c", parent / "fos_lab.c",
                         parent / "fos_film.c", parent / "fos_gfx.c", parent / "fos_print.c", "-lm",
                         "-o", card / "lab", cwd=SIM)
            self.assertEqual(result.returncode, 0, result.stderr)
            # enough listings for the first scan, then none
            result = subprocess.run([str(card / "lab")], cwd=SIM, text=True, capture_output=True, timeout=60,
                                    env={**os.environ, "FOS_CARD": str(card) + "/", "FOS_LIST_LIMIT": "2"})
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = result.stdout.strip().split("\n")
            self.assertEqual(lines[0], "before rolls 3: 12/gold200/0 2/gold200/0 1/gold200/0", result.stdout)
            self.assertEqual(lines[1], "after rolls 3: 12/gold200/1 2/gold200/0 1/gold200/0", result.stdout)
            self.assertEqual(lines[2], "frames 1", result.stdout)

    def test_a_rolls_folder_is_found_without_listing(self):
        with tempfile.TemporaryDirectory() as work:
            card = Path(work)
            folder = card / "DCIM/100EOS_R"
            folder.mkdir(parents=True)
            for roll in (1, 2):
                (folder / f"ROLL_{roll:03d}.TXT").write_text(
                    f"roll = {roll:03d}\nfilm = gold200\nexposures = 36\nloaded = 2026-11-02 01:28:{roll:02d}\n\n"
                    f"1  IMG_{roll:04d}.CR3  2026-11-02 01:29:13  f/2.8\n", encoding="ascii")
            program = card / "lab.c"
            program.write_text("""#include <stdio.h>
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
    static struct fos_lab_roll r[48];
    char d[128];
    sim_films();
    fos_lab_scan(r, 48);
    for (int roll = 1; roll <= 2; roll++) {
        int rc = fos_lab_dcim_dir(d, sizeof(d), roll, 3, "2026-11-02 01:29");
        printf("%d %s\\n", rc, strrchr(d, '/') + 1);
    }
    int rc = fos_lab_dcim_dir(d, sizeof(d), 1, 3, "2026-11-02 01:29");
    printf("%d %s\\n", rc, strrchr(d, '/') + 1);
    return 0;
}
""", encoding="utf-8", newline="\n")
            parent = SIM.parent
            version = (ROOT / "VERSION").read_text().strip()
            result = run("gcc", "-std=gnu99", "-O1", f'-DMFILM_VERSION="{version}"', "-DCONFIG_R", "-Wno-implicit-function-declaration",
                         "-I", parent, "-I", SIM, program, SIM / "port_files.c", SIM / "films.c", parent / "fos_lab.c",
                         parent / "fos_film.c", parent / "fos_gfx.c", parent / "fos_print.c", "-lm",
                         "-o", card / "lab", cwd=SIM)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(card / "lab")], cwd=SIM, text=True, capture_output=True, timeout=60,
                                    env={**os.environ, "FOS_CARD": str(card) + "/", "FOS_LIST_LIMIT": "2"})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip().split("\n"),
                             ["0 Nov1102 Gold200", "0 Nov1102 Gold200 2", "0 Nov1102 Gold200"], result.stdout)
            self.assertTrue((card / "DCIM/Nov1102 Gold200/ROLL001.TXT").exists())
            self.assertTrue((card / "DCIM/Nov1102 Gold200 2/ROLL002.TXT").exists())

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
