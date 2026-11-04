/*
 * u2_sink_policy.h — U2 蓝牙 A2DP SINK 接收器：native 策略层（本项目自有代码，非 AOSP）
 *
 * 为什么需要这一层
 * ----------------
 * 实测（P0）：原厂 /system/lib/hw/bluetooth.default.so 里 A2DP **SINK** 的 native 机制
 * 是齐全的（btif_av_sink_execute_service / BTA_AvEnable_Sink / AVDT_SINK_Activate /
 * bta_av_co_audio_sink_supports_config / bta_av_co_sbc_sink_caps …），
 * 但 /system/app/Bluetooth/Bluetooth.apk 只启用 7 个 source/host 侧 profile
 * （Gatt/Headset/A2dp-SRC/Hid/Health/Pan/Map），**没有任何 Java sink 服务去调用它们**。
 *
 * 本项目走 native-only 路线（不换 APK，没有 MTK 平台签名私钥），所以
 * "启用 sink"这件事必须由我们自己在 native 侧完成。本模块就是那个入口。
 *
 * 关键时序约束（务必保持）
 * ----------------------
 * r36 的 btif_av 只有**一个** AV 槽位：btif_av_init() 以 `sm_handle == NULL` 为门槛，
 * 谁先调用谁拿到 service enable 的机会（后调用者只设回调、不再 enable）。
 * 而 bta_av_api_enable() 第一件事是 `memset(&bta_av_cb, 0, sizeof(tBTA_AV_CB))`，
 * 所以 BTA_AvEnable 全生命周期**只能调用一次** —— source 与 sink 不可并存。
 *
 * 因此本模块必须在 **btif_init_ok()**（btif task，BTA_EnableBluetooth 之前）被调用：
 *   1. 抢在 Java A2dpService 的 init_src() 之前占住 AV 槽位（顺带天然屏蔽 source 注册）；
 *   2. 把 BTA_A2DP_SINK_SERVICE_ID 置入 btif_enabled_services，
 *      由 btif_dm.c 的 BTA_DM_ENABLE_EVT service 循环**恰好一次**地执行
 *      btif_av_sink_execute_service(TRUE) → BTA_AvEnable + BTA_AvRegister(AUDIO_SINK)。
 *
 * 详见 docs/05-roadmap-and-design.md。
 */

#ifndef U2_SINK_POLICY_H
#define U2_SINK_POLICY_H

#include "bta_api.h"   /* tBTA_DM_DISC / tBTA_DM_CONN */

#ifdef __cplusplus
extern "C" {
#endif

/* 专用接收器策略开关：常驻"可发现 + 可连接"。
 *
 * 手机默认只在蓝牙设置界面打开时才 discoverable，且息屏后可能被 Java 侧降回
 * SCAN_MODE_CONNECTABLE / NONE；而音箱必须随时能被扫到并配对。
 * 置 1 时：适配器 ON 后由本模块主动设 visibility，并覆盖 Java 后续的降级请求。
 * 置 0 时：完全交回 Java（原厂行为），便于做对照实验。 */
#ifndef U2_ALWAYS_DISCOVERABLE
#define U2_ALWAYS_DISCOVERABLE 1
#endif

/*
 * 安装 sink 策略。幂等：同一个进程内重复调用只会补置 service 位，不会二次 BTA_AvEnable。
 *
 * 调用点：btif/src/btif_core.c 的 btif_init_ok()，在 BTA_EnableBluetooth(bte_dm_evt) 之前。
 * 线程：btif task（与 Java 的 JNI 调用不在同一线程，故此处不存在与 init_src 的竞态：
 *       Java 只有在收到 STATE_ON 之后才会去 init 各 profile，而那发生在 btif_init_ok 之后）。
 */
void u2_sink_policy_install(void);

/*
 * 适配器进入 STATE_ON 之后调用（btif_enable_bluetooth_evt 的成功分支）。
 * 作用：把本机设为"通用可发现 + 可连接"，让手机无需先进设置界面就能扫到并配对。
 * 线程：btif task（BTA_DM_ENABLE_EVT 已处理完，DM 已 enable，可以安全下发 visibility）。
 */
void u2_sink_policy_on_adapter_on(void);

/*
 * 覆盖 Java 侧的 scan mode 请求（btif_set_adapter_property 的
 * BT_PROPERTY_ADAPTER_SCAN_MODE 分支）：无论上层请求什么，都保持
 * GENERAL_DISCOVERABLE + CONNECTABLE。U2_ALWAYS_DISCOVERABLE==0 时为空操作。
 * 传指针，就地修改。
 */
void u2_sink_policy_force_scan_mode(tBTA_DM_DISC *disc_mode, tBTA_DM_CONN *conn_mode);

/* ===========================================================================
 * P1d：AVRCP TG 绝对音量（Absolute Volume）
 *
 * 为什么要有这一层（真机取证，见 WORKLOG 2026-11-04）
 * -------------------------------------------------
 * 我们是 A2DP **SINK**，所以对端（手机/PC）是 AVRCP **CT**，通过 1.4 的绝对音量控制
 * **我们**的输出电平。BTA 层其实已经就绪：因为注册的是 SINK，bta_av_api_register()
 * 选中 bta_avk_cfg，其 p_meta_evt_ids = {AVRC_EVT_VOLUME_CHANGE}（产物二进制实测 0x0d），
 * TG 的 SDP 记录也是 AVRC_REV_1_4。
 * 但 btif_rc.c 的 BTA_AV_META_MSG_EVT 派发器有三支：
 *     bt_rc_callbacks != NULL        → handle_rc_metamsg_cmd()      (Java AvrcpTarget)
 *     bt_rc_ctrl_callbacks != NULL   → handle_avk_rc_metamsg_cmd()  (Java AvrcpController)
 *     else                           → "Neither CTRL, nor TG is up, drop meta commands"
 *
 * ★ P1d 的判断（"两个回调都是 NULL、命中第三支"）被 **P1d2 的 snoop 取证推翻**：
 *   本机 Bluetooth.apk 的 AvrcpTargetService 其实起来了（logcat `get_profile_interface avrcp`），
 *   所以 bt_rc_callbacks != NULL → 命令走**第一支** handle_rc_metamsg_cmd()
 *   → btif_rc_upstreams_evt()，而后者**没有** AVRC_PDU_SET_ABSOLUTE_VOLUME 的 case，
 *   落到 `default: send_reject_response(..., AVRC_STS_BAD_CMD)`。
 *   17.6MB 真机 snoop（Windows 拖动滑块）逐帧实锤：
 *     [486] rx GET_CAPABILITIES(EVENTS) → [487] tx events=[0x0d]        ✔ 已正确广告
 *     [496] rx REGISTER_NOTIFICATION(VOLUME_CHANGED) → 无任何响应        ✘ 应回 INTERIM
 *     [786][833][879][925][972][1018] tx RSP **REJECTED** SET_ABS_VOL    ✘ 每拖一次一条
 *   （[516]/[6145] 是我们自己以 CT 身份发出的 REGISTER_NOTIFICATION，被 [6150] REJECTED，
 *     来自 handle_rc_features() 的 register_volumechange()，与音量滑块无关。）
 *   P1d 加的第三支在当前配置下是**死代码**（真机零条 u2_sink_rc_metamsg_cmd 日志）。
 *
 * P1d2 的修法：把两条 PDU 的处理抽成 btif_rc.c 的共享函数
 * u2_sink_rc_take_metamsg_cmd()，在**第一支**里截获（截获后直接 return，
 * 不再上报 Java，否则同一 label 会被应答两次），第三支复用同一函数。
 * 同时修了 r36 上游一个真 bug：avrc_pars_tg.c 的 avrc_pars_vendor_cmd()
 * 解析 SET_ABSOLUTE_VOLUME 时**只校验长度、没取音量字节**（volume 恒为 0）。
 *
 * 分工：AVRCP 协议编解码留在 btif_rc.c（要用它的 static send_metamsg_rsp /
 * send_reject_response 与 btif_rc_cb），本模块**只持有音量状态**并负责把增益落到
 * AudioTrack。线程：写入在 btif task（AVRCP 命令）与 media task（track 创建后重放）；
 * 增益的实际施加由 media task 执行（见 btif_media.h 的 tBTIF_MEDIA_SINK_TRACK_GAIN）。
 *
 * 语义边界（Microsoft《Bluetooth Classic audio accessories》AVRCP–Volume）：
 *   ✔ "shall adjust audible speaker volume when receiving SetAbsoluteVolume" → gain=vol/127
 *   ✔ "shall fully mute the speaker when the AVRCP volume is 0"              → setVolume(0)
 *   ✔ "shall properly support receiving the GetCapabilities PDU"             → BTA 层已答 0x0d
 *   ✘ "shall complete the Volume Change Notification when the volume changes
 *      locally on the speaker accessory" —— **本机尚无本机音量 UI**（硬件音量键改的是
 *      AudioFlinger 的 stream volume，我们观测不到），故暂无 CHANGED 上报路径；
 *      簿记（rc_notif[].bNotify/label）已就位，P1e 线控音量接入时补上即可。
 *   ✔ "when Windows changes volume remotely with SetAbsoluteVolume, accessories
 *      shall not complete the Volume Change Notification" → 截获函数刻意不回 CHANGED。
 * ========================================================================= */

/* P1d 总开关（默认开；置 0 则整条绝对音量路径回到原厂行为，便于对照实验）。
 * 也在 bld/bdroid_buildcfg/bdroid_buildcfg.h 里显式声明一份。 */
#ifndef U2_AVRCP_TG_ABSVOL
#define U2_AVRCP_TG_ABSVOL 1
#endif

/* AVRCP 绝对音量范围：0..127（AVRC_EVT_VOLUME_CHANGE 的 param 只有低 7 位有效） */
#define U2_RC_ABS_VOL_MIN   0
#define U2_RC_ABS_VOL_MAX   127
/* 对端还没下发音量时的默认值。选 127 而不是 0：宁可先给满音量，
 * 也不要出现"连上了却完全没声音"这种最难排查的现象。 */
#define U2_RC_ABS_VOL_DEF   127

/* 当前绝对音量（0..127）。用于回 RegisterNotification(VOLUME_CHANGED) 的 interim。 */
unsigned char u2_sink_policy_rc_get_abs_vol(void);

/* 记录对端下发的绝对音量并**立即**应用到 AudioTrack 增益（vol/127）。
 * 超出范围的值会被夹到 0..127 并打 WARNING（对端固件不规范时的自保）。 */
void u2_sink_policy_rc_set_abs_vol(unsigned char vol);

/* AudioTrack 刚创建好时调用：把已保存的音量重放一次。
 *
 * 必须有的原因（源码级）：btif_a2dp_set_audio_track_gain() 把增益**投递到 media task**
 * （P1d2 起，避免跨线程解引用 audio_track 的 use-after-free），而 media task 的
 * BtifAvrcpSetAudioTrackGain() 在 handle==NULL 时只打一条 DEBUG 就返回。
 * Windows 常在**连接时**（还没开流、audio_track 仍为 NULL）就下发 SetAbsoluteVolume，
 * 那次设置会被静默丢掉。
 * 调用点：btif_media_task.c 的 btif_media_task_aa_handle_decoder_reset()，
 *         BtifAvrcpAudioTrackCreate() 成功之后（本函数运行在 media task）。 */
void u2_sink_policy_rc_on_track_created(void);

/* RC 断开时清状态（音量恢复到默认，注册标记清零），避免跨连接串味。 */
void u2_sink_policy_rc_reset(void);

/* P1d 总开关：U2_AVRCP_TG_ABSVOL == 0 时本模块全部为空操作，
 * btif_rc.c 的派发器回到原厂行为（第三支只打日志），便于做对照实验。 */
int u2_sink_policy_rc_enabled(void);

/* 只读快照，便于日志/调试（不进 media 热路径）。 */
unsigned int u2_sink_policy_sample_rate(void);   /* 最近一次协商采样率，0 = 尚未协商 */
unsigned int u2_sink_policy_channel_count(void);
int          u2_sink_policy_is_installed(void);

#ifdef __cplusplus
}
#endif

#endif /* U2_SINK_POLICY_H */
