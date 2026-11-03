#!/usr/bin/env python3
"""Makes the EOS R's grain tiles, chosen on the user's own frames against scans of real film.

usage: make_grain.py <grain.bin at commit 84aba9b> <grain.bin>

Filtered noise looks regular, so these are random grains of a few sizes, some places busier than
others and a few grains stronger. Only what's far bigger than a grain goes, or it would show as
blotches. The tiles wrap. From the input only the strength by brightness is kept, which now fades
out only right at black.
"""

import sys

import numpy
from scipy.ndimage import gaussian_filter

TILE = 128
SET = 2 * TILE * TILE + 256
SIZES = ((0.65, 0.5), (1.0, 0.35), (1.7, 0.15))     # a grain's radius against the tile's, and its share
RADIUS = (0.8, 1.05)                                # pixels: fine, coarse
STRENGTH = (1.3, 1.2)                               # against a standard deviation of 32
SPREAD = 0.45                                       # how much stronger some grains are
BUSY = 0.45                                         # how much busier some places are
DENSITY = 0.5
LARGEST = 0.018                                     # cycles a pixel: larger than this goes
FADE_FROM, FADE_TO = 2, 14                          # brightness


def grains(rng, shape, radius):
    busy = gaussian_filter(rng.normal(size=shape), 10, mode="wrap")
    busy = numpy.exp(busy / busy.std() * BUSY)
    tile = numpy.zeros(shape)
    for scale, share in SIZES:
        strength = rng.lognormal(0, SPREAD, shape) * numpy.where(rng.random(shape) < 0.56, 1.0, -1.0)
        points = strength * (rng.random(shape) < numpy.minimum(1, DENSITY * busy / busy.mean()))
        blurred = gaussian_filter(points, radius * scale, mode="wrap")
        tile += blurred / blurred.std() * share ** 0.5
    fy = numpy.fft.fftfreq(shape[0])[:, None]
    fx = numpy.fft.fftfreq(shape[1])[None, :]
    keep = 1 - numpy.exp(-(fx ** 2 + fy ** 2) / LARGEST ** 2)
    tile = numpy.fft.ifft2(numpy.fft.fft2(tile) * keep).real
    return tile / tile.std()


def to_bytes(tile):
    return numpy.clip(numpy.round(tile), -127, 127).astype(numpy.int8).tobytes()


def main(source, target):
    tiles = open(source, "rb").read()
    assert len(tiles) == 2 * SET, len(tiles)
    out = bytearray(tiles)
    rng = numpy.random.default_rng(800)
    for at, radius, strength in zip((0, SET), RADIUS, STRENGTH):
        out[at:at + TILE * TILE] = to_bytes(grains(rng, (TILE, TILE), radius) * 32 * strength)
        # (Cb, Cr) pairs
        colour = numpy.stack([grains(rng, (TILE, TILE // 2), radius), grains(rng, (TILE, TILE // 2), radius)], axis=2)
        out[at + TILE * TILE:at + 2 * TILE * TILE] = to_bytes(colour * 32)
        tone = numpy.frombuffer(tiles, numpy.uint8, 256, at + 2 * TILE * TILE).astype(float)
        t = numpy.clip((numpy.arange(256.0) - FADE_FROM) / (FADE_TO - FADE_FROM), 0, 1)
        out[at + 2 * TILE * TILE:at + SET] = numpy.uint8(numpy.round(tone * t * t * (3 - 2 * t))).tobytes()
    open(target, "wb").write(bytes(out))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
