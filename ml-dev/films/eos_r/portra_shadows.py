#!/usr/bin/env python3
"""Takes half the warmth out of Portra 400's shadows, as the user chose against scans of the film.

usage: portra_shadows.py <portra400.lut at commit 1d6906e> <portra400.lut>

A curve on each of the table's outputs; from a brightness of about 150 up it hardly moves them.
"""

import sys

import numpy

STRENGTH = 0.5
CURVES = (
    ([0, 20, 40, 80, 130, 205, 255], [0, 20, 38, 78, 129, 200, 255]),
    ([0, 19, 34, 70, 105, 190, 255], [0, 20, 37, 72, 106, 192, 255]),
    ([0, 12, 18, 25, 48, 67, 100, 145, 255], [2, 16, 21, 33, 57, 73, 103, 146, 255]),
)


def main(source, target):
    table = numpy.fromfile(source, numpy.uint8).astype(float).reshape(-1, 3)
    for channel, (x, y) in enumerate(CURVES):
        table[:, channel] += STRENGTH * (numpy.interp(table[:, channel], x, y) - table[:, channel])
    numpy.uint8(numpy.clip(numpy.round(table), 0, 255)).tofile(target)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
