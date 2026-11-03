# docs/05 — 架构决策与路线图

## 1. 需求 → 阶段映射

| 需求 | 内容 | 阶段 |
|---|---|---|
| R1 | 基线 SINK：SBC 在 Android 手机 + Windows 上稳定、音量可控 | **P1a–P1d** |
| R1′ *(2026-11-04 用户修订)* | 身份 = **"有麦克风的耳机"**，且是**真 HFP**（接打电话 + 真麦克风上行），不是只换 COD 图标 | **P1f** |
| R1″ *(同上修订)* | 播放/暂停方向 = **线控插在 U2 上，由 U2 控制手机/PC** → U2 是 AVRCP **CT**，发 passthrough | **P1e** |
| R2 | AAC 解码（fdk-aac，LATM），48kHz 优先 | **P2** |
| R3 | LDAC 解码（open-vela/external_libldac dev 分支 2.0.72，**全源码静态链接，禁止闭源 blob**），48k@990kbps 优先 | **P3** |
| R4 | 一拖二：2 台手机同时 A2DP 连接、单路活跃流、切换 ≤1s、24h 双连 | **P4** |
| R5 | 72h 老化：零 tombstone / 零 EBADF / 无可闻断音 | **P5** |
| R6 | 工程化：每阶段 commit + Release（`pN-topic-date`）+ WORKLOG | 全程 |
| 全局硬约束 | **48kHz 全链路**（协商 → 解码输出 → AudioTrack 采样率）；44.1k 仅作回退 | P1 起贯穿 |

## 2. 关键架构决策：**native-only sink，不换 APK**

### 2.1 事实依据（P0 实测 + 源码核对）

1. **原厂 APK 只启用 7 个 source/host 侧 profile**（Gatt/Headset/A2dp-SRC/Hid/Health/Pan/Map），
   `grep -i -e sink -e A2dpSink -e AvrcpController -e HfpClient` → **0 命中**。Java 层没有 sink 服务在跑。
2. **但 native 层的 sink 机制全都在**，而且已经编进了我们自己的 P0 产物。
   对 `bld/out/bluetooth.default.so.unstripped` 做 `llvm-nm` 实测：

   ```
   BTA_AvEnable_Sink          BTA_AvEnable           BTA_AvRegister
   bta_av_api_sink_enable     AVDT_SINK_Activate     AVDT_SINK_Deactivate
   bta_av_co_audio_sink_supports_config             bta_av_co_audio_sink_supports_cp
   bta_av_co_audio_sink_getconfig                   bta_av_co_sbc_sink_caps
   btif_av_sink_execute_service                     btif_av_get_sink_interface
   btif_av_is_sink_enabled    init_sink             cleanup_sink      src_connect_sink
   ```
   （均为 `t`/`d`/`b` 本地符号 —— 因为链接用了 `-Bsymbolic`，不影响内部调用。）
3. **`com.android.bluetooth` 的 JNI 不链接 `bluetooth.default.so`**（符号审计已确认），
   所以 Java 层不会因为我们的导出符号集变化而崩；native 侧可以自主决定启用哪些 service。
4. APK 替换代价高且有风险：`/system/app/Bluetooth/Bluetooth.odex`（8.6MB）必须 `rm -rf oat`；
   `/system/app` 的 APK 由 **MTK platform key** 签名，我们没有该私钥 →
   自签替换会触发 PackageManager 签名校验失败，除非再动 `/data/system/packages.xml`（高危）。

### 2.2 决策

> **只改 `/system/lib/hw/bluetooth.default.so`。**
> 在我们的 `.so` 内部主动完成 sink service 注册、SEP/caps 广播、COD 设置、可发现性、
> 音频输出（native `AudioTrack`）与 AVRCP 控制，不依赖任何 Java sink 服务。

好处：单一交付物、可原子刷入/回滚、无签名问题、不动 odex、与"一次一个变量"的工程纪律天然契合。

## 3. P1 技术方案（逐条都有源码依据）

### 3.1 启用 sink service

`btif/src/btif_dm.c:330` 的 `btif_in_execute_service_request()` 已经能处理
`BTA_A2DP_SINK_SERVICE_ID → btif_av_sink_execute_service(TRUE)`，但**没人调用它**
（Java 侧没有 A2dpSinkService）。我们要自己在 native 触发：

```
init_sink(&u2_sink_callbacks)            /* btif_av_init(BTA_A2DP_SINK_SERVICE_ID) + 我们自己的回调结构 */
btif_av_sink_execute_service(TRUE)       /* BTA_AvEnable(NO_SCO_SSPD|RCCT|METADATA|VENDOR|ADV_CTRL|RCTG)
                                            + BTA_AvRegister(CHNL_AUDIO, BTIF_AVK_SERVICE_NAME,
                                                             UUID_SERVCLASS_AUDIO_SINK) */
```

- `bt_av_sink_callbacks` 的**所有**使用点都有 `!= NULL` 保护（`btif_av.c:234/262/284/344/387/562/1358/1440`），
  因此传 NULL 也不会崩；但我们**要传自己的回调结构**，以便在 native 拿到
  `connection_state_cb` / `audio_state_cb` / `audio_config_cb`（P4 一拖二与 48k 校验都靠它）。
- sink 的 AVRCP 角色是 **CT + TG**（`BTA_AV_FEAT_RCCT | BTA_AV_FEAT_RCTG`，已在 `btif_av_sink_execute_service` 里给全）：
  CT 用于我方发播放/暂停，TG 用于手机侧下发 Absolute Volume。

### 3.2 音频输出：**必须打开 `USE_AUDIO_TRACK`**（P1 最关键的一改）

r36 的 sink PCM 出口有两条互斥路径，由 `USE_AUDIO_TRACK` 决定：

| 宏 | 出口 | 在手机上 |
|---|---|---|
| **未定义（现状）** | `UIPC_Send(UIPC_CH_ID_AV_AUDIO, …)`（`btif_media_task.c:1594` 的 `#else` 分支） | **死路**：没有 UIPC 消费者；且 `btif_media_task_handle_inc_media()` 里 `#ifndef USE_AUDIO_TRACK` 分支会因 `data_channel_open == FALSE` **直接 return** |
| **定义（目标）** | `BtifAvrcpAudioTrackWriteData()` → native `android::AudioTrack`（`btif_avrcp_audio_track.cpp`） | ✅ 直接出声，音量交给 AudioFlinger |

实测：`grep -rn "USE_AUDIO_TRACK" --include=*.mk src/` → **0 命中**，`bld/plan.json` 的 flags 里也没有它。
也就是说 **AOSP r36 的官方 make 根本不开这个宏**，原厂 `.so` 里的 `AudioTrack` 未定义符号
只是"文件被编了但没人调用"的结果。

→ P1 第一步（单变量）：在 `bld/bdroid_buildcfg/` 或 `build_bt.py` 的 flags 里加 `-DUSE_AUDIO_TRACK`。

### 3.3 焦点（focus）门禁：native 侧自己给 GRANTED

打开 `USE_AUDIO_TRACK` 后会出现**新的门禁**（`btif_media_task.c:1319-1326`）：

```c
#ifdef USE_AUDIO_TRACK
    if (btif_media_cb.rx_audio_focus_state == BTIF_MEDIA_FOCUS_NOT_GRANTED)
        return;                 /* 丢帧，不出声 */
#endif
```

而初值是 **NOT_GRANTED**（`btif_media_task.c:931`），只有 Java 通过
`bt_av_sink_interface.update_audio_focus_state()`（`USE_AUDIO_TRACK` 才编入）才会改它。
我们没有 Java sink 服务 → **必须在 native 侧自己授予焦点**。

方案：新增 `src/u2/u2_sink_policy.c`（我们自己的文件，不改 AOSP 语义），在
`audio_state_cb(STARTED)` 时调用 `btif_a2dp_set_audio_focus_state(BTIF_MEDIA_FOCUS_GRANTED)`，
并在 media task 初始化后设一次默认 GRANTED。
理由：专用接收器上没有其他抢焦点的音频应用，"永远已授权"是正确且最简单的策略。

### 3.4 COD 与可发现性

- COD 走 `bta/dm/bta_dm_cfg.c` 的 `bta_dm_cfg.dev_class` → `BTM_SetDeviceClass()`（`bta_dm_act.c:404`）。
  目标 `0x240414` = `dev_class = {0x24, 0x04, 0x14}`（Major: Audio/Video，Minor: Loudspeaker）。
  改完后手机端应显示为"扬声器/音频设备"图标，而不是"手机"。
- 当前 `bt_config.conf` 里 `ScanMode=0`（不可发现）。Java 不帮我们设 → native 侧在启动完成后调用
  `BTM_SetDiscoverability(BTM_GENERAL_INQUIRY | BTM_LIMITED_INQUIRY, 0)` +
  `BTM_SetConnectability(BTM_CONNECTABLE, 0)`，并做可发现超时策略（避免 24h 全程 inquiry-scan 耗电）。

### 3.5 48kHz 全链路（全局硬约束）

实测 sink 的 SBC caps（`btif/co/bta_av_co.c:84`）：

```c
const tA2D_SBC_CIE bta_av_co_sbc_sink_caps = {
    (A2D_SBC_IE_SAMP_FREQ_48 | A2D_SBC_IE_SAMP_FREQ_44),   /* ← 两个都广告，手机通常挑 44.1 */
    (MONO|STEREO|JOINT|DUAL), (BLOCKS_16|12|8|4), (SUBBAND_4|8), (ALLOC_L|S),
    A2D_SBC_IE_MAX_BITPOOL, A2D_SBC_IE_MIN_BITPOOL };
```

策略（按优先级）：
1. **P1c 单变量实验**：caps 只广告 `A2D_SBC_IE_SAMP_FREQ_48` → 逼手机协商 48k。
   风险：某些老旧 source 可能 SET_CONFIG 失败 → 需要回退路径（见 3）。
2. 解码输出即 48k（`btif_media_cb.sample_rate = btif_a2dp_get_track_frequency(sbc_cie.samp_freq)`），
   `BtifAvrcpAudioTrackCreate(trackFreq, channelType)` 直接用协商值建 AudioTrack → **无重采样**。
3. 回退：若 48-only 导致某台手机连不上，则改为"48|44 + 在 `bta_av_co_audio_sink_supports_config()`
   里优先接受 48"，并在 WORKLOG 记录哪台设备走了回退。
4. P2/P3 的 AAC/LDAC caps 同样按"48k 优先、必要时 48k-only"设定；
   **任何重采样都视为违反全局约束**，需要显式评审。
5. 验证手段：`audio_config_cb` 打印 `sample_rate`，并在 `btif_media_task_aa_handle_decoder_reset`
   的 TRACE 里核对；同时读 AudioTrack 的实际采样率（P1 验收项）。

### 3.6 P1 已知风险

| 风险 | 说明 | 缓解 |
|---|---|---|
| AVRCP CT/TG 在 r36 的 `btif_rc.c` 里偏 source 视角 | 音量（Absolute Volume）作为 TG 的处理路径需核对 | P1 分两步：先出 SBC 声（R1 前半），再补 AVRCP 控制（R1 后半） |
| source 与 sink 共用一个 `btif_av_cb` 状态机 | 同时注册两个 SDP 记录会让手机看到"既能发也能收" | P1b：native 侧收敛/移除 source 注册（`btif_av_execute_service` 门控），单变量推进 |
| 改 caps 后 Windows/手机缓存旧偏好 | 已入册（docs/03 §7） | 删除重配对 + bthserv 重启 + PnP Disable/Enable，2–3 轮 |
| `AudioTrack` 走 libmedia，MTK 版结构体布局 | 已确认 ABI 兼容（max 成员写偏移 656 / r36 sizeof 664） | 不再重复验证；热路径零新增分配 + canary |

### 3.7 P1a / P1b 实施结果（已刷入实测，2026-11-04）

上面 §3.1–§3.4 的方案已按"读码 → 实现 → 构建 → 原子刷入 → HCI 层取证"的顺序落地，
实测确认了三条**硬约束**（写代码前必须先知道，否则会踩坑）：

1. **r36 的 `btif_av` 只有一个 AV 槽位**：`btif_av_init(service_id)` 以 `sm_handle == NULL`
   为门槛，只有第一次调用才 `btif_enable_service()`。→ 我们必须在 Java 的 `init_src`
   之前跑，否则 SINK 永远不会被 enable。选定的钩子点是 **`btif_init_ok()`**
   （`btif_core.c`，由 `stack/btu/btu_task.c:193` 投递，运行在 btif task，早于
   `BTA_EnableBluetooth`，因而也早于 Java 收到 STATE_ON 后的 profile init）。
2. **`BTA_AvEnable` 全生命周期只能调一次**：`bta_av_api_enable()` 第一件事就是
   `memset(&bta_av_cb, 0, sizeof(tBTA_AV_CB))`。→ 我们**不直接**调
   `btif_av_sink_execute_service()`，而是把 `BTA_A2DP_SINK_SERVICE_ID` 置入
   `btif_enabled_services`，由 `btif_dm.c:1712` 的 `BTA_DM_ENABLE_EVT` service 循环
   恰好一次地执行它。source 与 sink 天然不可并存（对纯接收器正好）。
3. **Java 会主动把我们降级**：适配器 ON 后约 5s，上层下发
   `BT_PROPERTY_ADAPTER_SCAN_MODE = CONNECTABLE`（实测 `disc=0x0 conn=0x1`）。
   → 必须在 `btif_set_adapter_property()` 里拦截（`u2_sink_policy_force_scan_mode`），
   否则音箱扫不到。

实测取证（无需第二台蓝牙设备，靠 btsnoop 落盘 + `tools/dev/parse_snoop.py`）：

```
Write_Class_of_Device  bytes(LE)=14 04 24 → COD=0x240414 Audio/Video · Loudspeaker · Rendering+Audio
Write_Scan_Enable      0x03               → inquiry + page scan（可发现 AND 可连接）
```

运行日志（tag `u2_sink_policy`，注意本机 `log.tag=E`，需先 `setprop log.tag.u2_sink_policy V`）：

```
I install: enter (installed=0, stack_running=0)
I init_sink() -> 0
I A2DP SINK service 已置位 (service_id=18)，等待 DM enable 循环执行
I audio focus 预授予 GRANTED（native AudioTrack 通路）
I adapter ON：设为 GENERAL_DISCOVERABLE + CONNECTABLE（接收器常驻可发现）
W 覆盖上层 scan mode 请求 disc=0x0 conn=0x1 → GENERAL_DISC + CONN
```

STATE_ON、`/data/tombstones` total 0、`/proc/<pid>/maps` 里是我们的 .so（无 `(deleted)`）。
符号审计仍是 229 UND / 0 未解析，DT_NEEDED 未变（没有引入新的运行时依赖）。

**尚未验证的部分**（需要第二台设备，属 P1c 验收）：手机扫描到"U2"并显示为音频设备、
配对、A2DP 连接、SBC 出声、协商采样率。

### 3.8 P1c 实施结果（已刷入，待真机确认）

§3.5 的策略 1 已落地：`bta_av_co_sbc_sink_caps.samp_freq` 在 `U2_SBC_SINK_48K_ONLY == TRUE` 时
只广告 `A2D_SBC_IE_SAMP_FREQ_48`，source 侧 caps 不动。字节级取证见 WORKLOG 同日条目：

```
bta_av_co_sbc_sink_caps @0x1a75ee : 10 0f f0 0c 03 fa 02   （samp_freq 0x10 = 48k only）
bta_av_co_sbc_caps(SRC) @0x1a75e7 : 20 01 10 04 01 35 02   （未动）
```

回退路径保留（策略 3）：把 `U2_SBC_SINK_48K_ONLY` 置 `FALSE` 即回到 `48|44`；
若某台 source 在 SET_CONFIG 阶段被拒（连不上），先按此回退并在 WORKLOG 记录设备型号。
**验证前必须两端"忘记设备 + 重新配对"**（codec 偏好有缓存）。

### 3.9 AVRCP：两个方向、两套实现（P1d / P1e）

用户澄清了使用场景后，AVRCP 要分成**两条独立的链路**，方向相反：

**(A) 我们作为 TG（Target）—— 修 Windows 音量（P1d）**

远端（手机/PC）是 CT，通过 AVRCP 1.4 绝对音量控制**我们**的输出电平。
r36 在 sink 注册时已经把 TG 记录声明为 AVRC 1.4
（`bta/av/bta_av_main.c:557-566`，注释原文 "Initialize AVRCP1.4 to provide Absolute Volume control"），
所以对端会来发 `RegisterNotification(VOLUME_CHANGED)` / `SetAbsoluteVolume`。

r36 的问题分两层，**真正的丢弃点在 btif 派发器**（详见 WORKLOG 同日"P1d 根因升级"）：

- **BTA 层是通的**：`bta_av_proc_meta_cmd()` 就地应答 `GetCapabilities`，
  内容取自 `p_bta_av_cfg`；因为 P1a 注册的是 SINK，`bta_av_main.c:506` 选中
  `bta_avk_cfg`（注释原文 *"This configuration to be used when we are Sink + CT + TG(only for abs vol)"*），
  其 `p_meta_evt_ids = {AVRC_EVT_VOLUME_CHANGE}`、`num_evt_ids = 1`、
  `num_co_ids = 2`、`co_id = 0x1958(AVRC_CO_METADATA)`（已在**产物二进制**里 dump 核对）。
  `RegisterNotification(VOLUME_CHANGED)` 也能通过 `bta_av_chk_notif_evt_id()` 校验并上报 btif。
- **btif 层把它丢了**：`btif_rc.c:1069-1116` 的 `BTA_AV_META_MSG_EVT` 三分支派发
  ```c
  if (bt_rc_callbacks != NULL)                     → handle_rc_metamsg_cmd()     /* Java AvrcpTarget */
  else if (bt_rc_ctrl_callbacks != NULL)           → handle_avk_rc_metamsg_cmd() /* Sink+CT+TG(absvol) */
  else                                             → "Neither CTRL, nor TG is up, drop meta commands"
  ```
  我们两个回调**都是 NULL** → 命中第三支，命令**连 `AVRC_Ctrl_ParsCommand()` 都没执行就被丢弃**，
  一个字节都不回。（早先记录的"`handle_avk_rc_metamsg_cmd` 转发给 NULL 的
  `bt_rc_ctrl_callbacks`"只描述了第二支，而第二支我们根本进不去。）

可直接复用的现成 TG 应答函数（r36 里就有，本来是给 Java 调的）：
`set_volume_rsp(bd_addr, abs_vol, label)` → ACCEPT；
`volume_change_notification_rsp(bd_addr, rsp_type, abs_vol, label)` → INTERIM/CHANGED。
两者的 `CHECK_RC_CONNECTED` 门禁我们能满足：`handle_rc_connect()` 设置
`rc_connected/rc_handle/rc_vol_label/rc_volume` **不受回调 NULL 影响**。

实测表现完全对得上：
- Android 拿不到 interim response → 认为绝对音量不可用 → **回退软件音量**（编码前缩放）→ 正常
- Windows 不做软件缩放、只认绝对音量 → **滑块无效**，仅 0 值时静音

P1d 修复（纯 native，不引入 Java 服务）：
0. 在派发器第三支（`Neither CTRL, nor TG is up`）里改为先交给
   `u2_sink_policy_rc_metamsg_cmd()`：`AVRC_Ctrl_ParsCommand()` 解出 pdu 后分派；
1. `RegisterNotification(VOLUME_CHANGED)` → 立刻回 **interim**（当前增益映射到 0–127），
   记 label 并置 `rc_notif[AVRC_EVT_VOLUME_CHANGE-1].bNotify = TRUE`，**不释放事务**；
2. `SetAbsoluteVolume(vol)` → `btif_a2dp_set_audio_track_gain((float)vol / 127.0f)` + 回 accept；
3. 本地音量变化（P1e 的音量键）→ 主动对所有已注册 VOLUME_CHANGED 的对端发 **changed**，
   保持手机 UI 与实机音量同步（否则用户以为手机能控、实际不能）。
4. 状态量集中放在 `src/u2/u2_sink_policy.c` 的独立结构里（热路径零新增分配），
   避免与 vendor 二进制共享的结构体布局发生任何变化。

**(B) 我们作为 CT（Controller）—— 线控控制手机播放（P1e）**

场景：**线控（耳机控制器）插在 U2 的 3.5mm/USB 上**，按键要控制**手机/PC**的播放。
即 U2 = CT，对端手机 = TG。r36 native 侧已有 CT 接口：

- `include/hardware/bluetooth.h` 的 `bt_rc_ctrl_interface.pass_through_cmd / groupnavigation_cmd`
- 实现见 `btif/src/btif_rc.c: btif_rc_passthrough_cmd()`（`BTIF_RC_FUNC` 家族）

**✅ 输入源已实测确认**（`scripts/device/p1e_probe_input.sh` + `p1f_probe_audio.sh`，2026-11-04）：

```
/dev/input/event0  name="ACCDET"   sysfs=/devices/virtual/input/input0
   B: EV=3 (SYN|KEY)   B: KEY=40 0 0 0 0 0 0 1000000000 c000000000000 0
   getevent -p → KEY (0001): 0072  0073  00a4  0246
       0x0072=114 KEY_VOLUMEDOWN     0x0073=115 KEY_VOLUMEUP
       0x00a4=164 KEY_PLAYPAUSE      0x0246=582 KEY_VOICECOMMAND
/sys/class/switch/h2w/state = 0   （0=无配件, 1=带麦耳机, 2=无麦耳机；插线控时会变）
```

即 **MTK 的 ACCDET（accessory detect）驱动就是耳机线控的输入设备**，
且它只上报这 4 个 code —— 没有 `KEY_NEXTSONG`/`KEY_PREVIOUSSONG`/`KEY_HEADSETHOOK`。
所以 P1e 的映射必须基于"单击/长按/连击"的**时序判定**（MTK accdet 把线控按键
翻译成 VOLUMEDOWN/VOLUMEUP/PLAYPAUSE/VOICECOMMAND 四类事件，具体时序要实测抓
`getevent` 原始流后再定），不能指望直接拿到 NEXT/PREV。

要做的事：
1. **抓线控原始事件流**：插入线控后跑 `getevent -lt /dev/input/event0` 与
   `cat /sys/class/switch/h2w/state`，记录"单击/双击/长按/音量+"分别产生什么事件序列
   —— **这是 P1e 的第一步，也是映射表的唯一依据**。
2. **连接前提**：CT 发 passthrough 需要一条已建立的 AVRCP 连接与对端 `rc_features`。
   我们做 A2DP sink 时，AVRCP 通道由对端（TG 侧的手机）发起或我们主动 `bta_av_rc_open`
   —— 需要在 P1e 里确认 sink 场景下 CT 方向的通道是否会建立
   （r36 的 `btif_rc_cb[].rc_features`/`peer_features` 只在 `bta_av_rc_opened` 时填充）。
   若不建立，则要在 sink 注册后主动开一条 AVCTP 连接（`BTA_AvOpen` 的 rc 参数 / `bta_av_rc_open`）。
3. **映射**：`KEY_PLAYPAUSE`→`AVRC_ID_PLAY`(0x44)/`PAUSE`(0x46) 切换（本地记播放态）、
   `KEY_NEXTSONG`→`FORWARD`(0x4B)、`KEY_PREVIOUSSONG`→`BACKWARD`(0x4C)、
   `KEY_STOPCD`→`STOP`(0x45)、`KEY_HEADSETHOOK`→PLAY/PAUSE。
   音量键 → **不发** passthrough，改为本地调 AudioTrack 增益 + 按 (A)-3 回发 changed 通知。
4. 线程：按键读取必须在**独立的低优先级线程**里做（不能占 btif/btu task），
   通过 `btif_transfer_context` 或直接调 `bt_rc_ctrl_interface` 投递。

### 3.10 P1f：HFP HF（真麦克风 + 通话音频）—— 可行性分层

用户要求的是**真 HFP**，不是换图标。结构上分两层，难度差别极大：

**信令层（可行，机制与 A2DP sink 完全相同）**
r36 自带 HF client 角色：`btif/src/btif_hf_client.c` + `bta/hf_client/*`
（SLC 建立、AT 命令解析 `bta_hf_client_at.c`、来电/去电/接听/挂断、codec 协商）。
启用方式就是我们已验证过的那条路：把 `BTA_HFP_HS_SERVICE_ID`（= **24**，
`bta/include/bta_api.h:82`）置入 `btif_enabled_services`，
`btif_dm.c:1713` 的 service 循环会调 `btif_in_execute_service_request(24, TRUE)`
→ `btif_dm.c:338 btif_hf_client_execute_service(TRUE)`。
注意 APK 现在启用的 `BTA_HFP_SERVICE_ID`(=6) 是 **AG（网关/手机）角色**，
与 24（HF/耳机角色）**不同**，两者不会互相顶掉，但 SDP 记录会同时存在 → 手机可能把我们当手机。
COD 届时改为 headset 类（提案 `0x2C0404` = Audio/Video · Wearable Headset ·
Rendering+Capturing+Audio），并把 APK 启用的 AG 侧服务在 native 收敛掉（同 P1b 对 source 的处理）。

**音频层（真正的难点，需先做可行性实验）**
`bta_hf_client_sco.c` 调 `BTM_CreateSco(p_bd_addr, is_orig, packet_types, &sco_idx, conn_cb, disc_cb)`
—— **没有 route 参数**，即 r36 假定 SCO 语音由**控制器/平台**走 PCM 接口直连音频编解码器，
native 只上报 `BTA_HF_CLIENT_AUDIO_OPEN_EVT / MSBC_OPEN_EVT`
（`btif_hf_client.c:888-897` → `audio_state_cb` → Java `HfpClientService` → `AudioManager` 切通路）。
我们没有 Java 服务，所以必须二选一：

| 方案 | 做法 | 风险 |
|---|---|---|
| **F1 平台 PCM 路由** | 依赖 MTK 音频 HAL 把 SCO PCM 接进 voice/call 通路 | 完全取决于 vendor 驱动，BT .so 里改不动；U2 不是手机，通话通路可能根本没接线 |
| **F2 透明 SCO（自解码）** | 让控制器把 SCO 数据走 HCI，native 解 CVSD(8k)/mSBC(16k) → AudioTrack；上行从 AudioRecord/麦克风采集后编码 | 需要控制器支持透明 SCO（要实测 `Read_Local_Supported_Features` / MTK vendor HCI 命令）；mSBC 解码要引入实现（同 R2/R3 的做法：全源码静态链入，不用 blob） |

**P1f 的第一步不是写代码，是做 F1/F2 判定实验**（低成本）：
在 native 侧建 SLC + 手动 `BTM_CreateSco`，然后
① 抓 HCI 看 SCO 是否以 HCI 包形式上来（→ F2 可行）；
② 同时在手机上播通话，听 U2 扬声器是否有任何声音（→ F1 可行）。
两者都不通则真 HFP 语音在 U2 上不可达，必须回头和用户重新确认身份范围
（届时可退化为"只做 COD/SDP 层面的耳机身份 + A2DP"，但那**不是**用户要的，需明确告知）。

**✅ 平台侧证据已采集**（`p1e_probe_input.sh` / `p1f_probe_audio.sh`，2026-11-04），
结论是 **F1 有戏，但不能只靠 BT .so**：

1. 声卡 `mt-snd-card` 里存在 **BT SCO 专用 PCM DAI**：
   ```
   00-10 Voice_MD1_BT_Playback   （modem 通话走 BT）
   00-11 VOIP_Call_BT_Playback   （VoIP 下行 → BT）
   00-12 VOIP_Call_BT_Capture    （BT 上行 → 采集）
   00-02 Voice_MD1_PLayback / 00-17 Voice_MD2_PLayback
   00-09 DL1_AWB_Record / 00-16 I2S0AWB_Capture   （AWB = 16k 宽带，对应 mSBC）
   00-04 ULDL_Loopback           （上下行环回，调试用）
   ```
   → 说明 BT 芯片与音频 DSP 之间有 **PCM 硬连线**，MTK 的 SCO 是**平台路由**，
   这正是 r36 `BTM_CreateSco()` 不带 route 参数的原因（见上）。**F1 是 vendor 的正道。**
2. `/vendor/etc/audio_policy.conf`（primary 模块）：
   - output `primary`：`sampling_rates 44100|48000`，devices 含 **`AUDIO_DEVICE_OUT_ALL_SCO`**
     → **我们的 AudioTrack 48k 有 HAL 支持**（48k 硬约束在输出侧成立，不会被重采样到 44.1）
   - input `primary`：`8000|16000|32000|44100|48000`，devices 含 **`AUDIO_DEVICE_IN_ALL_SCO`**、
     `AUDIO_DEVICE_IN_COMMUNICATION`、`AUDIO_DEVICE_IN_VOICE_CALL`，
     channel_masks 含 `VOICE_UPLINK|VOICE_DNLINK` → **麦克风采集设备在 HAL 层是存在的**（R1′ 真麦克风有物理基础）
   - a2dp 模块还声明了 input `AUDIO_DEVICE_IN_BLUETOOTH_A2DP`（44100 stereo）
     → 印证 P0 的发现：MTK 固件本来就带 sink 支持，只是 Java 层没启用
   - `attached_input_devices` 里**没有** SCO → SCO 输入需要运行时 connect，
     即调 `AudioSystem::setDeviceConnectionState(AUDIO_DEVICE_IN_ALL_SCO, AVAILABLE, …)`
     （这正是 Java `AudioManager.startBluetoothSco()` 底层做的事，我们可以在 native 直接做）
3. `tinymix` 不存在（exit 127）→ 不能用命令行改 mixer，只能走 audio HAL / AudioSystem API。
4. `/sys/class/switch/h2w/state` 可读 → 能感知有线耳机（线控）插拔，P1e 用它做使能门禁。

**由此得到 P1f 的推荐路径**：先做 **F1'（AudioSystem 通路声明 + 平台 PCM 路由）**：
`BTA_HFP_HS_SERVICE_ID` 建 SLC → SCO 建立时 native 调
`AudioSystem::setDeviceConnectionState(OUT/IN_ALL_SCO)` + `AudioSystem::setParameters("screen_state=on")` 之类，
再看 MTK HAL 是否把 PCM 通路接上；**同时抓 HCI 确认 SCO 数据没有走 HCI**（若走了，F2 也可选）。
F2（自解码 CVSD/mSBC）作为备选保留：需要控制器支持透明 SCO，MTK 平台通常不支持，
但 `bta_hf_client_sco.c` 的 codec 协商（`BTM_SCO_CODEC_MSBC`）在两条路下都用得上。

**与 48k 约束的关系**：SCO 语音固定 8k(CVSD)/16k(mSBC)，与 A2DP 的 48k 音乐链路是**两条独立通路**，
48k 硬约束只适用于音乐链路；但必须显式设计"通话时暂停/降低 A2DP 流"的切换（`bta_sys_sco_use` 已有钩子）。

## 4. P2（AAC）与 P3（LDAC）：r36 缺什么

实测 `src/stack/a2dp/` 只有 **`a2d_api.c` / `a2d_int.h` / `a2d_sbc.c`** —— **没有 AAC，也没有 LDAC / vendor codec**。
`bta_av_co_audio_sink_supports_config()` 的 switch 里 **只有 `BTA_AV_CODEC_SBC`**，default 直接 `return FALSE`。

所以 P2/P3 都要自己补齐三层：

1. **SEP 注册与 caps**：新增 AAC（MPEG-2/4 AAC，AVDTP media codec id `0x02`，LATM 载荷）
   与 LDAC（vendor id `0x012D`，codec id `0xAA`）的 sink caps 数组 + `BTA_AvRegister`/SEP 表项；
   在 `bta_av_co_audio_sink_supports_config()` 的 switch 里加分支（48k 优先，见 3.5）。
2. **解码器接入**：
   - AAC → **fdk-aac**（googlesource r36 上**没有** `platform/external/fdk-aac`，需另取源码，vendored 进 `external/fdk-aac`）；
   - LDAC → **open-vela/external_libldac `dev` 分支 2.0.72，全源码**，静态链入（`libldacBT.a` 形式），
     **禁止任何闭源 blob**；48k@990kbps（quality index 优先 1000/990）。
3. **media task 分流**：在 `btif_media_task_handle_inc_media()` 里按 codec_type 分派到
   SBC / AAC / LDAC 解码器，统一输出 16bit 立体声 PCM 到同一个 AudioTrack（48k），
   复用现有 `pcmData` 缓冲以遵守"热路径零新增分配"。

## 5. 阶段定义与验收

| 阶段 | 主题 | 单一变量 / 交付 | 验收 |
|---|---|---|---|
| **P0** ✅ | 干净 AOSP 基线可构建 + 可原子刷入 + BT 起得来 | `bluetooth.default.so`（源码等价） | STATE_ON、地址/名称正常、**零 tombstone**（在机 `df19dc6a…`，重构建 `845164e3…`；已 Release `p0-baseline-build-flash-20261104`） |
| **P1a** ✅ | `USE_AUDIO_TRACK` + native 启用 SINK service | `-DUSE_AUDIO_TRACK`、`src/u2/u2_sink_policy.c`、`btif_init_ok()` 一行钩子 | 已刷入 `d73dc8c9…`：`init_sink()->0`、`SINK service 已置位(18)`、focus GRANTED；STATE_ON、零 tombstone |
| **P1b** ✅ | 扬声器身份 + 常驻可发现（顺带收敛掉 source 注册） | `BTA_DM_COD {0x24,0x04,0x14}`、`on_adapter_on()`、`force_scan_mode()` | 已刷入 `84f09a4c…`；**HCI 实证** `Write_Class_of_Device=14 04 24`(COD 0x240414 Loudspeaker)、`Write_Scan_Enable=0x03`(可发现+可连接) |
| **P1c** ✅刷入 | SBC sink caps 收敛到 48k-only | `bta_av_co_sbc_sink_caps.samp_freq` 仅 `48`（开关 `U2_SBC_SINK_48K_ONLY`） | 已刷入 `bd7bcef2…`，字节级取证 `10 0f f0 0c 03 fa 02`，STATE_ON、零 tombstone。**待真机**：重配对后 `audio_config_cb` 报 48000 + snoop 里 SET_CONFIG 为 48k |
| **P1d** | AVRCP **TG**：绝对音量（修 Windows 音量） | `handle_avk_rc_metamsg_cmd()` 的 interim/changed 应答 + `btif_a2dp_set_audio_track_gain()` | Windows 音量滑块生效；Android 仍走软件音量不出问题；两端 UI 与实机音量同步 |
| **P1e** | AVRCP **CT**：线控 → 控制手机播放 | `/dev/input/eventX` 读线控 → `bt_rc_ctrl_interface.pass_through_cmd` | 线控播放/暂停/上下曲能控制手机；音量键调本地 AudioTrack 并回发 VOLUME_CHANGED |
| **P1f** | **HFP HF**：真麦克风 + 通话音频（R1′） | `BTA_HFP_HS_SERVICE_ID`(24) 入 `btif_enabled_services` + SCO 音频通路（F1 平台 PCM / F2 透明 SCO 自解码，先做判定实验） + COD 改 headset 类 | 手机把我们当耳机；来电能在 U2 接听并双向通话；CVSD/mSBC 出声 |
| **P1** | 基线 SINK（R1 + R1′ + R1″）汇总 | P1a–P1f | Android + Windows 各连一次 SBC 48k 稳定播放；音量可控；线控可控手机；通话可用 |
| **P2** | AAC（R2） | fdk-aac vendored + SEP/caps + 解码分派 | 手机选 AAC 时出声，48k，无重采样 |
| **P3** | LDAC（R3） | libldac 2.0.72 全源码静态链入 + SEP/caps + 解码分派 | 48k@990kbps 出声；`nm` 无闭源 blob 痕迹；产物自洽 |
| **P4** | 一拖二（R4） | 双 AVDTP/多 peer 管理 + 活跃流切换策略 | 2 台同时连接、单路活跃、切换 ≤1s、24h 双连不断 |
| **P5** | 老化（R5） | 长跑脚本 + 内存/句柄监测 | 72h 零 tombstone、零 EBADF、无可闻断音 |
| **P6** | 工程化收尾（R6） | Release 打包 + 文档 + 回滚脚本 | `pN-topic-date` Release 齐全，WORKLOG 完整 |

每阶段：**一次 build-flash-test 循环只改一个变量**；刷入后立即记录 md5 + 行为；
阶段末 commit + tag + Release（**push/release 前必须征得用户确认**）。

## 6. 参考实现的使用纪律

`android12`（`/tmp/build/system_bt_a12`，只读蓝本）、bluez-alsa、PipeWire 仅作**架构参考**：
看它们如何组织 sink SEP、codec 插件、多点切换 —— 但代码**必须为 r36 重写**，不做文件级复制。
