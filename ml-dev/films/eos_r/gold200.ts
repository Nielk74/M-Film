// SPDX-License-Identifier: GPL-2.0-only
// Gold 200 on the EOS R, as a .cube over Canon's rendering, the same way as portra400.ts: grmod-film's recipe
// with half its teal mixer, a less yellow white, truer greens, a gentler colour push and subtractive density.
// From a grmod-film checkout: npx vite-node --script <this file> gold200.cube
import { writeFileSync } from 'node:fs';
import { LOOKS, cubeText, chain, curve, split, saturation, bands, mixer } from '../../../../grmod-film/core/src/filmpack/looks';
import type { Op } from '../../../../grmod-film/core/src/filmpack/looks';
import { filmResponse, lab, display } from '../../../../grmod-film/examples/gr3x/film-pack-v3/response';

const base = LOOKS.find(l => l.name === 'Portra 400')!;
const TEAL = [[1.125, 0.030, -0.154], [0.087, 0.916, -0.003], [0.067, -0.099, 1.033]];
const half = TEAL.map((row, r) => row.map((v, c) => (r === c ? 1 : 0) + 0.5 * (v - (r === c ? 1 : 0))));

const look: Op = filmResponse(chain(
  mixer(half),
  curve([[0, 0], [0.12, 0.1], [0.5, 0.51], [0.85, 0.86], [1, 0.985]]),
  split([0.002, 0.008], [0.004, 0.018]),
  saturation(1.0),
  bands({ hue: 85, width: 35, sat: 1.15, shift: -4 }, { hue: 30, width: 25, sat: 1.05, shift: 4 }, { hue: 140, width: 40, sat: 0.95, shift: 0 }, { hue: 250, width: 50, sat: 0.9 }),
), 1.2, 0.048, 0.03);

const op: Op = c => { const y = lab(look(c)); return display(y[0] - 0.22 * Math.hypot(y[1], y[2]), y[1], y[2]); };
writeFileSync(process.argv[2], cubeText({ ...base, op }));
