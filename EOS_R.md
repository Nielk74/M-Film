# Experimental Canon EOS R port

This fork is developing M-Film for the **original EOS R**. It is not an EOS RP,
R5, or R6 build. It has run on **one** EOS R so far, in one short test; see
[Hardware results](#hardware-results-recorded-on-2026-11-02). It is still
experimental, and most of the checks under Hardware progression remain open.

## What this first port implements

- The existing 720 × 480 film shelf, Home, roll interface and input hooks.
- An RGBA palette adapter for Magic Lantern's DIGIC 8 compositor. It uses ML's
  indexed staging canvas, never the old LCD palette registers (NULL on EOS R).
- LiveView-only behavior, CR3 filenames in roll logs and explicit display refreshes.
- The newer upstream Picture Style setters for preview contrast, sharpness,
  saturation and colour tone. These are the film's existing preview recipe,
  **not its complete film LUT, grain or halation**.
- The in-camera darkroom, with a CR3 decoder for **RAW** frames (`fos_crx.c`). It
  feeds the same film pipeline as the CR2 cameras: film colour, grain and halation.
  The JPEG is 3360 x 2240, one pixel for each 2 x 2 of the mosaic (1493 x 2240 for a
  vertical frame), and the print on the screen stays 720 x 480. Short of memory
  it steps down to 2240 x 1494, 1620 x 1080, then 720 x 480, and logs the size used.
  C-RAW frames are refused with a message: they use a wavelet coding this decoder
  does not have yet. Shoot RAW.

ISO, Kelvin white balance and WB shifts remain manual. Upstream does not allow
those property writes on EOS R; this port does not expand its whitelist.
The HUD meter needle is omitted because the upstream port has no verified
`AE_VALUE` source. Canon's own meter remains available.
Picture Style changes and all camera behavior still need physical verification.
The upstream EOS R port flags programmatic image capture as unreliable. Use the
camera's shutter; this work does not enable remote shutter properties.

The first package disables automatic startup, boot animation, HUD and playback
locking. After a verified stock ML boot, a developer can request Home with the
TRASH button on the idle stills LiveView screen. Button events and EVF/LCD
switching still need validation. Existing settings on a reused card override
these defaults, so do not overlay it onto an old ML installation.

## Required firmware and source revision

Upstream targets **Canon EOS R firmware 1.8.0, internal version 7.3.9**.
There are at least three 1.8.0 builds: upstream documents 7.3.9 and 7.4.0, and
the test body arrived on 7.4.2, which Canon does not offer as a download. Only
7.3.9 is the target. Other installed firmware versions are not supported by this
code. The camera menu shows only 1.8.0, so read the internal version first.
See the [upstream EOS R notes](https://github.com/reticulatedpines/magiclantern_simplified/blob/142472e95054d73640f50789ac0fd66325499d82/platform/R.180/README.md).

The EOS R build uses Magic Lantern commit
`142472e95054d73640f50789ac0fd66325499d82`. This newer core includes the EOS R
Picture Style property support missing from M-Film's original core. Other
camera builds keep their existing core and entry point, `build_fork.sh`.

No Canon firmware, ROM dump, installer FIR or firmware downgrade is supplied or
automatically installed. Stock ML must first be demonstrated working on the
exact target body/firmware. Its boot image memory limit is still unknown here.
Do not treat the generated developer files as a tested installation package.

## Build on Linux or Ubuntu in WSL

Use a dedicated source checkout; the patcher changes it in place. Requirements:
GCC/make, ARM bare-metal GCC and binutils, newlib headers, git, Python 3,
NumPy, SciPy and Pillow. On Ubuntu 24.04:

```sh
sudo apt-get update
sudo apt-get install --no-install-recommends build-essential gcc-arm-none-eabi binutils-arm-none-eabi libnewlib-arm-none-eabi libnewlib-dev git python3-numpy python3-scipy python3-pil
git clone --filter=blob:none https://github.com/reticulatedpines/magiclantern_simplified.git "$HOME/ml/magiclantern-r"
git -C "$HOME/ml/magiclantern-r" checkout 142472e95054d73640f50789ac0fd66325499d82
bash ml-dev/build_eos_r.sh
```

`ML_SRC` can point to another dedicated checkout at that exact commit. The script
runs the complete host test suite, patches the core, builds `autoexec.bin` with
fatal compiler warnings, and writes `ml-dev/out/eos-r-experimental/`. It produces
no installer or release. `BUILD-INFO.txt` records source revision, compiler,
image sizes and checksum. Build errors stop the script rather than reusing a ZIP.

To run only the host tests:

```sh
ML_TEST_SRC="$HOME/ml/magiclantern-r" python3 -m unittest discover -s ml-dev/tests -v
```

The tests exercise palette transparency, high colour indices, photograph fades,
restoring ML's palette, patcher guards/idempotence, every existing scripted UI
scene and synthetic CR2 decoding with native and ARM-style unaligned reads.
They do not execute the camera's firmware, compositor or hardware.

`test_cr3.py` needs `libraw-dev` and two real EOS R files from
[raw.pixls.us](https://raw.pixls.us/data/Canon/EOS%20R/) (fetched once, hash
checked, or from `FOS_CR3_DIR`). Every sensor sample of the RAW frame must match
LibRaw's; truncated and corrupted files must be refused without overrunning a
buffer under AddressSanitizer; other cameras' builds must not contain the decoder.

## Verification recorded on 2026-11-01

- Stock EOS R core cross-compiles with ARM GCC 13.2.1.
- Experimental EOS R `autoexec.bin` links with fatal warnings enabled for core
  and Film OS sources. Upstream TinyCC emits its pre-existing FIXME warning.
- All eight host tests pass; the scene regression covers 19 scripted scenes.
- The palette-close regression reproduces the unsafe double read and passes
  after the fix; ARM disassembly confirms the converter loads the pointer once.
- Both synthetic CR2 channel layouts decode without sample mismatches; native
  and ARM-style unaligned builds produce identical print files.
- The original full-darkroom EOS M build cross-compiles against its original
  core. Its text/data/BSS total is 719,924 bytes, below the recorded 727,072-byte
  boot limit. This remains a build regression check, not another hardware test.
- No camera has been connected, flashed, booted or used for capture validation
  in this work. EOS R hardware behavior and CR3 rendering remain unverified.

## Hardware results recorded on 2026-11-02

One EOS R, one session. This is evidence that the path works, not a guarantee.

- **Reading the firmware.** A Canon Basic script calling `CamInfo_Debug(1)` wrote
  `CAM_INFO.XML` to the card and reported internal 7.4.2. The card was a 32 GB
  FAT32 SDHC with `SCRIPT` at boot sector offset 0x1F0, `extend.m` and a
  `script.req` containing `for DC_scriptdisk` in its root. The script runs from
  Playback by pressing SET; nothing shows on screen. Without `script.req` it did
  not run.
- **Going from 7.4.2 to 7.3.9.** The camera accepted Canon's official 1.8.0
  package `gds/8/0400006288/01/eosr-v180-win.zip` (`EOSR0180.FIR` dated
  2020-10-21) from the Firmware menu, and `CAM_INFO.XML` then reported 7.3.9.
  This is one report from one body. Upstream documents only 7.4.0 to 7.3.9, and a
  build that was never a public download may mean changed hardware, so the risk
  for another body is unknown.
- **Boot flag.** A Canon Basic script calling `EnableBootDisk()` set it (from the
  [Canon Basic thread](https://www.magiclantern.fm/forum/index.php?topic=25305.0),
  which also gives `DisableBootDisk()`). The card light did not visibly change
  when the script ran.
- **Stock ML at the pinned revision.** It booted. The first start wrote
  `ROM0.BIN` and `ROM1.BIN` to `ML/LOGS` (a long red card light), and `magic.cfg`
  named the build. The tester reported that the ML menu, a photo, playback and
  shutdown all worked. ROM dumps must not be published.
- **M-Film at 4d3d0e8.** The 309,632-byte `autoexec.bin` booted, compared with
  239,296 bytes for stock, so that size is known to start. The tester reported
  that the test on the camera worked, covering Home on TRASH, the interface and a
  photo. The result has not been checked against `ML/FILMROLL.LOG` or the roll
  log, and no individual item below has been confirmed from them.

Still unverified: palette colours against the simulator, the closing iris and the
`FOS_RGBA_CLEAR` fix, the repeated ISO/WB prompt, Picture Style acknowledgement,
`.CR3` names in the roll log, EVF/LCD switching, dialogs, the HUD and any longer
shooting session. The boot memory limit is still unknown.

## Hardware progression

1. Identify the installed public and internal firmware revisions; save camera
   settings and photos. Verify that the stock ML core boots on that exact body.
2. Establish the stock boot memory footprint and logging. A build larger than
   the available reservation may hang; successful linking is not a boot test.
3. Test a manual UI open/close, TRASH, SET, directions and both dials. Check that
   half-shutter exits immediately and Canon menus/playback remain usable.
4. Check colours, display-off, LCD/EVF changes and card/low-battery dialogs.
5. Load one virtual roll, verify preview Picture Style changes, capture with
   Canon's shutter, and compare CR3 files to the roll log across a restart.
6. Only after those checks, try the HUD and longer shooting sessions.

CR3 development ran on the test body at 720 x 480 (16 s a frame) and 1620 x 1080
(21 s), its log clean, and every sample of a real frame from that body matched
LibRaw. Its log also showed three faults the host tests could not: a wrong file
prefix, a false "set RAW" notice and an unknown aperture, all now handled for this
camera. That body refused single blocks of 90 MB and 40 MB (and 1620 x 1080 then took
42 s, never explained), so a bigger frame now uses 8 MB pieces. In pieces, one frame
developed there at 3360 x 2240: 80 s in all (34 s decoding, 24 film, 15 JPEG, 2 print)
and a 1.7 MB JPEG, with all 11 pieces available. A landscape frame at that size now
takes 6 pieces, and its planes are decoded row by row. A later frame of nearly the same
file size, on another film, took 69 s there (28 s decoding, 7.4 of it reading the card,
22 film, 12 JPEG, 2 print) and a 1.2 MB JPEG; part of the JPEG's gain is that frame's
smaller file. Those two runs are not the same frame, so the 11 s is approximate. The
outputs are not approximate: the code before and after this change, on x86 and emulated
ARM, given the card's film tables and the camera's date and model name, each reproduce
both frames' JPEG and print from the camera byte for byte. `test_cr3.py` holds every
output to its exact bytes through speed work. A whole roll, memory over many frames and
a vertical frame are untested there.

Portra 400 on this body is its own (`ml-dev/films/eos_r/`): the shot's white balance,
read from the CR3's metadata track, a quarter of the lab scanner's correction, a
table over Canon's rendering, and film grain from a tile, so it isn't as warm or as
flat as the other cameras'. On six frames from the test body, without grain, this
code's JPEGs are byte for byte the version chosen by eye, and the white balance matches
LibRaw on nine. It has not yet developed on the camera itself, and the other films
still develop at a fixed daylight balance. A vertical frame comes out at 1493 x 2240.

The JPEG is written in one go, so the screen
pauses meanwhile. `autoexec.bin` of 362,784 bytes starts; the boot limit is unknown.
`CRASH00.LOG` recorded one Canon `ImageController.c:981` assert in that testing, in
LiveView after M-Film had closed and before any developing. Its cause is unknown.
`WHITE_LEVEL` comes from LibRaw at ISO 100 (the test body's file gave 14448, not 14008). C-RAW is later work. Do not mark EOS R as supported in the main
camera table until hardware evidence justifies it.
