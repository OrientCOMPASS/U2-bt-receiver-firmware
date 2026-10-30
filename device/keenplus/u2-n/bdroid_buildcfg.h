/******************************************************************************
 *
 *  bdroid_buildcfg.h -- KeenPlus U2 (MT6735M, Android 7.0 baseline)
 *
 *  Device build configuration for the Nougat Fluoride stack
 *  (android-7.0.0_r36), consumed via bt_target.h when the build defines
 *  HAS_BDROID_BUILDCFG.  Every symbol below overrides an #ifndef default
 *  in include/bt_target.h -- nothing here is a stock-tree edit.
 *
 *  Rationale (docs/NOUGAT_BT_SWAP.md):
 *   - the U2 is an A2DP SINK (BT audio receiver); stock MTK firmware already
 *     ships the sink path (its bluetooth.default.so imports AudioTrack and
 *     the sink SEP), so we enable the same feature set on the AOSP tree.
 *   - sink audio renders straight into AudioTrack (USE_AUDIO_TRACK), the
 *     path MTK validated on this device; no audio HAL replacement needed.
 *   - multi-codec sink: SBC + AAC + LDAC-compatible SEPs require 4 local
 *     SEPs (1 src + 3 sink) and codec info elements up to 11 octets
 *     (LDAC vendor CIE), hence AVDT_NUM_SEPS / AVDT_CODEC_SIZE bumps.
 *
 ******************************************************************************/

#ifndef BDROID_BUILDCFG_H
#define BDROID_BUILDCFG_H

/* ---- A2DP sink role (bt_target.h default: FALSE) ---- */
#define BTA_AV_SINK_INCLUDED        TRUE
/* Render decoded sink PCM via android::AudioTrack (btif_avrcp_audio_track),
 * not through the UIPC/audio-HAL channel. */
#define USE_AUDIO_TRACK             TRUE

/* ---- multi-SEP sink (SBC src + SBC/AAC/LDAC sink) ---- */
/* stock default 3 -- one more than our 4 audio SEPs would need headroom for
 * the AR/AVRCP registrations; keep generous. */
#define AVDT_NUM_SEPS               8
/* stock default 10 octets; the LDAC vendor CIE is LOSC(1) + 10 = 11.
 * Matches the 16-octet size later AOSP generations use. */
#define AVDT_CODEC_SIZE             16
/* BTA stream control block SEP array (bta_av_api.h): stock 2 (1 src +
 * 1 sink SEP); the multi-codec sink needs 1 src + 3 sink SEPs
 * (SBC/AAC/LDAC).  The symbol is #ifndef-guarded, so this is a pure
 * config override -- no stock-tree edit. */
#define BTA_AV_MAX_SEPS             4

/* ---- identity ---- */
#define BTM_DEF_LOCAL_NAME          "KeenPlus U2"

#endif /* BDROID_BUILDCFG_H */
