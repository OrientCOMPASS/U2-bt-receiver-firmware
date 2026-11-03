# Decode power optimization (patch 0011 + `--opt` build variant)

Stage-2 work: minimize the CPU power cost of A2DP **sink** audio decoding
(SBC / AAC / LDAC) on the U2's ARMv7 SoC, verified in the sandbox before
any device exposure.  Per the maintainer's decision, the on-device
power A/B measurement was **removed from scope**; the device channel is
reserved for functional verification only.

## 1. How the stack decodes today (answer to "is it Java?")

No Java is involved in the audio path.  The chain is entirely native C/C++:

```
BTU thread                       media worker thread
AVDTP media packet ──────────►   20 ms periodic alarm (decode pump)
 btif_media_sink_enque_buf()      btif_media_task_avk_handle_timer()
  osi_malloc per RTP packet        SBC : OI_CODEC_SBC_DecodeFrame (embdrv)
  enqueue → RxSbcQ                 AAC : Fraunhofer FDK aacDecoder_Fill/
  delayed start at 5 pkts                DecodeFrame (external/aac)
                                   LDAC: ldacDecode (third_party/libldacdec,
                                         vendored MIT, scalar **double**)
                                 PCM → btif_sink_write_audio()
                                     → BtifAvrcpAudioTrackWriteData
                                     → AudioTrack (TRANSFER_SYNC)
```

`packages/apps/Bluetooth` only handles the control plane (pairing,
connection, AVRCP); media flows BTU → media task → AudioFlinger.

## 2. Findings

| # | finding | fix | phase |
|---|---------|-----|-------|
| F1 | The released firmware builds the whole stack with **-O0** (no optimization at all) | `--opt`: every TU at `-O2 -ffp-contract=off` | A |
| F2 | `third_party/libldacdec` is scalar **double**-precision: the IMDCT/DCT-IV runs on the soft-float-ish D path and the NEON FPU is idle | `third_party/libldacdec_float`: float32 pipeline + NEON-vectorized DCT-IV butterflies | B |
| F3 | Glue churn: per-RTP-packet `osi_malloc`/`osi_free`, 25 wakeups/s/stream from the 20 ms decode alarm, minimal AudioTrack buffer | RX buffer pool, 40 ms tick, 80 ms track buffer | C |

`-ffp-contract=off` pins IEEE-754 rounding (no FMA contraction), which is
what makes the optimized builds numerically comparable to the -O0
reference in the harness gates below.

## 3. Phase A — `-O2` whole-stack (bit-exact)

All 443 translation units compile at `-O2 -ffp-contract=off` with
NDK r25c clang-14 (`armv7a-linux-androideabi26`); all three artifacts
link (`bluetooth.default.so`, `audio.a2dp.default.so`,
`libFraunhoferAAC.a`).

Decode outputs vs the -O0 build on torture streams (white noise +
sweeps, 2000+ frames/codec): **bit-exact** for SBC, AAC and LDAC.

Throughput on the sandbox executor (`qemu-arm-static`, 5 iterations,
ns per decoded frame; -O0 → -O2):

| codec | -O0 | -O2 | speedup | realtime factor @-O2 |
|-------|-----|-----|---------|----------------------|
| SBC (44.1k, 128 samples/frame) | 98 705 | 11 033 | **≈8.9×** | ≈263× |
| AAC (44.1k, 1024 samples/frame) | 1 713 443 | 298 732 | **≈5.7×** | ≈78× |
| LDAC (44.1k, 128 samples/frame) | 100 929 | 70 666 | **≈1.43×** | ≈41× |

(SBC -O0 jitters 77.6k–98.7k between runs; qemu user-mode under-
represents cache effects, so real-hardware deltas will differ, but the
ordering and the F1 conclusion hold.)

## 4. Phase B — float+NEON LDAC decoder (`third_party/libldacdec_float`)

Drop-in variant of the vendored decoder (same API/ABI, same file set):

* `imdct.c/h` — float32 interleaved twiddle tables
  (`SinCosTables[b][2i]=cos, [2i+1]=sin`), float MdctWindow/ImdctWindow;
  the DCT-IV is split into a pre-twiddle plus 6 butterfly stages, each
  vectorized with `float32x4` NEON intrinsics (2 complex pairs per op)
  under `#ifdef __ARM_NEON`, with scalar float tails/fallback.
  ARMv7-canonical idioms only (`vtrnq_f32(W,W).val[]` instead of the
  aarch64-only `vtrn1q/vtrn2q`; `vrev64q_f32` for the [c,s]→[s,c]
  swap; `vmulq_f32` with a `{1,-1,1,-1}` sign vector for the conjugate).
  Interleaved tables also **halve the twiddle memory traffic** and drop
  the separate sin/cos tables (−36 KB static RAM).
* `spectrum.c` — coarse/fine dequantization and spectrum scaling in
  float (scaling factors kept exact powers of two → no rounding change).
* `utility.c/h` — `Round(float)`.
* Remaining `double`s are the dead At9 code in `bit_allocation.c` and
  the (intentional) double-precision constant-table generation.

**Acceptance gate (PASSED):** decode of the torture and sine LDAC
streams with the float build, compared sample-by-sample against the
-O0 **original** decoder output:

| stream | PSNR | differing samples | max diff |
|--------|------|-------------------|----------|
| torture (2069 frames) | **112.50 dB** | 549 / 513 280 | 1 LSB |
| sine (691 frames) | **115.77 dB** | 53 / 160 512 | 1 LSB |

(gate was ≥90 dB; observed diffs are single-LSB roundings at the final
quantization, i.e. the float pipeline is perceptually and numerically
indistinguishable from the double reference.)

Throughput (`qemu-arm-static`, ns/frame): -O0 double 103 118 →
-O2 double 69 403 (1.49×) → **float+NEON 57 758 (1.78× vs -O0)**.
qemu emulates NEON scalarly and does not model the halved memory
traffic, so the on-hardware gain is expected to be larger.

## 5. Phase C — sink glue (`patches/nougat/system_bt/0011-*.patch`)

`btif_media_task.c` / `btif_avrcp_audio_track.cpp`:

* **40 ms pump tick** (`BTIF_SINK_MEDIA_TIME_TICK_MS`, was 20 ms):
  halves the decode-alarm wakeup rate (25/s → 12.5/s per stream), the
  dominant periodic cost of sink playback; cost is +20 ms pipeline
  latency.  All tick-derived budgets (SBC `frames_to_process`, LDAC
  per-tick feeding, scheduling stats) scale off the constant
  automatically.
* **RX buffer pool**: `RxSbcQ` packets are served from a `fixed_queue`
  free list of 32 soft-capped uniform 2048 B slots (largest negotiated
  A2DP payload here is LDAC's ~1008 B) with `osi_malloc`/`osi_free`
  fallback and an oversize-payload guard — removes the per-packet
  allocator churn (~50 malloc/free pairs per second per stream) across
  the BTU and media threads.  All release paths (pump drains, write
  failure, flush, queue-full drop) return buffers to the pool; the pool
  is created with the media thread and drained at cleanup.
* **AudioTrack frameCount 0 → 80 ms** (2× the new tick): explicit
  underrun margin for the TRANSFER_SYNC writer; AudioFlinger consumes
  at real-time rate, so steady-state latency is unchanged.

Decode output is untouched by Phase C (pure scheduling/allocation).

## 6. Building

```sh
# reference build (unchanged behavior, -O0):
scripts/bt_standalone_build.sh --tag android-7.0.0_r36 \
    --workdir /tmp/btw-default --link

# power-optimized build (-O2 everywhere + float/NEON LDAC):
scripts/bt_standalone_build.sh --tag android-7.0.0_r36 \
    --workdir /tmp/btw-opt --opt --link
```

Artifact names are identical; keep the variants in separate workdirs.

## 7. Verification harness (`tools/decode-bench/`)

`codec_harness.cpp` links the same codec objects as the firmware and
exercises generate → encode → decode → PSNR/bench per codec:

```sh
# needs a completed bt_standalone_build.sh workdir (env.sh + obj/):
tools/decode-bench/build_harness.sh lb /tmp/btw-build/obj \
    third_party/libldacdec_float "-O2 -ffp-contract=off"
/tmp/btw-build/harness/lb/harness_lb psnr  dec-ldac f1 f2
/tmp/btw-build/harness/lb/harness_lb bench dec-ldac stream frames
```

`opt_build.sh` drives the Phase-A -O2 object tree.  The harness runs on
x86 hosts via `qemu-arm-static`.  Acceptance results are recorded in
§3–§4; the -O0 decode outputs are the numeric reference.

## 8. Latency budget (Phase C)

| stage | old | new |
|-------|-----|-----|
| decode alarm tick | 20 ms | 40 ms |
| delayed start (5 packets) | ≈40–100 ms | unchanged (packet-count based) |
| AudioTrack buffer | AF minimum | 80 ms explicit (no steady-state add) |
| **worst-case pipeline add** | — | **≈ +20 ms** |

## 9. Scope note

The maintainer removed the real-device power A/B metric (old-vs-new
playback utime/stime comparison) from this work item; Phases A–C above
are the complete delivered scope.  The optimized artifacts are ready
for functional device verification through the existing swap flow
(`docs/NOUGAT_BT_SWAP.md`).
