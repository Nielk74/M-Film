// SPDX-License-Identifier: GPL-2.0-only
// Portra 400 on the EOS R, as a .cube over Canon's rendering: grmod-film's recipe with less warmth, half its
// teal mixer and a gentler colour push, because Canon's rendering is already saturated; rosier skin; and
// subtractive density: the more dye, the less light, so saturated colours are darker.
// From a grmod-film checkout: npx vite-node --script <this file> portra400.cube
// (the imports below need that repository next to this one)
import { writeFileSync } from 'node:fs';
import { LOOKS, cubeText, chain, curve, split, saturation, attract, bands, mixer } from '../../../../grmod-film/core/src/filmpack/looks';
import type { Op } from '../../../../grmod-film/core/src/filmpack/looks';
import { filmResponse, lab, display } from '../../../../grmod-film/examples/gr3x/film-pack-v3/response';

const base = LOOKS.find(l => l.name === 'Portra 400')!;
const TEAL = [[1.120, 0.063, -0.183], [0.081, 0.829, 0.090], [0.028, -0.063, 1.036]];
const half = TEAL.map((row, r) => row.map((v, c) => (r === c ? 1 : 0) + 0.5 * (v - (r === c ? 1 : 0))));

const look: Op = filmResponse(chain(
  mixer(half),
  curve([[0, 0.015], [0.2, 0.206], [0.5, 0.51], [0.8, 0.81], [0.95, 0.935], [1, 0.97]]),
  split([-0.003, 0], [0.002, 0.008]),                   // grmod-film: [0.006, 0.02], a creamier white
  saturation(0.92),
  attract(44, 28, 0.25),                                // grmod-film: 52
  bands(
    { hue: 50, width: 34, sat: 1.08, shift: -5, light: 1.02 },
    { hue: 140, width: 40, sat: 0.9, shift: 0 },        // grmod-film: 0.75, -14, which goes mustard here
    { hue: 245, width: 50, sat: 0.95, light: 1.0 },
  ),
), 1.2, 0.048, 0.03);

const DENSITY = 0.22;
const op: Op = c => { const y = lab(look(c)); return display(y[0] - DENSITY * Math.hypot(y[1], y[2]), y[1], y[2]); };

writeFileSync(process.argv[2], cubeText({ ...base, op }));
