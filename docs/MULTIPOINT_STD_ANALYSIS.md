# 多点 A2DP Sink：标准实现对比与「恢复播放永久静音」根因分析

> **状态（2026-11-03）**：分析完成；修复已落地为
> `patches/nougat/system_bt/0010-multipoint-std-transactional-switch.patch`
> （本机 standalone build：baseline 0001–0009 与叠加 0010 后均
> **443/443 TU GREEN + 链接成功**，NDK r25c；等待上机验证）。
> 配套 config 修复：`device/keenplus/u2-n/bdroid_buildcfg.h`
> `AVDT_NUM_SEPS` 8 → 12。

## 1. 症状（真机，firmware-btso-mp4-slotfix = 0008+0009）

两台手机同时连接 U2（多点 sink）：

- **方向 1**：A 播放中，B 开始播放 → B 抢断 A（0008 的 preemptive
  suspend）——**正常**；
- **方向 2**：随后让 A 恢复播放 → 设备**永久静音**（无 crash、蓝牙仍
  连接，重播/换曲无效，往往重启蓝牙才恢复）。

## 2. 标准参考实现（收集）

### 2.1 android11-release `system/bt`（A11）—— 原生层标准

`btif_a2dp_sink.cc` + `btif_av.cc`（sink 多点重构后的形态）：

| 要素 | 做法 |
|---|---|
| **事务化重建** | `btif_a2dp_sink_restart_session()`：停泵、清队列、拆 track/decoder、按新 codec 重建，作为**一个不可分割的动作**在 media 工作线程执行；任何路径都不存在「track 已拆、新 track 未建」的中间窗口 |
| **单一大锁** | `g_mutex` 覆盖全部 sink media 状态（队列、rx 状态、track、alarm）；BTU 侧 enqueue 与 media 线程的拆装互斥 |
| **同层 active peer** | `BtifAvSink::SetActivePeer` 与 `IsActivePeer()`：媒体包、start/stop、flush 全部严格以 active peer 为准，不匹配即丢弃（`FindPeerByHandle` 严格匹配，无 fallback） |
| **NULL 防护** | `TrackStart/TrackWriteData` 对 NULL track 显式返回（不依赖 assert） |

### 2.2 android10-release `packages/apps/Bluetooth`（A10）—— 策略层标准

`A2dpSinkStreamHandler`（每设备一个 handler）：

- 设备间**音频焦点仲裁**决定谁占用输出；
- 被抢占设备收到 focus loss 时，除了流层动作，**还会通过 AVRCP 向对方
  播放器发 PAUSE**（`LOSS_TRANSIENT`：先 duck/增益 0，随后延迟发
  PAUSE；重获焦点可恢复）；
- 意义：只发 AVDTP SUSPEND 不够——很多手机播放器认为自己仍在
  「播放中」，会自动重发 START，形成两台设备的**抢占总线战争**。

### 2.3 BlueALSA（独立开源实现）

`bluealsa-aplay`：**每个蓝牙 transport（每设备）独立 PCM 设备**，守护
进程可同时播放所有已连接设备（混音交给音频服务器）。说明工业界的常规
做法是**每设备媒体状态完全分离**；U2 的「单一共享解码通路」是相对标准
的偏离——偏离本身可以接受（省内存、MT6735 上简单），但代价是**切换必须
事务化**（即 A11 模型），而补丁前的实现恰恰不是。

## 3. 与 U2 现状（0001–0009）对比：缺陷清单 D1–D9

| # | 缺陷 | 位置 | 标准对照 | 后果 |
|---|------|------|----------|------|
| **D1** | 媒体路径切换 = `CLEAR_TRACK` + `CFG_UPDATE` **两条独立消息**，期间 BTU 线程持续 enqueue；队列到 5 帧触发的 `start_decoding` 可落在窗口内 | `btif_av.c switch_to_slot`、`btif_media_task.c` | A11 `restart_session` 事务 | track=NULL 时 start → crash；或 alarm/track 生命周期脱钩 → **泵永不启动 → 永久静音**（★主根因之一；MT6735 慢速 binder 使队列预积 ≥5，窗口更宽） |
| **D2** | 流事件按 hndl 路由失配时 `slot<0 → slot=0` 兜底；一条迟到/重复的 CLOSE 即**解绑健康的 slot0**（hndl 清零、cfg_valid=FALSE） | `btif_av_handle_event` | A11 严格 handle 匹配、失配即丢 | 此后一切 switch 触发 fail-safe abort；SUSPEND 时 owner==-1 → `rx_flush=TRUE` **永久** + 泵停 → 所有 enqueue 被丢（★主根因之二） |
| D3 | `stop_with_fallback` 只看 `other=(slot==0)?1:0` —— 3 槽扩展后的 2 槽残留 | 同上 | — | 第 3 台设备永远得不到回退接管 |
| D4 | SUSPEND/STOP 守卫 `owner==ctx \|\| owner<0`：owner==-1（已被 D2 毒化）时**任何**槽挂起都会 flush 活动路径 | started/closing handler | A11 `IsActivePeer` 严格判定 | 静音被固化、无法自愈 |
| D5 | `bta_av_stream_data_cback` 扫描 `p_scb[0..3]` 无 NULL 判断（`BTA_AV_NUM_STRS=4` > 实际分配数） | `bta_av_aact.c` | — | 失配媒体包 → NULL 解引用崩溃（0004 扩流后可达） |
| D6 | `bdroid_buildcfg.h` `AVDT_NUM_SEPS=8` 覆盖 `bt_target.h` 的 12；而每次 `BTA_AvRegister`（每槽一次，共 3 次）各创建 4 个本地 SEP（`bta_av_register → AVDT_CreateStream` 循环） | 设备 config | — | 第 3 个注册实例的 SEID 9+（含 LDAC sink）创建失败 → 第 3 台设备无法协商完整 codec |
| D7 | 媒体 accept gate：owner<0 时兜底「任一槽 OPENED/STARTED 即收」 | `bte_av_media_callback` | A11 严格 `IsActivePeer` | 非 owner 的包灌进错误 codec 的共享解码器 → 噪声/静音 |
| **D8** | 抢占只发 AVDTP SUSPEND（0008），**不发 AVRCP PAUSE** | 抢占路径 | A10 focus-loss → PAUSE；商用多点接收器同 | 被抢占手机播放器仍自认 playing → 可能自动 re-START → 两台设备**乒乓切换**，每轮都重新打开 D1 窗口；也解释方向 1/方向 2 的不对称（不同手机对 SUSPEND 的反应不同） |
| D9 | `BTIF_AV_DISCONNECT_REQ_EVT` 等用 `btif_av_cb.bta_handle`（**注册** hndl）调 `BTA_AvClose/OpenRc/CloseRc`；多点下实际流的 scb hndl ≠ 注册 hndl（0009 现场日志已证实） | 各 state handler | A11 严格 handle | Java 侧断开/RC 打到错误（常为空闲）scb → 断开失效、AVRCP 失效——用户连「断开重连自救」这条路都被堵死 |

## 4. 「方向 2 静音」的机理推演

B 抢占 A 后，A 恢复播放时存在两条互相叠加的致死路径：

1. **D8 抢占战 × D1 竞态**：B 的播放器被 SUSPEND 后自动 re-START →
   BTA 再次抢占 → A/B 乒乓。每轮切换都是
   `CLEAR_TRACK`→(窗口)→`CFG_UPDATE` 两消息序列；窗口内 BTU 线程
   enqueue 到 5 帧即 `start_decoding`：轻则泵与 track 生命周期脱钩
   （新 track 建好后**永远没人 start 它**），重则 `TrackStart(NULL)`
   crash。静音即「泵死」的稳定态：连接正常、包照收（或被 flush）、
   无声输出。
2. **D2 毒化 × D4 固化**：切换混战期间产生的失配流事件（典型：旧
   hndl 的迟到 CLOSE）落入 `slot=0` 兜底 → 解绑 slot0 →
   `cfg_valid=FALSE` → A 的 START 触发 switch 时 fail-safe abort →
   owner 与实际流不一致；随后任一 SUSPEND 在 owner==-1 的洞里把
   `rx_flush` 置 TRUE 且**再无人清除** → 所有入队被丢弃 → 永久静音。

D7 让错误路径的包进一步污染解码器，D9 让「断开重连」自救失效——
四者共同构成实测到的「彻底哑掉、只能重启蓝牙」现象。

> 说明：单靠静态分析无法指认**唯一一行**必现代码（竞态时序依赖真机
> 调度），故按既定决策修复**整类缺陷**：与标准实现对齐做结构化加固。

## 5. 修复（补丁 0010）与标准要素的对应

| 标准要素 | 0010 落地 |
|---|---|
| A11 事务化 `restart_session` | 新增 `BTIF_MEDIA_AUDIO_SINK_SWITCH` 事件 + `btif_media_task_sink_switch_req(codec_info)`：停泵+清队列+拆 track/decoder+按新 cfg 重建 = **单条媒体线程消息**；`switch_to_slot`、SINK_CFG 内联路径、START 抢占路由全部改发这一条 |
| A11 `g_mutex` | `sink_media_lock`（递归 pthread mutex）覆盖 enqueue（含 5 帧 delayed-start）、rx_flush、start/stop decoding、clear_track、decoder_reset、switch、rx_flush handler；BTU 线程与媒体线程不可能再交错看到半拆状态 |
| A11 active-peer 严格化 | 流事件 hndl 失配 → **丢弃+日志**（绝不 slot0 兜底）；accept gate 仅认 owner 槽；SUSPEND/STOP 守卫严格 `owner==ctx`；`stop_with_fallback` 扫描全部 3 槽 |
| A11 NULL 防护 | `start_decoding` track==NULL 拒绝起泵；`btif_sink_write_audio`、`BtifAvrcpAudioTrackStart/WriteData` NULL 安全（替换 release 版无效的 assert） |
| A10 抢占策略 | 被抢占槽收到本地发起的 SUSPEND 时（`initiator==TRUE && owner!=ctx`）向**被抢占方自己的 RC 通道**补发 AVRCP PASS THROUGH PAUSE（press+release，独立 label）→ 其播放器真正暂停，杜绝 re-START 抢占战 |
| 所有权时序 | START 事件在**状态机分发之前**（路由器内）完成 owner 认领 + `rx_flush=TRUE` + 投递 SWITCH；STARTED 入口的 switch 因此成为 no-op；`set_rx_flush(FALSE)` 在锁上阻塞至重建完成——新 track 就绪前不可能有包入队 |
| D9 | `BTA_AvClose/OpenRc/CloseRc` 改用运行时 scb hndl（`g_slot_hndl[slot]`，缺省回落注册 hndl） |
| D5/D6 | BTA 扫描 NULL scb 跳过；`AVDT_NUM_SEPS` 12（config 侧） |
| BlueALSA 启示 | 共享单通路方案的固有代价已由「事务化切换」补齐；若未来要三台**同时出声**，需走每设备独立 PCM/track 的架构（超出本次范围） |

**遗留流程保全**（防回归设计）：

- 首台设备（单机 legacy 流）：`SINK_CFG` 内联认领 owner + 投递同一条
  SWITCH；START 路由见 `owner==slot` 不再重发；enqueue 到 5 帧起泵
  ——行为与 0009 前一致，但已无竞态；
- 0007/0008/0009 的 BTA 层抢占、48k 全链、运行时 hndl 路由全部保留。

## 6. 验证

| 项 | 结果 |
|---|---|
| standalone build（`scripts/bt_standalone_build.sh`，NDK r25c） | baseline 0001–0009：**443/443 GREEN**；`git am` 0010 后：**443/443 GREEN**，`bluetooth.default.so`/`audio.a2dp.default.so` 链接成功，sentinel 20/20 |
| CI（`bt70.yml`，同一脚本） | 预期同绿（待推送验证） |
| 上机清单（待测） | ① A/B 互相抢占+恢复 ×20 轮无静音；② B 抢占后 A 恢复出声（本次主诉）；③ 被抢占手机通知栏/播放器状态变为**暂停**（D8 生效证据）；④ Java 设置页断开任一台成功（D9）；⑤ 三台设备同时连接，第 3 台可协商 AAC/LDAC（D6，需重刷含新 bdroid_buildcfg 的固件）；⑥ logcat 关键字：`transactional switch` / `matches no slot - dropped` / `AVRCP PAUSE -> preempted` |

## 附：已知但不在 0010 范围

- `AVDT_RECONFIG_IND` 在 BTA 层因 map entry 0 被丢弃（reconfigure 请求
  无法到达应用层）——协议层独立缺陷，需单独补丁，不与本次静音修复
  混装。
