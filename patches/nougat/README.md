# patches/nougat -- B2 series for `android-7.0.0_r36` (route RESTARTED 2026-10-30)

Target: A2DP **Sink** on the official 7.0 baseline with **AAC decode** and
**LDAC(660 kbps) receive** (DEVELOPMENT_PLAN.md §16.8), deployed by swapping
`/system/lib/hw/bluetooth.default.so` into the debloated system image
(NOUGAT_BT_SWAP.md B4).  Java layer untouched (R3): all codec negotiation
lives in the native stack.

Planned series (each entry CI-gated by `.github/workflows/bt70.yml`:
compile -> link -> B3 symbol gate against the baseline extraction):

| # | subject | content |
|---|---------|---------|
| -- | *(pristine)* | r36 tree compiles & links as-is -- foundation gate |
| 0001 | aac-sink | backport Pie `a2dp_aac_decoder` into N structure: codec dispatch in `btif_a2dp_sink`, AAC SEP/caps in `bta_av_co` (`a2dp_aac.c` already parses sink caps), FDK `libAACdec` statically linked (baseline has no usable AAC .so -- B1) |
| 0002 | sbc-xq | dual-channel bitpool 45.3 negotiation (honor `XQ` caps from source) |
| 0003 | avdt-mtu | `AVDT_MTU` 672 -> 1024 (LDAC needs >= 679, §16.7 R11) |
| 0004 | ldac-sink | `hegdi/libldacdec` (MIT clean-room) + ~300-line shim exporting `ldac_BCO_{init,cleanup,decode_packet,start,suspend,configure}` built as `libldacBT_bco.so`; `SINK_LDAC` SEP + decode dispatch (§16.8).  Label "LDAC-compatible" only -- Sony trademark. |

Status: pristine gate wired up (harness `--tree nougat` auto-selected by the
`android-7.*` tag); series starts after the foundation run is green.
