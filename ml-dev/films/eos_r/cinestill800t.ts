// SPDX-License-Identifier: GPL-2.0-only
// CineStill 800T on the EOS R, as a .cube over Canon's rendering, the same way as portra400.ts: grmod-film's recipe
// with half its cool balance and teal shadows, because the shot's white balance is kept; a gentler colour push and
// subtractive density. From a grmod-film checkout: npx vite-node --script <this file> cinestill800t.cube
import { writeFileSync } from 'node:fs';
import { LOOKS, cubeText, chain, curve, split, saturation, bands, balance } from '../../../../grmod-film/core/src/filmpack/looks';
import type { Op } from '../../../../grmod-film/core/src/filmpack/looks';
import { filmResponse, lab, display } from '../../../../grmod-film/examples/gr3x/film-pack-v3/response';

const base = LOOKS.find(l => l.name === 'Portra 400')!;

const look: Op = filmResponse(chain(
  balance(0.95, 0.99, 1.07),
  curve([[0, 0.01], [0.15, 0.132], [0.5, 0.5], [0.85, 0.87], [1, 0.99]]),
  split([-0.018, -0.022], [0.006, 0.0]),
  saturation(0.92),
  bands({ hue: 28, width: 30, sat: 1.18, shift: -4 }, { hue: 60, width: 25, sat: 0.97, shift: -3 }, { hue: 90, width: 30, sat: 0.9 }, { hue: 140, width: 40, sat: 0.85, shift: 14 }),
), 1.2, 0.048, 0.03);

const op: Op = c => { const y = lab(look(c)); return display(y[0] - 0.22 * Math.hypot(y[1], y[2]), y[1], y[2]); };
writeFileSync(process.argv[2], cubeText({ ...base, op }));
