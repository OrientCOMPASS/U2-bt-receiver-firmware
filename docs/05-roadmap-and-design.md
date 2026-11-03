# docs/05 — 架构决策与路线图

## 1. 需求 → 阶段映射

| 需求 | 内容 | 阶段 |
|---|---|---|
| R1 | 基线 SINK：以"扬声器"身份可被发现（COD `0x240414`）、SBC 在 Android 手机 + Windows 上稳定、AVRCP 播放/暂停/音量 | **P1** |
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
| **P0** ✅ | 干净 AOSP 基线可构建 + 可刷入 + BT 起得来 | `bluetooth.default.so`（源码等价） | STATE_ON、地址/名称正常、**零 tombstone**（已达成，md5 `df19dc6a…` 在机，重构建 `845164e3…`） |
| **P1** | 基线 SINK（R1） | `-DUSE_AUDIO_TRACK` → sink service 启用 → COD/可发现 → 48k caps → AVRCP | 手机显示为"扬声器"；Android + Windows 各连一次 SBC 稳定播放；48k 协商成功；播放/暂停/音量可控 |
| **P1b** | sink-only 收敛 | 移除/门控 source 注册 | 手机侧只看到 AudioSink；不影响 STATE_ON |
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
