# The EOS R's films

All 72 films develop differently on the EOS R than on other cameras. `install.py` puts them on the card, in
`ML/DARKROOM`: a `LOOKnn.LUT` for each film and `GRAIN.BIN`. The other cameras' films are as they were.

With these `fos_dev.c` takes the shot's white balance from the CR3 (daylight if the file doesn't give it), a
quarter of the lab scanner's exposure correction (slides have no scanner), the film's table, and film grain in
place of its single-pixel noise. Grain strength is the film's own; films of 1.3 and up take the coarse tile.

## The tables

Portra 400, Gold 200 and CineStill 800T have recipes, chosen on the user's own frames, and their tables are here:

1. `portra400.ts`, `gold200.ts` and `cinestill800t.ts` write the look as a cube over Canon's rendering, from
   grmod-film's recipes: less warmth, half the teal mixer, a gentler colour push, subtractive density.
2. `make_table.py` puts `base_rendering.json` and a softer highlight roll-off in front of it and writes the table.

The other 69 start from the table the build makes from their look or their LUT. `retune.py` rebases that: Canon's
colour, Canon's mid grey, a grey ramp between Canon's tone with soft highlights and the film's own, part of the
film's tint on grey, leaves kept from going mustard, and subtractive density. How much of each goes by the film's
kind and film by film (`KINDS` and `FILMS` there), as each was looked at on real frames: portraits, leaves, a pale
sky, a sunlit crowd and a tungsten interior. Cross-processed films and redscale keep their cast: it's what they are.

`base_rendering.json` is a matrix (rows summing to 1, so grey stays grey) and a tone curve from the camera's
linear light to Canon's JPEG, fitted on four frames of the test body with the camera's own white balance
(mean error 0.012 to 0.019 of full scale). Those frames are private and not part of this repository, and the
Picture Style they were shot with isn't known.

## The grain

`grain.bin` is grmod-film's film grain (`core/src/color/grain-tables.ts`, made by its `tools/grain/tiles.py`
from the Boolean film grain model): for fine, then coarse, a 128 x 128 luma tile, 128 rows of 64 (Cb, Cr) pairs,
and the strength by brightness in 256 bytes, most in the shadows and lower midtones.

Every number is set by hand or fitted to those frames: these are interpretations, not measurements of the films.
