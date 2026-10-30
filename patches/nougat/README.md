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

### No-ops (kept out of the series deliberately)

* **sbc-xq**: r36 `bta_av_co_sbc_sink_caps` already advertises the full
  bitpool range (`A2D_SBC_IE_MAX_BITPOOL` = 53) with joint/dual/stereo --
  XQ-capable sources can negotiate dual-channel bitpool 45.3 as-is.
* **avdt-mtu**: r36 `BTA_AV_MAX_A2DP_MTU` is already 1008 (>= the 679 that
  LDAC 660 kbps needs); the stock 672 value only exists on older branches.

### Companion config (NOT a patch -- pure bdroid overrides)

`device/keenplus/u2-n/bdroid_buildcfg.h` (consumed via `-I$BDCFG`):
`BTA_AV_SINK_INCLUDED=TRUE`, `USE_AUDIO_TRACK=TRUE`, `BTA_AV_MAX_SEPS=4`,
`AVDT_NUM_SEPS=8`, `AVDT_CODEC_SIZE=16` (LDAC CIE is LOSC+10=11 octets;
stock 10 is too small), `BTM_DEF_LOCAL_NAME="KeenPlus U2"`.
All symbols are `#ifndef`-guarded in the stock tree -- no stock edits.

Harness support (scripts/bt_standalone_build.sh): stages
`third_party/libldacdec` into the source root (root key `libldacdec`,
`-std=gnu99` for the GNU statement-expression macros in `ldacdec.h`),
links its objects into `bluetooth.default.so` together with the Fraunhofer
AAC objects (`libAACdec` is already globbed from `external/aac`).
