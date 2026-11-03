# decode-bench — codec decode harness for the U2 BT stack

Sandbox verification harness behind the stage-2 decode power work
(see `docs/DECODE_POWER_OPTIMIZATION.md`).  It links the **same codec
objects** the firmware builds (OI SBC decoder, Fraunhofer FDK AAC,
vendored libldacdec / libldacdec_float, Sony libldacBT_enc for LDAC
stream generation) into one static ARM executable and runs it under
`qemu-arm-static` on an x86 host.

## Files

| file | purpose |
|------|---------|
| `codec_harness.cpp` | the harness: `gen`, `enc-sbc/aac/ldac`, `dec-sbc/aac/ldac`, `psnr` (auto-lag scan ±8192, priming-skip for long streams), `bench` (ns/frame) |
| `build_harness.sh <tag> <objdir> [ldacdec_srcdir] [extra_cflags]` | builds `harness_<tag>` from an existing `bt_standalone_build.sh` workdir's objects; recompiles the stubs every run; optional LDAC source-dir override compiles a decoder variant (e.g. `third_party/libldacdec_float`) |
| `opt_build.sh` | Phase-A driver: re-derives the whole object tree at `-O2 -ffp-contract=off` into `${BTW_BUILD_OPT:-/tmp/btw-build-o2}` and links the artifacts |

## Prerequisites

* A completed `scripts/bt_standalone_build.sh` workdir (provides `env.sh`
  with the NDK toolchain and `obj/` with the stack objects).
  Override its location with `BTW_BUILD=/path` (default `/tmp/btw-build`).
* `qemu-arm-static` in `PATH` for running the ARM binary on x86.
* Sony LDAC **encoder** sources at `/tmp/libldac81` (unity build, cached
  objects under `harness/obj_ldacenc`) for `enc-ldac`; not needed for
  decode-only work.
* `ffmpeg` (>= 5.x) for reference AAC encoding of noise-heavy streams
  (the harness FDK encoder is tonal-accurate but weak on full-scale
  noise — see the optimization doc §4 notes).

## Quick start

```sh
# 1) build the stack workdir once:
scripts/bt_standalone_build.sh --tag android-7.0.0_r36 --workdir /tmp/btw-build

# 2) baseline harness (-O0 objects, original double libldacdec):
tools/decode-bench/build_harness.sh o0 /tmp/btw-build/obj

# 3) float+NEON LDAC variant at -O2:
tools/decode-bench/build_harness.sh lb /tmp/btw-build/obj \
    third_party/libldacdec_float "-O2 -ffp-contract=off"

# 4) run:
H=/tmp/btw-build/harness
qemu-arm-static $H/o0/harness_o0 gen   /tmp/bench/pcm/test.pcm torture
qemu-arm-static $H/o0/harness_o0 enc-ldac /tmp/bench/pcm/test.pcm /tmp/bench/streams/test.ldac
qemu-arm-static $H/lb/harness_lb  dec-ldac /tmp/bench/streams/test.ldac /tmp/bench/pcm/lb_test.pcm
qemu-arm-static $H/o0/harness_o0  dec-ldac /tmp/bench/streams/test.ldac /tmp/bench/pcm/o0_test.pcm
qemu-arm-static $H/lb/harness_lb  psnr /tmp/bench/pcm/o0_test.pcm /tmp/bench/pcm/lb_test.pcm
qemu-arm-static $H/lb/harness_lb  bench dec-ldac /tmp/bench/streams/test.ldac 2069
```

Environment knobs: `SBC_CHMODE` (JS/STEREO/...), `SBC_STREAMS`,
`LDAC_MTU` (default 1003 — LDAC EQMID_SQ needs ≥1002).

## Acceptance gates used for patch 0011

* **-O2 vs -O0**: decode output **bit-exact** (SBC/AAC/LDAC torture).
* **float+NEON vs -O0 double**: PSNR ≥ 90 dB → measured **112.50 dB**
  (torture) / **115.77 dB** (sine), max diff 1 LSB.
* Both firmware variants (`--opt` on/off) compile + link GREEN.
