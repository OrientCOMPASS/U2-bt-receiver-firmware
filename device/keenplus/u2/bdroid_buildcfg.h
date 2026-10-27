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

/* ---- Disable unused profiles: saves RAM and startup time (R3/R5/R9) ---- */
#define BTIF_HF_CLIENT_INCLUDED   FALSE      /* HFP Client (no hands-free) */
#define BTA_HF_CLIENT_INCLUDED    FALSE
#define BTIF_HH_INCLUDED          FALSE      /* HID Host */
#define BTA_HH_INCLUDED           FALSE
#define BTA_HL_INCLUDED           FALSE      /* Health */
#define BTIF_HL_INCLUDED          FALSE
#define BTA_MIP_INCLUDED          FALSE
#define BTIF_MAP_INCLUDED         FALSE      /* Message Access */
#define BTA_MAP_INCLUDED          FALSE
#define BTIF_PAN_INCLUDED         FALSE      /* PAN / BT tethering */
#define BTA_PAN_INCLUDED          FALSE
#define SAP_SERVER_INCLUDED       FALSE      /* SIM Access */
#define BTA_SDP_INCLUDED          FALSE
#define PBAP_INCLUDED             FALSE      /* Phonebook Access */
#define BIP_INCLUDED              FALSE      /* Basic Imaging */
#define DUN_INCLUDED              FALSE      /* Dial-up Networking */
#define BTA_PR_INCLUDED           FALSE      /* Printer */

/* ---- BLE: not needed by this product; disabling saves ~10-15 MiB + a thread ----
 * If a companion phone app later needs BLE provisioning/remote, flip these back
 * to TRUE and keep GATT.
 */
#define BLE_INCLUDED              FALSE
#define BTA_GATT_INCLUDED         FALSE
#define BTIF_GATT_INCLUDED        FALSE
#define BTA_BLE_INCLUDED          FALSE

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
