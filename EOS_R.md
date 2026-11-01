# Experimental Canon EOS R port

This fork is developing M-Film for the **original EOS R**. It is not an EOS RP,
R5, or R6 build. **No physical EOS R test has been performed.** A successful
cross-compilation does not establish that it boots or that shooting works.

## What this first port implements

- The existing 720 × 480 film shelf, Home, roll interface and input hooks.
- An RGBA palette adapter for Magic Lantern's DIGIC 8 compositor. It uses ML's
  indexed staging canvas, never the old LCD palette registers (NULL on EOS R).
- LiveView-only behavior, CR3 filenames in roll logs and explicit display refreshes.
- The newer upstream Picture Style setters for preview contrast, sharpness,
  saturation and colour tone. These are the film's existing preview recipe,
  **not its complete film LUT, grain or halation**.
- A CR3-aware unavailable-darkroom screen. The EOS M's CR2 decoder is not used
  for EOS R photos. Copy CR3 files to a computer for development.

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
There are two different 1.8.0 firmware images. Internal 7.4.0 is not the same
target. Other installed firmware versions are not supported by this code.
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

CR3 decoding and full film development are subsequent work. A real EOS R CR3
fixture and on-camera memory measurements are needed before designing that
part. Do not mark EOS R as supported in the main camera table until hardware
evidence justifies it.
