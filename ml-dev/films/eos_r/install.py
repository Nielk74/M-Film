"""Put the EOS R's Portra 400 in the card's ML/DARKROOM: install.py DIR

The table's header marks it as this film's, the way fos_dev.c's look_hash works it out.
"""

import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "filmos/tools"))
from genfilms import FILMS, builtin_keys, look_bytes, look_hash  # noqa: E402

KEY = "portra400"
TUNED_MARK = 0x46                       # as fos_dev.c


def main():
    folder = Path(sys.argv[1])
    table = (HERE / "portra400.lut").read_bytes()
    assert len(table) == 33 ** 3 * 3, len(table)
    grain = (HERE / "grain.bin").read_bytes()
    assert len(grain) == 2 * 128 * 128 + 256, len(grain)
    mark = look_hash(KEY, look_bytes(FILMS[KEY])) ^ TUNED_MARK
    out = folder / f"LOOK{builtin_keys().index(KEY):02d}.LUT"
    out.write_bytes(b"FOL1" + struct.pack("<I", mark) + table)
    (folder / "GRAIN.BIN").write_bytes(b"FOG1" + grain)
    print(f"{out}, GRAIN.BIN: Portra 400")


if __name__ == "__main__":
    main()
