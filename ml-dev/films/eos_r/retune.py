"""Rebase a film's table on the EOS R: the look it has, over Canon's rendering.

A film's table runs from linear light on a 33-point Cineon grid to display values. Here it gets:
  - Canon's colour (base_rendering.json's matrix) and an exposure that puts mid grey where Canon's does;
  - a grey ramp between the Canon tone with soft highlights and the film's own (contrast: how much of
    the film's), keeping part of its tint (tint): the film's character without an overall cast;
  - any colour change the film's table can't carry through that: leaves kept from going mustard, chroma;
  - subtractive density: the more dye, the less light.
What a film gets is in KINDS, by its kind, and in FILMS, film by film, as each was looked at on real frames.
"""

import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
GRID = 33
CIN_BLACK = 0.0108                      # as fos_dev.c
MID = 0.09                              # the raw's mid grey, as Canon renders it (0.5 on the display)
SOFT_LIN = np.array([0.18, 0.36, 0.7, 1.0, 2.0])
SOFT_OUT = np.array([0.815, 0.92, 0.972, 0.995])

# contrast: of the film's grey ramp; punch: an S on top; tint: of its colour on grey; density;
# leaf: how much of a mustard turn is undone
KINDS = {
    "neg": dict(contrast=0.20, punch=0.015, tint=0.40, density=0.22, leaf=0.6, sat=1.0),
    "cine": dict(contrast=0.20, punch=0.015, tint=0.50, density=0.22, leaf=0.6, sat=1.0),
    "slide": dict(contrast=0.30, punch=0.05, tint=0.50, density=0.22, leaf=0.8, sat=1.0),
    "xpro": dict(contrast=0.50, punch=0.05, tint=1.00, density=0.10, leaf=0.0, sat=1.0),
    "print": dict(contrast=0.50, punch=0.0, tint=0.70, density=0.15, leaf=0.5, sat=1.0),
    "bw": dict(contrast=0.50, punch=0.025, tint=0.50, density=0.00, leaf=0.0, sat=1.0),
}
MAX_SLOPE = 2.5
# looked at one by one, on portraits, leaves, sky, a sunlit crowd and an interior
FILMS = {
    # the films' tables come out paler than these stocks are: sat brings each to its place among them
    "ektar100": dict(sat=0.95),
    "velvia50": dict(sat=0.92),
    "portra400vc": dict(sat=1.2),
    "portra400uc": dict(sat=1.3),
    "portra160vc": dict(sat=1.2),
    "velvia100": dict(sat=1.15),
    "provia100f": dict(sat=1.45),
    "provia400f": dict(sat=1.5),
    "provia400x": dict(sat=1.2),
    "astia100f": dict(sat=1.15),
    "sensia100": dict(sat=1.25),
    "kodachrome25": dict(sat=1.25),
    "kodachrome64": dict(sat=1.6),
    "kodachrome200": dict(sat=1.8),
    "e100gx": dict(sat=1.45),
    "e100vs": dict(sat=1.5),
    "elitechrome200": dict(sat=1.45),
    "elitechrome400": dict(sat=1.45),
    "extracolor100": dict(sat=1.25),
    "precisa100": dict(sat=1.35),
    # these are their cast
    "redscale100": dict(tint=1.0, contrast=0.6, leaf=0.0, density=0.1),
    "elite100xpro": dict(tint=0.8),
    "superia200xpro": dict(tint=0.55),
}

base = json.loads((HERE / "base_rendering.json").read_text())
MATRIX, XS, YS = np.array(base["matrix"]), np.array(base["xs"]), np.array(base["ys"])


def canon(v):
    return np.interp(np.log2(np.maximum(v, 1e-5)), np.r_[-16.6, XS, 3], np.r_[0, YS, 1])


def soft(v):
    v = np.asarray(v, dtype=np.float64)
    out = canon(v)
    high = v >= SOFT_LIN[0]
    out = np.where(high, np.interp(np.log2(np.maximum(v, 1e-5)), np.log2(SOFT_LIN), np.r_[canon(SOFT_LIN[:1]), SOFT_OUT]), out)
    return out


def to_lin(v):
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def to_disp(v):
    v = np.clip(v, 0, 1)
    return np.where(v <= 0.0031308, v * 12.92, 1.055 * v ** (1 / 2.4) - 0.055)


def oklab(rgb):
    x = to_lin(np.clip(rgb, 0, 1))
    l = np.cbrt(0.4122214708 * x[..., 0] + 0.5363325363 * x[..., 1] + 0.0514459929 * x[..., 2])
    m = np.cbrt(0.2119034982 * x[..., 0] + 0.6806995451 * x[..., 1] + 0.1073969566 * x[..., 2])
    s = np.cbrt(0.0883024619 * x[..., 0] + 0.2817188376 * x[..., 1] + 0.6299787005 * x[..., 2])
    return np.stack([0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
                     1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
                     0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s], -1)


def from_oklab(o):
    L, a, b = o[..., 0], o[..., 1], o[..., 2]
    l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3
    return to_disp(np.stack([4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
                             -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
                             -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s], -1))


def grid_lin():
    code = np.arange(GRID) / (GRID - 1)
    return (10 ** ((code * 1023 - 685) / 300) - CIN_BLACK) / (1 - CIN_BLACK)


def sample(table, lin):
    """the table (b, g, r, 3; 0..1) at linear light"""
    x = np.maximum(lin, 0) * (1 - CIN_BLACK) + CIN_BLACK
    pos = np.clip((685 + 300 * np.log10(x)) / 1023, 0, 1) * (GRID - 1)
    i0 = np.minimum(pos.astype(int), GRID - 2)
    f = pos - i0
    r, g, b = i0[..., 0], i0[..., 1], i0[..., 2]
    fr, fg, fb = f[..., :1], f[..., 1:2], f[..., 2:3]
    out = 0
    for db, wb in ((0, 1 - fb), (1, fb)):
        for dg, wg in ((0, 1 - fg), (1, fg)):
            for dr, wr in ((0, 1 - fr), (1, fr)):
                out = out + table[b + db, g + dg, r + dr] * wb * wg * wr
    return out


LUMA = np.array([0.2126, 0.7152, 0.0722])
LEAF = np.array([0.20, 0.34, 0.03])     # linear, as the camera has it, before Canon's matrix


def retune(table, kind, key=None):
    """table: uint8 (b, g, r, 3) -> the same, rebased"""
    p = {**KINDS[kind], **FILMS.get(key, {})}
    table = table.astype(np.float64) / 255
    mono = kind == "bw"
    matrix = np.eye(3) if mono else MATRIX
    xs = 2.0 ** np.linspace(-12, 4, 257)
    grey = lambda k: sample(table, np.stack([k * xs] * 3, -1))
    # the exposure that puts the film's mid grey at Canon's
    want = soft(np.array([MID]))[0]
    k = 2.0
    for _ in range(24):
        n = sample(table, np.full((1, 3), k * MID))[0] @ LUMA
        k = float(np.clip(k * 2 ** ((want - n) * 1.6), 0.25, 16))
    k *= p.get("exposure", 1)
    film = grey(k)
    film_y = film @ LUMA
    target_y = soft(xs) * (1 - p["contrast"]) + film_y * p["contrast"]
    target_y = target_y + p["punch"] * np.sin(2 * np.pi * (np.clip(target_y, 0, 1) - 0.5))
    target = target_y[:, None] + p["tint"] * (film - film_y[:, None])
    # the film's grey to that, channel by channel
    remap = []
    level = np.linspace(0, 1, 1025)
    for c in range(3):
        xp = np.maximum.accumulate(film[:, c]) + np.arange(len(xs)) * 1e-9
        y = np.interp(level, xp, np.maximum.accumulate(np.clip(target[:, c], 0, 1)))
        # where the film's table has nearly stopped rising, following it exactly would draw contours
        y = y[0] + np.r_[0, np.cumsum(np.minimum(np.diff(y), MAX_SLOPE * (level[1] - level[0])))]
        remap.append((level, y))
    def through(rgb):
        return np.stack([np.interp(rgb[..., c], *remap[c]) for c in range(3)], -1)

    lin = grid_lin()
    b, g, r = np.meshgrid(lin, lin, lin, indexing="ij")
    out = through(sample(table, k * (np.stack([r, g, b], -1) @ matrix.T)))
    if not mono:
        o = oklab(out)
        hue = np.arctan2(o[..., 2], o[..., 1])
        chroma = np.hypot(o[..., 1], o[..., 2])
        if p["leaf"]:
            # how far has a leaf turned from Canon's, in OkLCh? Turn the leaves back by part of it.
            leaf_canon = oklab(soft(LEAF @ matrix.T))
            leaf_film = oklab(through(sample(table, k * (LEAF @ matrix.T)[None]))[0])
            turn = np.arctan2(leaf_film[2], leaf_film[1]) - np.arctan2(leaf_canon[2], leaf_canon[1])
            turn = (turn + np.pi) % (2 * np.pi) - np.pi
            if turn < 0:
                centre = np.arctan2(leaf_film[2], leaf_film[1])
                d = (hue - centre + np.pi) % (2 * np.pi) - np.pi
                w = np.where(np.abs(d) < 0.9, 0.5 + 0.5 * np.cos(np.pi * d / 0.9), 0)
                # the warm side of the band is skin: its share of the turn fades out towards orange
                w = w * np.clip((hue - 1.45) / 0.35, 0, 1)
                hue = hue - turn * p["leaf"] * w
        # skin takes less of it: a slide's colour shouldn't turn faces red
        d = (hue - 0.85 + np.pi) % (2 * np.pi) - np.pi
        skin = np.where(np.abs(d) < 0.55, 0.5 + 0.5 * np.cos(np.pi * d / 0.55), 0)
        chroma = chroma * (1 + (p["sat"] - 1) * (1 - 0.7 * skin))
        L = o[..., 0] - p["density"] * chroma
        o = np.stack([L, chroma * np.cos(hue), chroma * np.sin(hue)], -1)
        out = from_oklab(o)
    return (np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8)
