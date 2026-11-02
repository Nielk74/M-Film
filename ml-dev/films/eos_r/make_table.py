"""Make portra400.lut: make_table.py portra400.cube portra400.lut

The camera's table runs from linear light, on a 33-point Cineon grid, to display values.
Here that is: base_rendering.json's matrix and tone (Canon's rendering), its highlights
rolled off more softly, then the cube that portra400.ts writes.
"""

import json
from pathlib import Path
import sys

import numpy as np

HERE = Path(__file__).resolve().parent
GRID = 33
CIN_BLACK = 0.0108                      # as fos_dev.c
# above mid grey: linear light -> display, more steps between light and white than Canon's
SOFT_LIN = np.array([0.18, 0.36, 0.7, 1.0, 2.0])
SOFT_OUT = np.array([0.815, 0.92, 0.972, 0.995])


def read_cube(path):
    rows, n = [], 0
    for line in open(path):
        p = line.split()
        if not p or p[0].startswith("#") or p[0] == "TITLE":
            continue
        if p[0] == "LUT_3D_SIZE":
            n = int(p[1])
        elif len(p) == 3 and p[0][0] in "0123456789.-":
            rows.append([float(x) for x in p])
    return np.array(rows, np.float32).reshape(n, n, n, 3), n    # [b, g, r]


def apply_cube(rgb, cube, n):
    x = np.clip(rgb, 0, 1) * (n - 1)
    i0 = np.minimum(x.astype(int), n - 2)
    f = x - i0
    r, g, b = i0[..., 0], i0[..., 1], i0[..., 2]
    fr, fg, fb = f[..., :1], f[..., 1:2], f[..., 2:3]
    out = 0
    for db, wb in ((0, 1 - fb), (1, fb)):
        for dg, wg in ((0, 1 - fg), (1, fg)):
            for dr, wr in ((0, 1 - fr), (1, fr)):
                out = out + cube[b + db, g + dg, r + dr] * wb * wg * wr
    return out


def main():
    base = json.loads((HERE / "base_rendering.json").read_text())
    matrix, xs, ys = np.array(base["matrix"]), np.array(base["xs"]), np.array(base["ys"])

    def canon(v):
        return np.interp(np.log2(np.maximum(v, 1e-5)), np.r_[-16.6, xs, 3], np.r_[0, ys, 1])

    def tone(v):
        out = canon(v)
        high = v >= SOFT_LIN[0]
        out[high] = np.interp(np.log2(v[high]), np.log2(SOFT_LIN), np.r_[canon(SOFT_LIN[:1]), SOFT_OUT])
        return out

    code = np.arange(GRID) / (GRID - 1)
    lin = (10 ** ((code * 1023 - 685) / 300) - CIN_BLACK) / (1 - CIN_BLACK)
    b, g, r = np.meshgrid(lin, lin, lin, indexing="ij")         # red changes fastest, as the camera's grid
    rgb = tone(np.stack([r, g, b], -1) @ matrix.T).astype(np.float32)
    out = apply_cube(rgb, *read_cube(sys.argv[1]))
    Path(sys.argv[2]).write_bytes((np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8).tobytes())


if __name__ == "__main__":
    main()
