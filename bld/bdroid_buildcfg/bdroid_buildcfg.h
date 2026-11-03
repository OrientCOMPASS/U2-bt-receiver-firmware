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

/* ---------------------------------------------------------------------------
 * P1b：设备身份 —— Class of Device
 *
 * AOSP 默认（include/bt_target.h:397，#ifndef 保护，可被本文件覆盖）：
 *     #define BTA_DM_COD {0x5A, 0x02, 0x0C}     // 0x5A020C = mobile phone
 * 数组是 **MSB 在前**：stack/include/bt_types.h 的 DEVCLASS_TO_STREAM 会按
 * a[2],a[1],a[0] 的顺序写进 HCI_Write_Class_of_Device。
 *
 * 目标是"扬声器"身份 COD 0x240414：
 *     major service class = (0x240414>>13)&0x7FF = 0x120 → Rendering(bit18) + Audio(bit21)
 *     major device class  = (0x240414>>8)&0x1F   = 0x04  → Audio/Video
 *     minor device class  = (0x240414>>2)&0x3F   = 0x05  → Loudspeaker
 * 于是手机/PC 在扫描列表里把本机显示为音频设备（而不是"手机"），并直接给出
 * "媒体音频"选项。
 *
 * 生效路径：bta/dm/bta_dm_cfg.c 的 bta_dm_cfg.dev_class
 *        → bta_dm_act.c:404 BTM_SetDeviceClass(dev_class)（在 bta_dm_enable 里）
 *        → btsnd_hcic_write_dev_class → HCI Write Class of Device
 * ------------------------------------------------------------------------- */
#ifndef BTA_DM_COD
#define BTA_DM_COD {0x24, 0x04, 0x14}
#endif

/* ---------------------------------------------------------------------------
 * P1c：SBC sink caps 收敛到 48kHz-only（全局 48k 硬约束）
 *
 * A2DP 的能力集是位掩码，表达不了"优先"，采样率由 source 在我们的 caps 里挑。
 * 原厂 caps 同时广告 48|44 → 手机/PC 通常挑 44.1k。只广告 48k 是唯一能真正拿到
 * 48k 的办法（source 若坚持 44.1 会在 SET_CONFIG 阶段被拒，符合 A2DP 规范）。
 *
 * 实测记录见 WORKLOG.md（P1c 一节）。若某台 source 因此连不上：
 *   1) 先记录机型/系统版本到 WORKLOG；
 *   2) 把下面改成 FALSE 回退到 48|44，并在 WORKLOG 标注该机型走了回退。
 * ------------------------------------------------------------------------- */
#ifndef U2_SBC_SINK_48K_ONLY
/* 2026-11-04 回退为 FALSE（原为 TRUE）—— **线上取证证明 48k-only 不可行**：
 *
 *   AVDTP GET_CAPABILITIES 实测（snoop，tools/dev/parse_snoop.py --avdtp）
 *     [226] 本机 SNK 应答 : SBC freq[48k]   mode[MONO|DUAL|STEREO|JOINT] bitpool 2..250
 *     [321] nubia Z40 SRC : SBC freq[44.1k] mode[MONO|JOINT] subbands[8] alloc[LOUDNESS] bitpool 2..53
 *   → 采样率交集为空；整份抓包里 **没有任何 SET_CONFIGURATION**，
 *     手机在 GET_CAPS 之后直接放弃并断开（HCI Disconnection reason=0x13）。
 *     Windows(C-TX4) 表现相同：只到 GET_CAPS 就停，故"连上但不是可用音频设备"。
 *
 * 根因不在我们：AOSP r36 的 SRC caps `bta_av_co_sbc_caps` 只广告
 * `A2D_SBC_IE_SAMP_FREQ_44`（btif/co/bta_av_co.c），Android 手机作为 source 只有 44.1k SBC；
 * Windows 的 SBC source 实测同样只有 44.1k。
 * → **48kHz 硬约束在 SBC 上无法通过"只广告 48k"实现**，只能靠 source 自己愿意发 48k，
 *   或改用 AAC(P2)/LDAC(P3) —— 这两条链路才是 48k 的正确落点。
 *
 * 后续若要"优先 48k、回退 44.1k"，有两条不会把设备变砖的路（详见 docs/05 §3.5 策略 5/6）：
 *   (a) 注册**两个 SNK SEP**：SEID1 = 48k-only，SEID2 = 48|44；source 会挑第一个能满足的；
 *   (b) caps 保持 48|44，但在 SET_CONFIG 校验时"若对端 caps 含 48k 则拒绝 44.1k"
 *       （对端 caps 我们已经作为 CT 通过 GET_CAPABILITIES 拿到了，见 snoop [319]）。
 *   两者都有"source 不重试就直接失败"的风险，必须在 P2 之后单独做一轮实验。
 */
#define U2_SBC_SINK_48K_ONLY FALSE
#endif

/* 48kHz 优先的全局约束在 codec 协商与 AudioTrack 侧落实（P2/P3），
 * 这里先记录 source 侧默认值以便对照（原厂 AOSP 默认 44100）。 */
#define BTIF_A2DP_SRC_SAMPLING_RATE 48000
#define BTIF_A2DP_SRC_BIT_DEPTH 16
#define BTIF_A2DP_SRC_NUM_CHANNELS 2

#endif /* BDROID_BUILDCFG_H */
