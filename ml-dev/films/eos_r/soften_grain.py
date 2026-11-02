#!/usr/bin/env python3
"""Makes grmod-film's grain tiles act more like real film in the dark.

usage: soften_grain.py <grmod-film's tiles> <grain.bin>

The clumps made holes in the noise of dark areas: frequencies below about 1 in 10 pixels go, softly,
and the tile is scaled back to the strength it had. The tiles wrap, so filtering them whole leaves
no seam. Scans of 800T go quiet in their blacks: the strength by brightness fades out below FADE_TO.
"""

import sys

import numpy

TILE = 128
SET = 2 * TILE * TILE + 256
CUT = 1 / 10            # cycles a pixel
FADE_FROM, FADE_TO = 6, 30  # brightness


def soften(tile, x_scale=1.0):
    fy = numpy.fft.fftfreq(tile.shape[0])[:, None]
    fx = numpy.fft.fftfreq(tile.shape[1])[None, :] * x_scale
    keep = 1 - numpy.exp(-(fx ** 2 + fy ** 2) / CUT ** 2)
    out = numpy.fft.ifft2(numpy.fft.fft2(tile) * keep).real
    return out * tile.std() / out.std()


def fade(tone):
    t = numpy.clip((numpy.arange(256.0) - FADE_FROM) / (FADE_TO - FADE_FROM), 0, 1)
    return numpy.uint8(numpy.round(tone * t * t * (3 - 2 * t))).tobytes()


def to_bytes(tile):
    return numpy.clip(numpy.round(tile), -127, 127).astype(numpy.int8).tobytes()


def main(source, target):
    tiles = open(source, "rb").read()
    assert len(tiles) == 2 * SET, len(tiles)
    out = bytearray(tiles)
    for at in (0, SET):
        luma = numpy.frombuffer(tiles, numpy.int8, TILE * TILE, at).reshape(TILE, TILE).astype(float)
        out[at:at + TILE * TILE] = to_bytes(soften(luma))
        # (Cb, Cr) pairs: each sample is two pixels wide
        colour = numpy.frombuffer(tiles, numpy.int8, TILE * TILE, at + TILE * TILE).reshape(TILE, TILE // 2, 2).astype(float)
        for channel in (0, 1):
            colour[:, :, channel] = soften(colour[:, :, channel], 0.5)
        out[at + TILE * TILE:at + 2 * TILE * TILE] = to_bytes(colour)
        tone = numpy.frombuffer(tiles, numpy.uint8, 256, at + 2 * TILE * TILE).astype(float)
        out[at + 2 * TILE * TILE:at + SET] = fade(tone)
    open(target, "wb").write(bytes(out))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
