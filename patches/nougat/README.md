# patches/nougat -- B2 series for `android-7.0.0_r36` (route RESTARTED 2026-10-30)

Target: A2DP **Sink** on the official 7.0 baseline with **AAC decode** and
**LDAC(660 kbps) receive** (DEVELOPMENT_PLAN.md §16.8), deployed by swapping
`/system/lib/hw/bluetooth.default.so` into the debloated system image
(NOUGAT_BT_SWAP.md B4).  Java layer untouched (R3): all codec negotiation
lives in the native stack.

Every entry is CI-gated by `.github/workflows/bt70.yml`:
compile (433+ TUs) -> link (`bluetooth.default.so`, `audio.a2dp.default.so`)
-> B3 symbol gate against the baseline extraction (`--proven-und`).

## Series status

| # | subject | state | content |
|---|---------|-------|---------|
| -- | *(pristine)* | GREEN (run 40949835930) | r36 tree compiles & links as-is -- foundation gate |
| 0001 | a2dp-sink control plane | **shipped** | `system_bt/0001-*.patch` |
| 0002 | a2dp-sink data plane | **shipped** | `system_bt/0002-*.patch` |
| 0003 | loudspeaker COD, SDP attr len 1024 | **shipped** | `system_bt/0003-*.patch` |
| 0004 | raise AV SEP/link/str limits (AAC+LDAC+multipoint) | **shipped** | `system_bt/0004-*.patch` |
| 0005 | passive sink: no active INT open on CONNECT_REQ | **shipped** | `system_bt/0005-*.patch` |
| 0006 | declare AVDTP/A2DP 1.3 in SDP records | **shipped** | `system_bt/0006-*.patch` |
| 0007 | true multipoint A2DP sink (一拖二): dual SCB slots | **shipped** | `system_bt/0007-*.patch` |
| 0008 | multipoint 3 devices + preemptive suspend + 48 kHz | **shipped** | `system_bt/0008-*.patch` |
| 0009 | slot routing by runtime scb hndl | **shipped** (device-tested silence fix) | `system_bt/0009-*.patch` |
| 0010 | multipoint STANDARD rework: transactional media switch | **new** (standalone build GREEN, awaiting device test) | `system_bt/0010-*.patch` |
| 0011 | decode power opt: 40 ms sink tick, RX buffer pool, 80 ms track | **new** (sandbox-verified: -O0/-O2 builds GREEN; see `docs/DECODE_POWER_OPTIMIZATION.md`) | `system_bt/0011-*.patch` |
| ~~0002'~~ | sbc-xq | **no-op** | see below |
| ~~0003'~~ | avdt-mtu | **no-op** | see below |
| ~~0004'~~ | ldac bco-shim | **superseded** | direct static libldacdec, no shim/dlopen |

### 0001 -- a2dp-sink: register AAC and LDAC sink endpoints (control plane)

r36 registers exactly ONE sink SEP (SBC).  Patch adds the multi-codec
control plane:

* `stack/a2dp/a2d_aac.c` + `stack/include/a2d_aac.h`: `A2D_Bld/ParsAacInfo`
  for the MPEG-2/4 AAC CIE (A2DP spec 4.3.2), modeled on `a2d_sbc.c`.
* `stack/a2dp/a2d_ldac.c` + `stack/include/a2d_ldac.h`: `A2D_Bld/ParsLdacInfo`
  for the Sony LDAC vendor CIE (vendor id 0x012D, codec id 0x00AA, LOSC 10);
  `a2d_api.h` gains `A2D_MEDIA_CT_VEND` (0xFF, absent from r36).
* SEP registration (`bta_av_main.c`): SNK role loops idx 1..`BTA_AV_MAX_SEPS`-1
  calling `cos.init` until declined.  With `BTA_AV_MAX_SEPS=4` the set is
  SRC/SBC + SNK/SBC + SNK/AAC + SNK/LDAC.
* `bta_av_co.c`: AAC/LDAC sink caps tables; init callout cases;
  `sink_supports_config` accepts M24/LDAC-VEND (cfg-in-cap helpers);
  INT-side selection prefers **LDAC > AAC > SBC** (priority scan in
  `peer_src_supports_codec` + per-codec preferred-config builders);
  `setconfig` on a SNK SEP accepts any codec we advertise (previously
  forced SBC reconfiguration for everything else).
* `btif_av_api.h` / `btif_av_co.h`: `BTIF_AV_CODEC_AAC/LDAC` ids
  (LDAC = 0xFE; 0xFF collides with `BTIF_AV_CODEC_NONE`) and the two new
  SEP indices.

Wire format verified byte-for-byte against the AOSP android-11 constants
(host round-trip test): AAC caps `08 00 02 C0 01 8C 84 E2 00`,
LDAC caps `0A 00 FF 2D 01 AA 00 3C 07 00 00 00 00`.

### 0002 -- btif_media: decode AAC (FDK/LATM) + LDAC (libldacdec) (data plane)

* Decoder reset dispatches on `codec_info[2]`: SBC keeps the OI path;
  AAC opens FDK `aacDecoder` in `TT_MP4_LATM_MCP1` mode (RFC 3016 payloads);
  LDAC heap-allocates a `ldacdec_t` (vendored MIT core,
  `third_party/libldacdec`, direct static link -- the old plan's
  `libldacBT_bco.so` shim is NOT used).  AudioTrack is created from the
  parsed CIE sample rate / channel mode.
* Rx path: AAC feeds the whole RTP payload to `aacDecoder_Fill` and drains
  `aacDecoder_DecodeFrame`; LDAC skips the 1-octet media payload header
  (low nibble = frame count, same layout as SBC) and calls `ldacDecode`
  per frame.  The enque pump stamps num_frames=1 for AAC (LATM has no
  media payload header) and the 20 ms decode tick drains whole packets
  for AAC/LDAC instead of the SBC per-frame budget.

### 0010 -- multipoint standard rework: transactional media switch, strict routing/ownership, preemptive AVRCP PAUSE

Fixes the field bug: two phones connected, the second preempts the first;
when the first resumes playback the receiver stays **permanently silent**.
Full analysis in `docs/MULTIPOINT_STD_ANALYSIS.md` (defects D1-D9 audited
against android11-release `btif_a2dp_sink.cc`, android10-release
`A2dpSinkStreamHandler` and BlueALSA).

* `btif_media_task.c`: new `BTIF_MEDIA_AUDIO_SINK_SWITCH` event +
  `btif_media_task_sink_switch_req(codec_info)` -- the switch (stop pump,
  flush queue, tear down track/decoders, rebuild from codec_info) executes
  as ONE media-worker message, mirroring A11 `btif_a2dp_sink_restart_session`.
  A recursive `sink_media_lock` (A11 `g_mutex` model) serializes enqueue
  (BTU thread, incl. the delayed start at 5 frames), rx_flush, start/stop
  decoding, clear_track and decoder reset, closing the D1 race.  NULL-track
  guards in `start_decoding` / `btif_sink_write_audio` / AudioTrack
  Start+WriteData (A11 parity).
* `btif_av.c`: strict stream-event routing -- an event whose hndl matches no
  slot is dropped + logged, never falls back to slot 0 (D2 poison: a stray
  CLOSE used to unbind healthy slot 0 and wedge `rx_flush=TRUE` forever);
  audio ownership is claimed in the START router BEFORE the SM dispatch and
  posts the transactional switch; all SUSPEND/STOP ownership guards are
  strict `owner == ctx` (D4); `stop_with_fallback` scans all 3 slots (D3);
  the media accept gate is owner-only (D7); OPEN-failure unbinds the
  pre-bound slot; IDLE entry clears the runtime hndl; a preempted peer now
  also receives AVRCP PASS THROUGH PAUSE over its own RC channel (D8, the
  android10 focus-loss policy) so its player cannot auto-reSTART into a
  switch war; `BTA_AvClose/OpenRc/CloseRc` use the runtime scb hndl (D9 --
  the register hndl hit the wrong scb whenever streaming hndl != register
  hndl, killing Java-initiated disconnect/RC).
* `bta_av_aact.c`: NULL scb guard in the `bta_av_stream_data_cback` scan
  (D5 -- `BTA_AV_NUM_STRS` > allocated scbs was a NULL deref).
* Companion config: `AVDT_NUM_SEPS` 8 -> 12 (D6 -- each of the 3
  registrations creates 4 local SEPs; with 8, the third peer's SEID 9+
  failed `AVDT_CreateStream`).

### 0011 -- decode power optimization: sink glue (40 ms tick, RX pool, track buffer)

Part of the stage-2 decode power work (full analysis, phases A/B and all
measurement gates: `docs/DECODE_POWER_OPTIMIZATION.md`).  Pure
scheduling/allocation glue -- decode output is unchanged:

* `btif_media_task.c`: `BTIF_SINK_MEDIA_TIME_TICK_MS` 20 -> 40 ms
  (halves the decode-alarm wakeup rate; every tick-derived budget scales
  off the constant); the per-RTP-packet `osi_malloc`/`osi_free` of
  `btif_media_sink_enque_buf()` is replaced by a soft-capped
  `fixed_queue` pool of uniform 2048 B slots (malloc fallback +
  oversize guard), with ALL release paths (pump drains, write-fail
  drop, flush, queue-full drop) returning buffers to the pool; pool
  lifecycle follows the media thread.
* `btif_avrcp_audio_track.cpp`: explicit 80 ms AudioTrack buffer
  (2x the new tick) instead of frameCount 0 -- underrun margin for the
  sync writer, no steady-state latency add.
* Build companion: `scripts/bt_standalone_build.sh --opt` compiles
  every TU `-O2 -ffp-contract=off` and stages
  `third_party/libldacdec_float` (float32 + NEON DCT-IV LDAC decoder;
  PSNR 112.5/115.8 dB vs the -O0 double reference, 1-LSB max diff).
* Latency cost: worst case ~ +20 ms pipeline (tick).
* Sandbox gates: both variants compile + link GREEN; -O2 decode is
  bit-exact vs -O0 for SBC/AAC/LDAC (harness `tools/decode-bench/`).

### No-ops (kept out of the series deliberately)

* **sbc-xq**: r36 `bta_av_co_sbc_sink_caps` already advertises the full
  bitpool range (`A2D_SBC_IE_MAX_BITPOOL` = 53) with joint/dual/stereo --
  XQ-capable sources can negotiate dual-channel bitpool 45.3 as-is.
* **avdt-mtu**: r36 `BTA_AV_MAX_A2DP_MTU` is already 1008 (>= the 679 that
  LDAC 660 kbps needs); the stock 672 value only exists on older branches.

### Companion config (NOT a patch -- pure bdroid overrides)

`device/keenplus/u2-n/bdroid_buildcfg.h` (consumed via `-I$BDCFG`):
`BTA_AV_SINK_INCLUDED=TRUE`, `USE_AUDIO_TRACK=TRUE`, `BTA_AV_MAX_SEPS=4`,
`AVDT_NUM_SEPS=12` (3 registrations x 4 SEPs; was 8 until patch 0010/D6),
`AVDT_CODEC_SIZE=16` (LDAC CIE is LOSC+10=11 octets;
stock 10 is too small), `BTM_DEF_LOCAL_NAME="KeenPlus U2"`.
All symbols are `#ifndef`-guarded in the stock tree -- no stock edits.

Harness support (scripts/bt_standalone_build.sh): stages
`third_party/libldacdec` into the source root (root key `libldacdec`,
`-std=gnu99` for the GNU statement-expression macros in `ldacdec.h`),
links its objects into `bluetooth.default.so` together with the Fraunhofer
AAC objects (`libAACdec` is already globbed from `external/aac`).
