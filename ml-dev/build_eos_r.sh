#!/usr/bin/env bash
# Experimental EOS R developer build. Does not create/install an update FIR.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="${ML_SRC:-$HOME/ml/magiclantern-r}"
OUT="${MFILM_OUT:-$HERE/out/eos-r-experimental}"
ML_REVISION=142472e95054d73640f50789ac0fd66325499d82

if [ ! -d "$SRC/.git" ]; then
    echo "A dedicated Magic Lantern checkout is required at $SRC (see EOS_R.md)." >&2
    exit 1
fi
ACTUAL=$(git -C "$SRC" rev-parse HEAD)
if [ "$ACTUAL" != "$ML_REVISION" ]; then
    echo "EOS R requires pinned ML revision $ML_REVISION; found $ACTUAL." >&2
    exit 1
fi

# Exercise both the EOS R additions and the existing Film OS/CR2 paths.
ML_TEST_SRC="$SRC" python3 -m unittest discover -s "$HERE/tests" -v

cp "$HERE"/fork/src/filmroll.{c,h} "$HERE"/filmos/fos*.{c,h} "$SRC/src/"
sed -i 's/\r$//' "$SRC"/src/filmroll.{c,h} "$SRC"/src/fos*.{c,h}
VERSION=$(tr -d ' \r\n' < "$HERE/../VERSION")
printf '#define MFILM_VERSION "%s-eos-r-experimental"\n' "$VERSION" > "$SRC/src/fos_version.h"
python3 "$HERE/fork/apply_fork.py" "$SRC" R.180 --experimental-eos-r

mkdir -p "$HERE/out"
LOG="$HERE/out/eos-r-build.log"
make -C "$SRC/platform/R.180" clean > "$LOG" 2>&1
if ! make -C "$SRC/platform/R.180" -j"${JOBS:-8}" FATAL_WARNINGS=y build/autoexec.bin >> "$LOG" 2>&1; then
    tail -60 "$LOG"
    exit 1
fi

mkdir -p "$OUT/ML/data" "$OUT/ML/fonts" "$OUT/ML/SETTINGS"
cp "$SRC/platform/R.180/build/autoexec.bin" "$OUT/autoexec.bin"
cp "$SRC"/data/fonts/*.rbf "$OUT/ML/fonts/"
gcc -O2 -I"$HERE/filmos" -o "$HERE/out/eos-r-fontblob" "$HERE/filmos/tools/fontblob.c"
"$HERE/out/eos-r-fontblob" "$OUT/ML/data/FOSFONT.BIN"
F="$HERE/filmos"
python3 "$F/tools/genfilms.py" "$OUT/ML/data/FOSFILMS.BIN"
# every film's colour table, mixed from its look then baked from its LUT, as
# in build_fork.sh: the darkroom would otherwise mix them itself, without the LUTs
[ -d "$HERE/films/luts/filmluts" ] || python3 "$HERE/films/getluts.py"
gcc -O2 -std=gnu99 -DFOS_DEV_TEST -I"$F" -o "$HERE/out/eos-r-devtest" "$F"/sim/devtest.c "$F"/sim/port_files.c \
    "$F"/sim/films.c "$F"/fos_dev.c "$F"/fos_print.c "$F"/fos_looks.c "$F"/fos_film.c "$F"/fos_gfx.c "$F"/fos_jpeg.c -lm
mkdir -p "$OUT/ML/DARKROOM"
FOS_FILMS="$OUT/ML/data/FOSFILMS.BIN" "$HERE/out/eos-r-devtest" -T "$OUT/ML/DARKROOM"
python3 "$F/tools/genfilms.py" "$OUT/ML/data/FOSFILMS.BIN" --tables "$OUT/ML/DARKROOM"
python3 "$HERE/films/eos_r/install.py" "$OUT/ML/DARKROOM"
# Never auto-open experimental UI at boot. A developer invokes Home with TRASH.
printf 'filmroll.startup = 0\nfilmroll.bootanim = 0\nfilmroll.hud = 0\nfilmroll.playlock = 0\n' > "$OUT/ML/SETTINGS/magic.cfg"
cp "$HERE/../EOS_R.md" "$OUT/EOS_R.md"
cp "$HERE/../LICENSE" "$OUT/LICENSE.txt"
cp "$HERE/../THIRD_PARTY.md" "$OUT/THIRD_PARTY.txt"
{
    echo 'EXPERIMENTAL EOS R developer build; NOT hardware validated.'
    echo 'Target: EOS R 1.8.0, internal 7.3.9. Boot memory limit unknown.'
    echo "Magic Lantern: $ML_REVISION"
    echo "M-Film source: $(git -C "$HERE/.." rev-parse HEAD) (includes local edits if present)"
    arm-none-eabi-gcc --version | head -1
    arm-none-eabi-size "$SRC/platform/R.180/build/magiclantern"
    sha256sum "$OUT/autoexec.bin"
} > "$OUT/BUILD-INFO.txt"
cat "$OUT/BUILD-INFO.txt"
echo "Developer files: $OUT; build log: $LOG"
