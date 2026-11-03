/*
 * bdroid_buildcfg.h — U2 蓝牙音频接收器固件的平台构建配置
 *
 * 作用：AOSP system/bt 的 include/bt_target.h 里所有 #ifndef 默认值都可以被这里覆盖。
 * 编译时由 tools/build/build_bt.py 传入 -DHAS_BDROID_BUILDCFG 并把本目录加入 include 路径。
 *
 * 铁律：每阶段只改一个变量；每次改动都在 WORKLOG.md 记录动机与实测结果。
 */

#ifndef BDROID_BUILDCFG_H
#define BDROID_BUILDCFG_H

/* ---------------------------------------------------------------------------
 * P0：A2DP SINK 角色
 *
 * AOSP r36 默认 BTA_AV_SINK_INCLUDED=FALSE。
 * 实测原厂 /system/lib/hw/bluetooth.default.so (md5 e0dd373e4632316a0348e6ec0a288347)
 * 的动态符号里有 android::AudioTrack 的一整套 UND 引用
 * （构造 + start/stop/pause/flush/write/setVolume），即 btif_avrcp_audio_track.cpp
 * 的 sink 播放路径已被编入 → 原厂构建打开了 SINK。
 * 我们的目标是纯接收器，必须打开。
 * ------------------------------------------------------------------------- */
#define BTA_AV_SINK_INCLUDED TRUE

/* A2DP source 保留（AVRCP/一拖多切换、以及回退到原厂行为时有用）。
 * 若后续要做"纯 sink profile 集"，优先在 Java/profile 层裁剪，不在这里关死，
 * 以免一次改两个变量。 */
#define BTA_AV_INCLUDED TRUE

/* 48kHz 优先的全局约束在 codec 协商与 AudioTrack 侧落实（P2/P3），
 * 这里先记录 source 侧默认值以便对照（原厂 AOSP 默认 44100）。 */
#define BTIF_A2DP_SRC_SAMPLING_RATE 48000
#define BTIF_A2DP_SRC_BIT_DEPTH 16
#define BTIF_A2DP_SRC_NUM_CHANNELS 2

#endif /* BDROID_BUILDCFG_H */
