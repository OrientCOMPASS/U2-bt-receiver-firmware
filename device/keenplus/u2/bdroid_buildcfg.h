/*
 * bdroid_buildcfg.h -- KeenPlus U2 Bluetooth compile-time configuration.
 *
 * Referenced by system/bt via BOARD_BLUETOOTH_BDROID_BUILDCFG_INCLUDE_DIR
 * (device/keenplus/u2) with -DHAS_BDROID_BUILDCFG.  See plan section 16.1.
 *
 * Product shape: a Bluetooth audio RECEIVER (A2DP Sink) that can also transmit
 * (A2DP Source).  No phone/HID/health/message/PAN profiles, no BLE.
 */

#ifndef _BDROID_BUILDCFG_H
#define _BDROID_BUILDCFG_H

#define BTM_DEF_LOCAL_NAME        "KeenPlus U2"
#define BTM_INQ_DB_LENGTH         8          /* smaller inquiry DB saves memory */

/* ---- A2DP ---- */
#define BTA_AV_INCLUDED           TRUE
#define BTA_AV_SINK_INCLUDED      TRUE       /* Sink role (F2) */
#define BTA_AV_MAX_STREAMS        2          /* dual-device / one-to-two (F6) */
/*
 * In AOSP 8.1 the A2DP sink PCM path is AudioTrack-only (btif_avrcp_audio_track),
 * so USE_AUDIO_TRACK is no longer a switch.  The macro is kept defined purely for
 * compatibility with any residual MTK patch that still tests it.
 */
#define USE_AUDIO_TRACK

/* ---- Profiles: deliberately left at AOSP 8.1 defaults ----
 * An earlier revision disabled HID Host / HFP Client / Health / MAP / PAN /
 * PBAP / SDP here.  That does NOT link on 8.1: btif_hh.cc (and friends) have
 * NO #if guards and reference BTA_Hh* unconditionally, while the bta/hh
 * sources are internally guarded and compile empty when BTA_HH_INCLUDED is
 * FALSE -> undefined references at libbluetooth link time (observed on the
 * real build machine).  BLE_INCLUDED=FALSE is equally unsafe on 8.1 (the
 * stack only became modular in 9.0).
 *
 * Strategy instead: keep the stock compile-time profile set (link-safe,
 * ~2.1 MiB .so) and disable profiles at RUNTIME through the Bluetooth APK
 * resource overlay (overlay/packages/apps/Bluetooth/res/values/config.xml)
 * and bt_stack.conf.  Unused profiles then never start, so the RAM/boot
 * budgets (R3/R5/R9) are still met.
 */

/* ---- Power saving (plan section 16.6) ---- */
#define BT_HCILP_INCLUDED         FALSE      /* also gated by BOARD_BLUETOOTH_BDROID_HCILP_INCLUDED=0 */
#define BT_WAKE_VIA_PROC          TRUE
#define LPM_BT_DEFAULT_IDLE_THRESHOLD_HOST  300
#define LPM_BT_DEFAULT_IDLE_THRESHOLD_HC    300

/* ---- AVDTP MTU ----
 * LDAC (source) silently fails below an AVDTP MTU of 679 (link shows active,
 * no error log, just no audio).  AOSP 8.1's default AVDT_MTU lives in
 * stack/avdt/avdt_defs.h; if a capture shows it is < 679 raise it to 1024.
 * SBC/AAC are unaffected (small packets).  See plan section 16.7 and risk R11.
 */

#endif /* _BDROID_BUILDCFG_H */
