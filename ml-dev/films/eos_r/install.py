"""Put the EOS R's films in the card's ML/DARKROOM: install.py DIR

The films with a table of their own here (FROM_RECIPE) take that. Every other film's table in DIR,
made from its look or its LUT by the build, is rebased (retune.py). The tables' headers mark them as
this body's, the way fos_dev.c's look_hash works it out; one marked already is left alone.
"""

import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "filmos/tools"))
from genfilms import FILMS, builtin_keys, look_bytes, look_hash  # noqa: E402

TUNED_MARK = 0x46                       # as fos_dev.c
FROM_RECIPE = ("portra400", "gold200", "cinestill800t")
TABLE_BYTES = 33 ** 3 * 3


def main():
    folder = Path(sys.argv[1])
    built = builtin_keys()
    order = built + [k for k in FILMS if k not in built]
    made = rebased = 0
    for index, key in enumerate(order):
        out = folder / f"LOOK{index:02d}.LUT"
        plain = look_hash(key, look_bytes(FILMS[key]))
        mark = struct.pack("<I", plain ^ TUNED_MARK)
        if key in FROM_RECIPE:
            table = (HERE / f"{key}.lut").read_bytes()
            assert len(table) == TABLE_BYTES, key
            out.write_bytes(b"FOL1" + mark + table)
            made += 1
        elif out.exists():
            data = out.read_bytes()
            assert len(data) == 8 + TABLE_BYTES and data[:4] == b"FOL1", key
            if data[4:8] == mark:
                continue
            assert data[4:8] == struct.pack("<I", plain), f"{key}: its table is from another look"
            import numpy as np
            from retune import retune
            table = np.frombuffer(data, np.uint8, offset=8).reshape(33, 33, 33, 3)
            out.write_bytes(b"FOL1" + mark + retune(table, FILMS[key].get("kind", "neg"), key).tobytes())
            rebased += 1
    grain = (HERE / "grain.bin").read_bytes()
    assert len(grain) == 2 * (2 * 128 * 128 + 256), len(grain)
    (folder / "GRAIN.BIN").write_bytes(b"FOG1" + grain)
    print(f"{folder}: {made} tables from recipes, {rebased} rebased, and GRAIN.BIN")


if __name__ == "__main__":
    main()
