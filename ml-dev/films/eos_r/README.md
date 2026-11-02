# The EOS R's Portra 400

The look of Portra 400 on the EOS R, chosen on the user's own frames against the alternatives. `install.py` puts
it on the card, in `ML/DARKROOM`: `LOOK00.LUT` from `portra400.lut`, and `GRAIN.BIN` from `grain.bin`.
The other films, and other cameras, are as they were.

With them `fos_dev.c` also takes the shot's white balance from the CR3 (daylight if the file doesn't give it),
a quarter of the lab scanner's exposure correction, and film grain in place of its single-pixel noise.

The table is made in two steps, from what is here:

1. `portra400.ts` writes the look as a cube over Canon's rendering. It is grmod-film's Portra 400 with less
   warmth, half its teal mixer, a gentler colour push, rosier skin and subtractive density.
2. `make_table.py` puts `base_rendering.json` and a softer highlight roll-off in front of it and writes the table.

`base_rendering.json` is a matrix (rows summing to 1, so grey stays grey) and a tone curve from the camera's
linear light to Canon's JPEG, fitted on four frames of the test body with the camera's own white balance
(mean error 0.012 to 0.019 of full scale). Those frames are private and not part of this repository, and the
Picture Style they were shot with isn't known.

`grain.bin` is grmod-film's fine film grain (`core/src/color/grain-tables.ts`, made by its `tools/grain/tiles.py`
from the Boolean film grain model): a 128 x 128 luma tile, 128 rows of 64 (Cb, Cr) pairs, and the strength by
brightness in 256 bytes, most in the shadows and lower midtones.

Every number is set by hand or fitted to those frames: this is an interpretation, not a measurement of the film.
