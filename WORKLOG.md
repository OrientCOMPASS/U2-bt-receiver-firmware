# WORKLOG — U2 蓝牙音频接收器固件（MT6735 · Android 7.0 · Fluoride r36）

> 本文件是工程的"当日流水账 + 事实账本"。规则：**当日更新，新坑即时入册**。
> 结论性/可复用的内容沉淀到 `docs/`，本文件保留时间线与实测原始数据。
> 时区：Asia/Shanghai。⚠️ 设备系统时钟错乱（见 docs/04-pitfalls.md），设备侧时间戳一律不可信。

---

## 2026-11-04 · P0 通道与基线

### 0. 会话与凭据
- 沙箱工作区：`bridge_client.py`（用户上传）+ `rc.sh`（本会话新写的远控封装）+ `CREDENTIALS.txt`
  （GitHub PAT / 桥接配对串）。**凭据文件在工作区根目录，不在仓库内，永不提交。**
- 桥接链路实测：沙箱 → `broker.emqx.io:1883` → Windows `Administrator@DeveloperOS`
  (Windows NT 10.0.28000, PowerShell) → `adb 1.0.41 / 37.0.1` → 设备 `0123456789ABCDEF (U2)`。
  握手 ~2s，RTT 550–1500ms。`--jitter 0 --decoy 0` 快速模式（rc.sh 默认）。
- GitHub PAT 验证通过（`git ls-remote` 可读写 `OrientCOMPASS/U2-bt-receiver-firmware`）。
  仓库现状：`main` 分支仅 LICENSE（commit `746dba4 Reset repository: LICENSE only (fresh start)`），
  另有 20+ 个历史 tag/Release —— **按任务书要求：不下载、不参考、不翻旧历史。**

### 1. 设备指纹（P0 退出条件之一）
| 项 | 值 |
|---|---|
| ro.build.fingerprint | `alps/full_tek6735m_65_d_n/tek6735m_65_d_n:7.0/NRD90M/1760605825:user/release-keys` |
| ro.build.display.id | `B04_35_1.5_8_3inch_M304_ST7701SN_20251016` |
| release / sdk | 7.0 / 24 |
| model / device | U2 / U2 |
| ro.hardware / ro.board.platform | mt6735 / mt6735m |
| kernel | `Linux localhost 3.18.35 #1 SMP PREEMPT Thu Oct 16 17:30:28 CST 2025 aarch64` |
| build type / keys | user / release-keys（**adb root 无效**：`service.adb.root=1` 但 shell 仍 uid=2000） |
| ro.debuggable / ro.secure | 1 / 0 |
| ro.boot.verifiedbootstate / flash.locked | green / 1 |
| SELinux | enforcing，但 **shell 域 permissive**（AVC 里 `permissive=1`） |
| 蓝牙 BD_ADDR | `00:00:46:03:35:01`（/data/misc/bluedroid/bt_config.conf） |
| 蓝牙名 | `U2` |
| /system | `/dev/block/mmcblk0p20` ext4 ro → **`mount -o rw,remount /system` 实测可用**（无 verity 阻塞） |
| BT 固件相关 prop | `bluetooth.fw.adv.num=40`、`bluetooth.fw.energyreport=true`、`bluetooth.fw.irk.num=40`、`bluetooth.fw.rpa=true`、`bluetooth.fw.scanfilter.num=40`、`bluetooth.fw.scanresult.size=8160` |

### 2. root 通道（实测协议 —— 与任务书描述**有一处关键差异**）
任务书写的是"脚本写入 `/data/local/tmp/cmd.txt`"。**本机实测不是 cmd.txt**：

- init banner：`repo-rootexec-v6-propexec`（注意是 **v6** 字样，用户描述的是 debug V7 boot）
- `init.svc.repoexec` 存在（= service 已在 ramdisk init 里定义）；`repo.exec` 属性**没有**
  property_contexts 条目 → 落到 `default_prop`，靠 shell 域 permissive 才允许 set（AVC denied 但放行）
- 触发后 init 日志：`Starting service 'repoexec'...`
- **失败态**：写 `cmd.txt` 时 → `Service 'repoexec' (pid N) exited with status 127`
  （逐个试了 cmd.txt / cmd.sh / repoexec.sh / root.sh / exec.sh / rootexec 六个候选名 + chmod 777）
- **成功态**：脚本名必须是 **`/data/local/tmp/rootexec.sh`**，且必须可执行：
  ```
  adb push <script> /data/local/tmp/rootexec.sh
  adb shell chmod 777 /data/local/tmp/rootexec.sh
  adb shell setprop repo.exec 0 ; sleep 2 ; adb shell setprop repo.exec 1
  ```
  → 产出 marker 文件 owner `root:root`，init 报 `exited with status 0`
- executor 身份：`uid=0(root) gid=0(root) groups=0(root) context=u:r:shell:s0`（root + permissive shell 域）
- 本机 executor 是"极简版"：**没有**任务书里说的自动重建 root_dmesg/root_logcat/root_ps/root_props/
  snoop_ls/rootexec.log；输出文件、权限、清理全部由脚本自己负责。
- ramdisk 里另有非标准文件：`/multi_init.rc`、`/sbin/multi_init`(1250664B)、空目录 `/root`(0700)；
  `/init.rc` 与 `/multi_init.rc` 里 grep 不到 `repoexec` → service 定义是**编进 init 二进制**的。

工具化：`tools/dev/root_run.sh <script.sh> [wait_sec]`
- 把脚本 base64 → Windows 落盘 → adb push → chmod 777 → 0/1 触发 → 等待 → adb pull 输出 → UTF-8 打印
- 自动注入 `O=/data/local/tmp/root_out_<唯一名>.txt`（脚本内直接用 `$O`），避免读到上一轮陈旧输出
- 输出 >56000 字符时本地做 head40000+tail12000 裁剪（桥接单轮上限 64KB）

### 3. 原厂件基线与备份（P0 退出条件之一）
| 文件 | 大小 | md5 | 备份 |
|---|---|---|---|
| `/system/lib/hw/bluetooth.default.so` | 1735356 | `e0dd373e4632316a0348e6ec0a288347` | `/data/local/tmp/bt_stock_backup.so` ✅ |
| `/system/app/Bluetooth/Bluetooth.apk` | 619805 | `7cbc91fa8c0bca8bdb750e08fba08a54` | `/data/local/tmp/Bluetooth_stock.apk` ✅ |

原厂 `.so` 逆向出的关键事实（决定我们的构建配方）：
- ELF32 ARM，`Flags: 0x5000200 = Version5 EABI, soft-float ABI`（softfp）
- `DT_NEEDED`：libcutils, libdl, liblog, libz, libpower, libprotobuf-cpp-full, libmedia, libutils,
  libchrome, **libcustom_prop**(MTK), **libstagefright**(MTK), libc++, libc, libm；`SONAME=bluetooth.default.so`
- **原厂已开 A2DP SINK**：UND 里有整套 `android::AudioTrack`
  （构造函数 + start/stop/pause/flush/write/setVolume）→ `btif_avrcp_audio_track.cpp` 被编入
  → 说明 MTK 构建里 `BTA_AV_SINK_INCLUDED=TRUE`（AOSP r36 默认 FALSE）
- 原厂静态链入 fdk-aac（**编码器** FDKaacEnc_*/SBR 一大批符号）与 tinyxml2（165 个符号）
- 导出 808 个动态符号（原厂未用 `-fvisibility=hidden`）；`HMI`（= `HAL_MODULE_INFO_SYM`）导出 ✅
- 0 个 `_ZTI*`/`__cxa_*` → 原厂 C++ 为 **-fno-rtti -fno-exceptions**
- `libbluetooth_jni.so` **不**链接 bluetooth.default.so（它的 NEEDED 里没有），
  只通过 libhardware 的 `hw_get_module` + `dlsym("HMI")` 拿 `bt_interface_t`
  → **我们只要导出 HMI，Java/JNI 侧就完全兼容**（已验证：JNI 从我们 .so 需要的符号数 = 0）
- MTK vendor HAL：`/vendor/lib/libbt-vendor.so`(13844B) + `libbluetooth_mtk.so`(30268B) +
  `libbluetoothem_mtk.so` / `libbluetooth_relayer.so`；我们的 .so 通过 `hci/src/vendor.c` 的
  `dlopen("libbt-vendor.so")` 加载它，`hw_get_module` 由静态编入的 `hardware.c` 提供
  （原厂 DT_NEEDED 里也没有 libhardware.so → 同样是静态编入，我们照做）

### 4. 构建体系（全新自研，不用 AOSP soong/make）
详见 `docs/02-build-system.md`。要点：
- 源码 vendored 进仓库：`src/` = AOSP `platform/system/bt @ android-7.0.0_r36`
  (commit `cc4b5efe1ce96967948eb66889bcda6d174b61da`)，`external/tinyxml2` 同 tag
- `tools/build/mk2plan.py`：mini-make 解析器，把 Android.mk 的模块定义（源文件清单/include/flags）
  抽成 `bld/plan.json` → **保证与官方构建"源码等价"，不靠人工列文件**
- `tools/build/build_bt.py`：NDK r21e clang `--target=armv7a-none-linux-androideabi24`，
  `-march=armv7-a -mfloat-abi=softfp -mfpu=neon -mthumb`（对齐原厂 soft-float ABI + Thumb-2），
  `-fno-exceptions -fno-rtti -fvisibility=hidden`，调试期 `-O0 -g`
- C++ 标准库：AOSP `external/libcxx @ r36` 头 + 设备 `/system/lib/libc++.so`
  （设备 libc++ 是 `std::__1`/`St3__1` ABI，**NDK 的 `__ndk1` ABI 不能用**）
- 链接对象：从设备 pull 下来的真实 `.so`（`devlibs/`，50 个）→ DT_NEEDED 与符号集与目标机一致
- protobuf：设备 `libprotobuf-cpp-full.so` 实测 `GOOGLE_PROTOBUF_VERSION=2006002`（2.6.x，
  从 `VerifyVersion` 反汇编 `movw #39922 / movt #30` 得出），因此用 **protoc 2.6.1** 生成
  `bluetooth.pb.cc`，并定义 `GOOGLE_PROTOBUF_NO_RTTI`（我们全程 -fno-rtti）
- `tools/build/audit_symbols.py`：链接期符号审计。实测结果
  **UND 强符号 229 个，无法解析 0 个**（递归解析 56 个库、27755 个可提供符号）
- 产物：`bld/out/bluetooth.default.so`，`llvm-strip --strip-unneeded`，
  同时保留 `.unstripped`（6.5MB，供 addr2line 符号化）

### 5. 首次刷入 + 基线验证 ✅
- 刷入的产物：md5 `df19dc6ace21e90aed41c357daccdc58`（1764376 B，`build/` 目录时期的构建）
- 传输链：沙箱 `uguu.py up` → `https://n.uguu.se/RQbPoWUz.so` → Windows `curl.exe --ssl-no-revoke -o`
  → `Get-FileHash` 校验 → `adb push /data/local/tmp/bt_new.so` → root 脚本原子安装
- 原子安装实测（`scripts/device/flash_bt_so.sh`，md5 门控生效）：
  `remount_rw_rc=0 / cp_rc=0 / mv_rc=0 / remount_ro_rc=0`，
  装完 `-rw-r--r-- root root u:object_r:system_file:s0`（**SELinux 标签正确**，restorecon 生效），
  设备侧 `md5sum` 复核 = `df19dc6ace21e90aed41c357daccdc58` ✅
- 开启蓝牙（`service call bluetooth_manager 6` = IBluetoothManager.enable()）后：
  ```
  Bluetooth Status
    enabled: true
    state: STATE_ON
    address: 00:00:46:03:35:01
    name: U2
  ```
  `com.android.bluetooth` 进程存活（pid 2664，RSS≈48MB），`/data/tombstones` **空**，
  logcat 无 FATAL/SIGSEGV/SIGBUS/dlopen 失败。
  → **干净 AOSP r36 源码构建的 bluetooth.default.so 在 MT6735 实机上成功接管蓝牙栈。**
- 目录重构后重建（`build/` → `bld/`，见 docs/04-pitfalls.md 的快照排除坑）：
  大小完全一致 1764376 B，md5 `845164e3c8a6266faefcec3e8128a995`
  （仅调试信息里的路径字符串不同，功能等价；设备上仍是 df19dc6a 那份，不重复刷）

### 6. profile 现状（决定 R1 的路线）
`dumpsys bluetooth_manager` 里 stock `Bluetooth.apk` 只启用了 **source 侧** 7 个 profile：
```
BtGatt.GattService / HeadsetService(HFP AG) / A2dpService(A2DP SRC) /
HidService / HealthService / PanService / BluetoothMapService
```
grep `sink|A2dpSink|AvrcpController|HfpClient` → **0 命中**。
即：native 侧 sink 能力齐全（`btif_av_sink_execute_service()`、`BTA_AvEnable_Sink()`、
`btif_avrcp_audio_track.cpp` 都在），但 **Java 层从不调用它们**。
`/system/app/Bluetooth/oat/arm/Bluetooth.odex` 存在（8.6MB）→ 换 APK 必须同时 `rm -rf oat`。
设备上的蓝牙相关包：`com.android.bluetooth`(/system/app/Bluetooth)、
`com.mediatek.bluetooth.dtt`(/system/vendor/app/BtTool)、`com.android.bluetoothmidiservice`。

### 7. 待用户决策 / 需要用户配合
1. **APK 是否替换**：stock 固件是 `user/release-keys`，我们**没有 MTK 平台签名私钥**。
   重签 APK 会破坏 signature 级权限（BLUETOOTH_STACK 等）与 sharedUserId 一致性。
   我倾向 **纯 native 路线**：在我们自己的 `.so` 里主动注册 A2DP SINK SEP +
   AVRCP CT，并把 source 侧注册收敛掉（详见 docs/05-roadmap-and-design.md 的 R1 方案）。
   → 请确认是否同意走 native-only，或你有平台签名/可签名的 APK 来源。
2. **debug boot 版本**：init banner 显示 `repo-rootexec-v6`，任务书写的是 V7；
   且 executor 是极简版（不自动生成 root_dmesg 等）。是否需要我们按 V7 的行为写兼容脚本？
   （当前已按实测 v6 行为工具化，不影响开发）
3. **push / Release**：任务书要求仓库操作前确认。P0 的 commit 已在本地准备好，
   是否 push 到 `main` 并打 Release `p0-baseline-build-flash-20261104`？

### 8. 本轮新入册的坑（详见 docs/04-pitfalls.md）
- **绝不要在桥接命令里跑 `adb kill-server`**：Windows 侧代理会被卡住，
  表现为握手成功但 ping 超时，只能等远端命令超时自愈（实测 ~2–3 分钟）。
- 沙箱 bash 工具默认 30s 超时 → 跑桥接命令必须显式给 `timeout` 参数，否则本地被杀、远端还在跑。
- uguu.se **按内容识别类型**：zip/apk 一律 `415 Filetype not allowed`（改扩展名无用）；
  **tar 放行**（application/x-tar），ELF/.so/.txt 放行 → 传输统一用 tar 或 base64→.txt。
- PowerShell `>` 重定向是 UTF-16；读设备侧 UTF-8 文件要 `[IO.File]::ReadAllText(...,UTF8)`，
  否则中文注释在回显里变成乱码。
- **toybox grep 不支持 `\|` 交替**（`grep "a\|b"` 恒不命中）→ 用多个 `-e`。
- 设备 `sh` 无 awk；`svc bluetooth enable` 不支持（只打印 usage）→ 用 `service call bluetooth_manager 6`。
- `kill \`pidof com.android.bluetooth\`` 在 BT 未启动时 pidof 为空 → kill 打印 usage 并 rc=1（无害）。
- 工作区快照**排除名为 `build`/`out`/`dist` 等的目录** → 仓库里的构建配置目录改名为 `bld/`，
  产物放 `bld/out/`（正好不进快照）。
- NDK 的 libc++ 是 `__ndk1` inline namespace，与设备 `/system/lib/libc++.so`（`__1`）**不兼容**，
  必须用 AOSP `external/libcxx @ r36` 头 + `-nostdinc++`。
- NDK r21e sysroot 缺 `linux/binder.h`（`binder/Parcel.h` 的传递依赖）→ 见 `bld/shim_include/`。
- aosp-mirror 上多个仓库不存在（clone 会报 `could not read Username`）→ 一律用
  `android.googlesource.com`；`platform/external/fdk-aac` 在 googlesource 上也不存在（P2 需另找源）。

### 9. 文档与工程化落地（本轮完成）

仓库骨架已从"只有 LICENSE"补齐为可复现工程：

```
README.md                     项目总览 + 快速开始 + 9 条铁律
docs/00-device-facts.md       设备事实（型号/内核/适配器/原厂件/profile/配置/命令速查）
docs/01-root-channel.md       root executor 协议 + 硬约束 + root_run.sh 用法 + 失效恢复
docs/02-build-system.md       构建体系（目录约定/启动顺序/工具链参数/protobuf/上游核对）
docs/03-flash-and-transfer.md 三段传输 + 原子刷入配方 + 门控 + 验证 + 回滚 + 配对缓存
docs/04-pitfalls.md           坑册（A 桥接 / B 传输 / C 设备 / D 沙箱 / E 构建ABI，共 30+ 条）
docs/05-roadmap-and-design.md 架构决策（native-only sink）+ P1 技术方案 + P2/P3 缺口 + 阶段验收
tools/build/setup_env.sh      全新会话一键重建 /tmp 环境（NDK+AOSP 头树+host protoc，幂等）
.gitignore                    忽略 bld/out/ 与一切凭据文件
```

`bld/` 目录改名（原 `build/`）已落实到全部脚本；`devlibs/`（设备 .so 快照，50 文件/16MB）
持久化在工作区根，`build_bt.py:load_env()` 会自动回退到它，`/tmp` 被清空也不影响链接输入。

### 10. P1 源码预研 —— 三个决定性发现（2026-11-04）

在写 P1 代码之前先把 r36 的 sink 数据通路读通了，结论比预期更明确：

**(1) 我们的 P0 产物里 sink 机制已经齐全（`llvm-nm` 实测）**

```
BTA_AvEnable_Sink        bta_av_api_sink_enable       AVDT_SINK_Activate/Deactivate
bta_av_co_audio_sink_supports_config / _supports_cp / _getconfig      bta_av_co_sbc_sink_caps
btif_av_sink_execute_service   btif_av_get_sink_interface   btif_av_is_sink_enabled
init_sink / cleanup_sink / src_connect_sink / btif_media_sink_enque_buf
```
并且 `btif_dm.c:330` 的 `btif_in_execute_service_request()` 已能处理
`BTA_A2DP_SINK_SERVICE_ID → btif_av_sink_execute_service()` —— **只是没人调用**（Java 无 A2dpSinkService）。

**(2) ★ AOSP r36 官方 make 根本没开 `USE_AUDIO_TRACK`**
`grep -rn "USE_AUDIO_TRACK" --include=*.mk src/` → **0 命中**，`bld/plan.json` 里也没有。
后果：sink 的 PCM 出口走的是 `#else` 分支 `UIPC_Send(UIPC_CH_ID_AV_AUDIO,…)`，
而且 `btif_media_task_handle_inc_media()` 在 `#ifndef USE_AUDIO_TRACK` 下会因
`data_channel_open == FALSE` **直接 return** —— 手机上是一条死路。
（原厂 `.so` 里那批 `AudioTrack` 未定义符号，只是因为 `btif_avrcp_audio_track.cpp`
被列进了 libbtif 源文件、**编译了但无人调用**。）
→ **P1 的第一个单变量改动就是加 `-DUSE_AUDIO_TRACK`。**

**(3) 开了 `USE_AUDIO_TRACK` 之后会出现"焦点门禁"，必须由我们在 native 侧授予**
`btif_media_task.c:1319`：`if (rx_audio_focus_state == BTIF_MEDIA_FOCUS_NOT_GRANTED) return;`
初值是 NOT_GRANTED（`:931`），只有 Java 通过 `bt_av_sink_interface.update_audio_focus_state()`
才会改它。我们没有 Java sink 服务 → 计划新增 `src/u2/u2_sink_policy.c`，
在 `audio_state_cb(STARTED)` 时调 `btif_a2dp_set_audio_focus_state(BTIF_MEDIA_FOCUS_GRANTED)`
（专用接收器无焦点竞争，"永远已授权"是正确策略）。
另：`bt_av_sink_callbacks` 的全部使用点都有 `!= NULL` 保护，传我们自己的回调结构最安全。

**(4) 48k 现状**：`btif/co/bta_av_co.c:84` 的 `bta_av_co_sbc_sink_caps` 广告
`A2D_SBC_IE_SAMP_FREQ_48 | _44`（手机通常挑 44.1）。P1c 单变量实验：只广告 48 → 逼协商 48k；
失败则退化为"48|44 + 在 `bta_av_co_audio_sink_supports_config()` 里优先 48"。
解码侧 `btif_media_cb.sample_rate = btif_a2dp_get_track_frequency(sbc_cie.samp_freq)`，
`BtifAvrcpAudioTrackCreate()` 直接用协商值建 track → **无重采样**，符合全局硬约束。

**(5) P2/P3 缺口比想象大**：`src/stack/a2dp/` 只有 `a2d_api.c / a2d_int.h / a2d_sbc.c`，
**没有 AAC、没有 LDAC、没有 vendor codec**；`bta_av_co_audio_sink_supports_config()` 的
switch 只有 `BTA_AV_CODEC_SBC`，default 直接 `return FALSE`。
→ AAC/LDAC 都要自己补三层：SEP+caps、解码器接入（fdk-aac / libldac 2.0.72 全源码静态链）、
media task 按 codec_type 分派。fdk-aac 在 googlesource r36 上不存在，需另取源码 vendored。

以上全部写入 `docs/05-roadmap-and-design.md`（含风险表与阶段验收标准）。

---

## 2026-11-04 · P1a + P1b：native 侧启用 A2DP SINK + 扬声器身份（已刷入验证）

用户决策（本轮确认）：① push + Release P0 ✅ ② 走 **native-only**，APK 仅作后备
③ P1 第一步 = `USE_AUDIO_TRACK` + 主动启用 sink service（合并做）④ root executor 按实测 v6 行为继续。

### P0 归档
- 已 push `main`（`15376bd`，841 文件）
- Release **`p0-baseline-build-flash-20261104`** 已建，附件 `bluetooth.default.so`
  (`845164e3…`) + `bluetooth.default.so.unstripped` (`2d51578b…`)

### P1a：`-DUSE_AUDIO_TRACK` + `src/u2/u2_sink_policy.c`

**读码得到的三个硬约束（决定了实现方式）**

1. r36 的 `btif_av` 只有**一个** AV 槽位：`btif_av_init(service_id)` 以 `sm_handle == NULL`
   为门槛，**只有第一次调用**才会 `btif_enable_service(service_id)`。→ 谁先 init 谁拿到
   service enable 的机会；Java 的 `init_src` 若先跑，SINK 就永远不会被 enable。
2. `bta_av_api_enable()` 第一件事是 `memset(&bta_av_cb, 0, sizeof(tBTA_AV_CB))`
   → **`BTA_AvEnable` 全生命周期只能调用一次**，source 与 sink 不可并存。
   所以我们**绝不能**自己直接调 `btif_av_sink_execute_service()`，否则与 DM 的
   service 循环形成第二次 `BTA_AvEnable`，把已注册的控制块抹掉。
3. 正确的触发点在 **`btif_init_ok()`**（`btif_core.c`，由 `stack/btu/btu_task.c:193`
   经 `btif_transfer_context` 投递，运行在 btif task，**早于** `BTA_EnableBluetooth`，
   也因此早于 Java 收到 STATE_ON 后的 profile init）。此刻 `stack_is_running == false`，
   `btif_enable_service()` 只置位；随后 `btif_dm.c:1712` 的 `BTA_DM_ENABLE_EVT`
   service 循环会**恰好一次**地执行 `btif_in_execute_service_request(18, TRUE)`
   → `btif_av_sink_execute_service(TRUE)` → `BTA_AvEnable(...)` + `BTA_AvRegister(AUDIO_SINK)`。

**改动（3 处，都很小）**

| 文件 | 改动 |
|---|---|
| `tools/build/build_bt.py` | 新增 `EXTRA_PROJECT_SOURCES=["src/u2/u2_sink_policy.c"]`（借 libbtif 的 include/flags 上下文）与 `EXTRA_PROJECT_DEFINES=["-DUSE_AUDIO_TRACK"]` |
| `src/u2/u2_sink_policy.{h,c}` | 本项目自有代码：自己的 `btav_callbacks_t`、`install()`、焦点授予、采样率观测 |
| `src/btif/src/btif_core.c` | `btif_init_ok()` 里加一行 `u2_sink_policy_install();` |

`USE_AUDIO_TRACK` 的必要性（源码依据）：未定义时 sink 的 PCM 走
`UIPC_Send(UIPC_CH_ID_AV_AUDIO,…)`，而且 `btif_media_task_handle_inc_media()` 会因
`data_channel_open == FALSE` **直接 return**（手机上没有 UIPC 消费者 = 死路）；
定义后走 `BtifAvrcpAudioTrackWriteData()` → native `android::AudioTrack`
（`AUDIO_STREAM_MUSIC` / `PCM_16` / `AUDIO_OUTPUT_FLAG_FAST` / `TRANSFER_SYNC`，
采样率**直接取协商值** → 48k 无重采样）。
实测 `grep -rn USE_AUDIO_TRACK --include=*.mk src/` → **0 命中**，即 AOSP r36 官方 make 不开它。

焦点门禁：`btif_media_task.c:1319` `if (rx_audio_focus_state == NOT_GRANTED) return;`
（初值 NOT_GRANTED，官方由 Java A2dpSinkService 授予）。我们在
`install()` 里预授予 + `audio_state_cb(STARTED)` 再授予一次（专用接收器无焦点竞争）。

**构建/刷入/实测**

| 项 | 值 |
|---|---|
| 构建 | 313 obj（+1 我们的文件），链接 OK |
| 符号审计 | 229 UND / **0 未解析**；DT_NEEDED 与原厂完全一致（未新增运行时依赖） |
| P1a md5 | `d73dc8c9509347a1a95e1bc0d4c47e11`（1768488 B）→ 已原子刷入 |
| `USE_AUDIO_TRACK` 生效证据 | 产物新增 `btif_a2dp_set_audio_focus_state` / `btif_a2dp_set_audio_track_gain` / `update_audio_focus_state` / `update_audio_track_gain` |
| 运行证据 | `install: enter (installed=0, stack_running=0)` → `init_sink() -> 0` → `A2DP SINK service 已置位 (service_id=18)` → `audio focus 预授予 GRANTED` |
| 稳定性 | **STATE_ON**，地址/名称正常，`/data/tombstones` **total 0** |
| 映射核对 | `/proc/<pid>/maps` 里是 `/system/lib/hw/bluetooth.default.so`（**无 `(deleted)`**）→ 确认跑的是新 .so |

副作用（有意为之，等于顺手完成了原计划的 P1b「收敛 source」）：Java 之后的
`init_src` 因 `sm_handle != NULL` 而不再 enable source → 设备上只会有 AudioSink 的
SDP 记录。`dumpsys` 里 Java 侧仍列出 A2dpService（无害，其 native 侧未注册）；
另观察到 framework 侧本来就有 `E BluetoothA2dpSink: Could not bind to Bluetooth A2DP
Service`（改前就存在，说明 Android 7 framework 有 sink API，只是 APK 没启用服务）。

### P1b：COD 0x240414 + 常驻可发现

| 文件 | 改动 |
|---|---|
| `bld/bdroid_buildcfg/bdroid_buildcfg.h` | `#define BTA_DM_COD {0x24, 0x04, 0x14}`（覆盖 `include/bt_target.h:397` 的默认 `{0x5A,0x02,0x0C}` = 手机） |
| `src/u2/u2_sink_policy.{h,c}` | 新增 `u2_sink_policy_on_adapter_on()`（`BTA_DmSetVisibility(GENERAL_DISC, CONN, IGNORE, IGNORE)`）与 `u2_sink_policy_force_scan_mode()` |
| `src/btif/src/btif_core.c` | `btif_enable_bluetooth_evt()` 成功分支调用 `on_adapter_on()`；`btif_set_adapter_property()` 的 `BT_PROPERTY_ADAPTER_SCAN_MODE` 分支调用 `force_scan_mode()` |

字节序已核对：`stack/include/bt_types.h:247` 的 `DEVCLASS_TO_STREAM` 按
`a[2],a[1],a[0]` 写出 → `BTA_DM_COD` 数组是 **MSB 在前**。
二进制层对照（直接扫产物字节，含 `bta_dm_cfg` 其余字段，可确认改对了结构体）：

```
原厂 stock : 5a 02 0c 00 0f 00 00 20 40 1f 01   → COD 0x5A020C（手机）
P1b 我们的 : 24 04 14 00 0f 00 00 20 40 1f 01   → COD 0x240414（Loudspeaker）
```

**为什么必须拦截 Java 的 scan mode**：实测适配器 ON 后约 5 秒，Java 下发
`disc=0x0 conn=0x1`（只可连接、不可发现）—— 日志 `W u2_sink_policy: 覆盖上层 scan mode 请求
disc=0x0 conn=0x1 → GENERAL_DISC + CONN` 证明拦截是必要的且生效。

**P1b 构建/刷入**：md5 `84f09a4cd465d1130f913c9f35517f2e`（1768488 B），原子刷入成功，
STATE_ON，零 tombstone，策略日志完整（install → SINK armed → focus → adapter ON visibility → 覆盖降级）。

### ★HCI 层硬证据（新工具链：btsnoop 落盘 + 本地解析）

新增 `scripts/device/enable_snoop.sh`、`scripts/device/dump_snoop.sh`、`tools/dev/parse_snoop.py`。
**不需要第二台蓝牙设备**即可验证射频侧参数：

```
=== Class of Device（HCI Write_Class_of_Device, 0x0C24）===
  [  58] bytes(LE)=14 04 24  →  COD=0x240414  major=0x04(Audio/Video)  minor=0x05(Loudspeaker)  services=[Rendering,Audio]
  [ 128] bytes(LE)=14 04 34  →  COD=0x340414  …services=[Rendering,Object Transfer,Audio]
=== Scan Enable（HCI Write_Scan_Enable, 0x0C1A）===
  [  84] 0x01 → [86..122] 0x03 = inquiry + page scan（可发现 AND 可连接）
```

- `0x240414` 正是 R1 要求的"扬声器"身份 ✅
- 稍后变成 `0x340414` 是 SDP 记录建立后 Fluoride 自动把 **Object Transfer** 服务位或进去
  （MAP/PBAP 之类），主类/次类仍是 Audio/Video + Loudspeaker，属外观差异；
  后续裁掉用不到的 profile 时会自然消失。
- Scan Enable 终值 `0x03` → 手机现在**无需进设置界面**就能扫到并配对 ✅

### 本轮新入册的坑（已写入 docs/04）

1. **`getprop log.tag` = `E`**：本机把全机日志最低优先级压到 ERROR，
   所以 `LOG_INFO/LOG_WARN` 全被 liblog 过滤（只有 `E bt_btif` 这类能看见）。
   对策：`setprop log.tag.<我们的TAG> V`（只影响单 tag，不动全机设置）。
   → 这就是第一版验证脚本"什么都看不到"的原因，不是代码没跑。
2. **`bt_stack.conf` 里 `BtSnoopFileName=/sdcard/mtklog/btlog/btsnoop_hci.log`，而
   `/sdcard/mtklog` 目录根本不存在** → `btsnoop.c` 的 `open()` 失败、`is_logging=false`，
   一个字节都不写（logcat: `E bt_snoop: update_logging unable to open ...`）。
   对策：改成 AOSP 默认 `/data/misc/bluedroid/btsnoop_hci.log`（bluetooth uid 自己的目录）。
3. `/data/misc/bluedroid` 是 `bluetooth:net_bt_stack 750`，adb shell 读不到、本机又没 `su`
   → snoop 文件必须先用 root executor `cp` 到 `/data/local/tmp` 并 `chmod 666`。
4. `mount -o ro,remount /system` 在 BT 进程运行时可能 `Device or resource busy`（rc=1），
   不影响刷入结果（`mv` 已完成、`sync` 已做），但要知道 /system 会留在 rw。
5. `build_bt.py` 的增量判断只看**源文件** mtime，不跟踪头文件 →
   改了 `bdroid_buildcfg.h` 这类头之后必须 `--force` 全量重编，否则改动不生效。

### 下一步（等用户配合的真机出声测试）
R1 剩下的部分必须有第二台设备：手机扫描 → 应显示为**扬声器/音频设备** → 配对 →
勾选"媒体音频" → 播放。届时我方会抓 `audio_config_cb` 的 `sample_rate`
（预期 44100，因为 sink caps 还广告 `48|44`）与 snoop 里的 AVDTP SET_CONFIG，
然后进入 **P1c：把 `bta_av_co_sbc_sink_caps` 收敛到 48k-only**（全局硬约束）。
风险预判：`BtifAvrcpAudioTrackCreate` 用了 `AUDIO_OUTPUT_FLAG_FAST`，44.1k 的 FAST track
在 MTK 48k AudioFlinger 上可能 `initCheck()` 失败 → 表现为"连上但没声"，
届时日志会有 `A2dpSink: Track creation fails!!!`；若如此，P1c 的 48k-only 正好一并解决。

---

## 2026-11-04 · 真机出声测试（Android + Windows）与 P1c

### ★里程碑：SBC 出声成功（R1 前半达成）

用户实测（P1b 版本 `84f09a4c…` 在机时）：

| source | 识别身份 | 出声 | 音量 |
|---|---|---|---|
| Android 手机 | 扬声器 ✅ | **正常出声** ✅ | **可正常调节** ✅ |
| Windows PC | 扬声器 ✅ | **正常出声** ✅ | **无效**（仅降到 0 时触发静音）❌ |

这证明 P1a 的两个关键判断都成立：`-DUSE_AUDIO_TRACK` 把 PCM 出口切到 native AudioTrack 是对的，
native 侧自授予 audio focus 也是必要的（否则解码定时器会一直丢帧）。

### Windows 音量失效 —— 根因（源码级定位，未修，排入 P1d）

`btif/src/btif_rc.c` 的 `handle_avk_rc_metamsg_cmd()`（AVK = A2DP **sink** 侧的 AVRCP 命令入口，
即我们作为 **TG** 收到远端 **CT** 的命令）对两个关键 PDU **只打日志，然后转给 Java**：

```c
if (avrc_cmd.pdu == AVRC_PDU_REGISTER_NOTIFICATION) { BTIF_TRACE_EVENT("Register notification event_id: %s", ...); }
else if (avrc_cmd.pdu == AVRC_PDU_SET_ABSOLUTE_VOLUME) { BTIF_TRACE_EVENT("Abs Volume Cmd Recvd"); }
btif_rc_ctrl_upstreams_rsp_cmd(avrc_cmd.pdu, &avrc_cmd, pmeta_msg->label);
   → HAL_CBACK(bt_rc_ctrl_callbacks, setabsvol_cmd_cb / registernotification_absvol_cb, ...)
```

`bt_rc_ctrl_callbacks` 是 Java **AvrcpControllerService** 的回调，我们没有它（HAL_CBACK 有 NULL 保护）
→ **命令被静默丢弃**：既没把音量应用到 AudioTrack（缺 `btif_a2dp_set_audio_track_gain()`），
也没给 `RegisterNotification(VOLUME_CHANGED)` 回 interim response。

配合 `bta/av/bta_av_main.c:557-566`（sink 注册时 TG 记录**明确用 AVRC_REV_1_4**，
注释写着 "Initialize AVRCP1.4 to provide Absolute Volume control"）就能解释两端差异：

- **Android**：注册通知拿不到 interim response → 判定绝对音量不可用 → **退回软件音量**
  （在编码前缩放 PCM）→ 听感正常 ✅
- **Windows**：不做软件缩放，只认绝对音量；我们不应答 → 滑块无效，只有 0 时静音 ❌

**P1d 修复方案**（纯 native，不依赖 Java）：在我们的策略层实现 TG 侧绝对音量：
① `RegisterNotification(VOLUME_CHANGED)` 回 interim（当前音量 0–127）并保持事务不释放，
后续音量变化再发 changed；② `SetAbsoluteVolume` → `btif_a2dp_set_audio_track_gain(vol/127.0f)`
+ 回 accept；③ 本地音量变化时主动发 changed 通知，让手机 UI 同步。

### 顺带解释：COD 为什么会从 0x240414 变成 0x340414

`bta/av/bta_av_main.c:568-575`：Fluoride 在注册 AV 通道时**按角色自动或上服务类位**
（SOURCE→`BTM_COD_SERVICE_CAPTURING`，SINK→`BTM_COD_SERVICE_RENDERING`），
多出来的 Object Transfer 位来自 APK 启用的 MAP（Message Access Server）。
主类/次类（Audio/Video · Loudspeaker）不受影响，属外观差异。

### 需求修订（用户本轮明确）

1. **R1 身份修订**：用户期望的不是"扬声器"，而是**"有麦克风的耳机"**，且要**真 HFP**
   （能接打电话 + 真麦克风上行），不是只改图标。
   → 新增阶段 **P1f：HFP HF + SCO 音频路由**（r36 有 `btif/src/btif_hf_client.c` = HF 角色，
   native 侧可走与 A2DP sink 同样的"抢槽位 + 置 service 位"套路：
   `BTA_HFP_HS_SERVICE_ID` → `btif_hf_client_execute_service(TRUE)`）。
   COD 届时改为 headset 类（提案 `0x2C0404` = Audio/Video · Wearable Headset ·
   Rendering+Capturing+Audio），并实测手机/PC 的识别结果。
   注意 SCO 语音是 8k(CVSD)/16k(mSBC)，与 48k 音乐链路共存，需要显式的音频通路切换设计。
2. **AVRCP 方向修订**：使用场景是**线控插在 U2 上**（耳机线控/有线控制器），
   由 U2 去控制**手机/PC 侧**的播放暂停 —— 即 U2 是 AVRCP **CT**（发 passthrough），
   不是我先前假设的"手机控制 U2"。
   → 新增阶段 **P1e：本地线控 → AVRCP CT passthrough**。
   r36 native 侧已有 CT 接口（`bt_rc_ctrl_interface.pass_through_cmd`），
   我们要做的是把 U2 的耳机按键（`KEY_HEADSETHOOK` / `KEY_PLAYPAUSE` / `KEY_NEXTSONG` …）
   从 `/dev/input/eventX` 读出来并映射成 passthrough opcode。
   音量键则本地调 AudioTrack 增益，并按 P1d 的机制回发 VOLUME_CHANGED 通知保持手机 UI 同步。

### P1c：SBC sink caps 收敛到 48k-only（已构建 + 已刷入，待真机确认）

A2DP 的 caps 是**位掩码**，表达不了"优先 48k"——采样率完全由 source 在我们的能力集里挑。
原厂 sink caps 同时广告 `48|44`，手机/PC 通常挑 44.1k。**只广告 48k 是唯一能真正拿到 48k 的办法。**

改动（单变量，带回退开关）：

| 文件 | 改动 |
|---|---|
| `src/btif/co/bta_av_co.c` | `bta_av_co_sbc_sink_caps.samp_freq` 由 `48\|44` 改为 `#if (U2_SBC_SINK_48K_ONLY == TRUE)` 时只留 `A2D_SBC_IE_SAMP_FREQ_48` |
| `bld/bdroid_buildcfg/bdroid_buildcfg.h` | `#define U2_SBC_SINK_48K_ONLY TRUE`（置 FALSE 即回退到 48\|44） |

同时影响两处（都是 sink 侧，语义一致）：AVDTP SEP caps
（`bta_av_co_audio_sink_getconfig()` → `A2D_BldSbcInfo`）与 SET_CONFIG 校验
（`bta_av_co_audio_sink_supports_config()` → `bta_av_sbc_cfg_in_cap()`）。
source 若坚持 44.1 会在 SET_CONFIG 阶段被拒 —— 符合 A2DP 规范（source 必须从 sink 能力集里选）。

**字节级验证**（用符号地址直接 dump `.rodata`，不靠猜字节模式）：

```
bta_av_co_sbc_sink_caps @0x1a75ee : 10 0f f0 0c 03 fa 02
   samp_freq = 0x10 = A2D_SBC_IE_SAMP_FREQ_48 仅 48k   ← 改前是 0x30 (48|44)
   ch_mode=0x0f(MONO|DUAL|STEREO|JOINT) block=0xf0(4|8|12|16) subband=0x0c(4|8)
   alloc=0x03(SNR|LOUDNESS) bitpool=2..250
bta_av_co_sbc_caps(SRC) @0x1a75e7 : 20 01 10 04 01 35 02   ← 未改动（44 only）
bta_dm_cfg             @0x1a6164 : 24 04 14 00 0f 00 00 20 40 1f 01   ← COD 0x240414 仍在
```

构建 md5 `bd7bcef2c0239b75dd917f78226baff0`（1768488 B），符号审计 229 UND / 0 未解析，
DT_NEEDED 未变。已原子刷入（原厂备份门控通过），**STATE_ON、`/data/tombstones` total 0**。

⚠️ 真机验证前必须做：**Android 与 Windows 都要"忘记设备 → 重新配对"**
（两端都会缓存 codec 偏好，见 docs/03 §7）。Windows 还需 bthserv 重启 + PnP Disable/Enable，
实测常要 2–3 轮。验收判据：我方 `audio_config_cb` 日志报 `sample_rate=48000`，
且 snoop 里 AVDTP SET_CONFIG 的 SBC 参数为 48kHz。

### 本轮新入册的坑
- **adb 会掉线**：测试过程中 U2 从 adb 掉了一次（PnP 里 `VID_0E8D&PID_2008` 状态变 Unknown，
  `adb devices` 空列表），root executor 脚本因此全部失败并报 `NO_OUTPUT_FILE`。
  对策：跑长流程前先 `adb devices` 探活；掉线时请用户重插 USB / 确认 USB 模式不是"仅充电"。
  **绝不用 `adb kill-server` 试图恢复**（会卡死远端代理，见 docs/04 A1）。
- 猜字节模式不可靠：`tA2D_SBC_CIE` 各位段的 bit 位置与直觉相反
  （ch_mode 是 b3..b0 而非 b7..b4），扫二进制会 0 命中。
  → 一律用 `llvm-nm` 取符号 vaddr + `llvm-readelf -S` 算 file offset 后直接 dump。

### P1e / P1f 前置探测（不刷机，只取事实）

设备回到 adb 后，写了两个只读探测脚本（`scripts/device/p1e_probe_input.sh`、
`scripts/device/p1f_probe_audio.sh`），一次性拿到 P1e（线控）与 P1f（HFP 音频）所需的全部平台事实：

**① 线控输入设备 = `/dev/input/event0`，name `ACCDET`**（MTK accessory-detect 驱动）

```
B: EV=3 (SYN|KEY)   KEY (0001): 0072 0073 00a4 0246
   114 KEY_VOLUMEDOWN / 115 KEY_VOLUMEUP / 164 KEY_PLAYPAUSE / 582 KEY_VOICECOMMAND
/sys/class/switch/h2w/state = 0   （0 无配件 / 1 带麦耳机 / 2 无麦耳机）
```

**注意**：ACCDET **不上报** `KEY_NEXTSONG`/`KEY_PREVIOUSSONG`/`KEY_HEADSETHOOK`。
线控的"切歌"要靠 MTK accdet 对**单击/双击/长按**的时序翻译（最终落到上面 4 个 code），
所以 P1e 第一步必须是插着线控跑 `getevent -lt /dev/input/event0` 抓原始事件序列，
按实测时序写映射表 —— 不能照抄别的平台的按键码。

**② 音频平台（P1f 的 SCO 通路判定，结论：F1 平台 PCM 路由有戏）**

声卡 `mt-snd-card` 里存在 BT SCO 专用 PCM DAI：
`00-10 Voice_MD1_BT_Playback`、`00-11 VOIP_Call_BT_Playback`、`00-12 VOIP_Call_BT_Capture`、
`00-09 DL1_AWB_Record`、`00-16 I2S0AWB_Capture`（AWB=16k 宽带，对应 mSBC）、`00-04 ULDL_Loopback`。
→ BT 芯片与音频 DSP 之间有 **PCM 硬连线**，SCO 由**平台**路由，
这解释了为什么 r36 的 `BTM_CreateSco()` 不带 route 参数、
`btif_hf_client.c:888-897` 只把 `AUDIO_OPEN/MSBC_OPEN` 事件转给 Java（`audio_state_cb` → AudioManager）。

`/vendor/etc/audio_policy.conf`：
- primary output `sampling_rates 44100|48000`，devices 含 `AUDIO_DEVICE_OUT_ALL_SCO`
  → **AudioTrack 48k 有 HAL 支持**，48k 硬约束在输出侧成立（不会被迫降到 44.1）
- primary input `8000|16000|32000|44100|48000`，devices 含 `AUDIO_DEVICE_IN_ALL_SCO` /
  `IN_COMMUNICATION` / `IN_VOICE_CALL`，channel_masks 含 `VOICE_UPLINK|VOICE_DNLINK`
  → **麦克风采集在 HAL 层存在**（R1′ 真麦克风有物理基础）
- a2dp 模块还声明了 input `AUDIO_DEVICE_IN_BLUETOOTH_A2DP`（44100 stereo）
  → 再次印证 P0 结论：MTK 固件本来就带 sink 能力，只是 Java 层没启用
- `attached_input_devices` 里**没有** SCO → SCO 输入必须运行时声明
  （`AudioSystem::setDeviceConnectionState(AUDIO_DEVICE_IN_ALL_SCO, AVAILABLE, …)`，
  等价于 Java 侧 `AudioManager.startBluetoothSco()` 的底层动作，可在 native 直接做）
- `tinymix` 不存在（exit 127）→ 不能命令行改 mixer，只能走 audio HAL / AudioSystem API

**③ 顺带确认**：在机 `bluetooth.default.so` md5 = `bd7bcef2c0239b75dd917f78226baff0`，
与 `/data/local/tmp/bt_want.txt` 一致（P1c 仍在机上，未被回滚）。

设计结论已写入 `docs/05` §3.8（P1c）/§3.9（AVRCP 双向）/§3.10（HFP 分层可行性 + F1′ 推荐路径）。

### P1d 根因升级：命令死在 **btif 派发器**，比之前判断的更靠前一层（二进制取证）

上一节说"`handle_avk_rc_metamsg_cmd` 转发给 NULL 的 `bt_rc_ctrl_callbacks`"—— 这只对了一半。
把 r36 的整条 TG 链路读完并用产物二进制核对后，实际链路是：

**(1) BTA 层：GetCapabilities 与通知注册都在 BTA 内部处理，配置是对的**

`bta/av/bta_av_act.c: bta_av_proc_meta_cmd()` 对 `AVRC_PDU_GET_CAPABILITIES`
**直接应答、不上报 btif**（`evt = 0`），内容取自 `p_bta_av_cfg->p_meta_evt_ids / num_evt_ids`；
`AVRC_PDU_REGISTER_NOTIFICATION` 先用 `bta_av_chk_notif_evt_id()` 校验事件是否在册，
不在册就地 `AVRC_STS_BAD_PARAM` 拒掉。

配置选择发生在 `bta/av/bta_av_main.c: bta_av_api_register()`：
```c
if (profile_initialized == UUID_SERVCLASS_AUDIO_SINK) p_bta_av_cfg = &bta_avk_cfg;   /* Sink+CT+TG(abs vol) */
else if (profile_initialized == UUID_SERVCLASS_AUDIO_SOURCE) p_bta_av_cfg = &bta_av_cfg;
```
我们 P1a 抢在 Java 之前 enable 的是 **SINK**，所以走 `bta_avk_cfg`。**产物二进制实测**
（`llvm-nm` 取符号 + `.data.rel.ro`/`.rodata` 偏移换算）：

```
bta_av_meta_caps_evt_ids  (SRC/TG) @0x1a6bae : 01 02 05        PLAY_STATUS/TRACK/PLAY_POS，无 VOLUME
bta_avk_meta_caps_evt_ids (SINK)   @0x1a6bb1 : 0d              AVRC_EVT_VOLUME_CHANGE ✓
bta_avk_cfg @0x1ae17c : co_id=0x1958(AVRC_CO_METADATA) avrc_mtu=512 br_mtu=1008
                        ct_cat=1 tg_cat=2 sig_mtu=672 transport_mtu=1008 flush_to=6
                        vdp_mtu=1008 vdp_flush_to=600 group=FALSE
                        num_co_ids=2  num_evt_ids=1  rc_pass_rsp=8(IMPLEMENTED_STABLE)
                        p_meta_co_ids→0x1a6b9c  p_meta_evt_ids→0x1a6bb1
bta_av_cfg  @0x1ae104 : co_id=0x1018(BROADCOM) … num_co_ids=2 num_evt_ids=3 → 0x1a6bae
```
→ **BTA 层完全就绪**：对端问 GetCapabilities(EVENTS_SUPPORTED) 会得到 `{0x0D}`，
注册 VOLUME_CHANGED 也会通过校验并被上报到 btif。sink 侧 TG 记录还是 AVRC 1.4
（`bta_av_main.c:557-566`）。

**(2) btif 层：这里才是命令被丢掉的地方**

`btif/src/btif_rc.c: btif_rc_handler()` 的 `BTA_AV_META_MSG_EVT` 分派（1069-1116）：
```c
if (bt_rc_callbacks != NULL)                  → handle_rc_metamsg_cmd()      /* Java AvrcpTarget 在 */
else if (bt_rc_callbacks == NULL &&
         bt_rc_ctrl_callbacks != NULL)        → handle_avk_rc_metamsg_cmd()  /* "Sink + CT + TG(abs vol)" */
else                                          → BTIF_TRACE_ERROR("Neither CTRL, nor TG is up, drop meta commands");
```
我们两个回调**都是 NULL**（APK 只启用了 source 侧 profile，没有 AvrcpController；
AvrcpTarget 是否注册待 (3) 确认）→ 命中第三支，**命令连解析都没做就被丢弃，
一个字节都不回**。Windows 的 `RegisterNotification(VOLUME_CHANGED)` 拿不到 interim、
`SetAbsoluteVolume` 拿不到 accept，于是滑块完全无效 —— 与实测吻合。

好消息：`handle_rc_connect()` 里 `rc_connected/rc_handle/rc_vol_label/rc_volume` 的设置
**不受回调 NULL 影响**（只有 uinput 初始化和 HAL_CBACK 被跳过），
而 r36 **已经写好了**我们需要的两个 TG 应答函数，可以直接复用：
```c
static bt_status_t set_volume_rsp(bd_addr, abs_vol, label);                       /* ACCEPT */
static bt_status_t volume_change_notification_rsp(bd_addr, rsp_type, abs_vol, label); /* INTERIM/CHANGED */
```
（二者带 `CHECK_RC_CONNECTED` 门禁，我们的 `rc_connected=TRUE` 能满足。）

**P1d 落地方案（最小改动，纯 native）**
1. 在 `BTA_AV_META_MSG_EVT` 的第三支（`Neither CTRL, nor TG is up`）里，
   改为先交给 `u2_sink_policy_rc_metamsg_cmd(&p_data->meta_msg)`：
   `AVRC_Ctrl_ParsCommand()` 解出 pdu；
2. `AVRC_PDU_REGISTER_NOTIFICATION && event_id==AVRC_EVT_VOLUME_CHANGE`
   → 记下 label、置 `btif_rc_cb.rc_notif[AVRC_EVT_VOLUME_CHANGE-1].bNotify=TRUE`
   → 回 **INTERIM**（当前增益 → 0..127），事务**不释放**；
3. `AVRC_PDU_SET_ABSOLUTE_VOLUME`
   → `btif_a2dp_set_audio_track_gain(vol/127.0f)` + 记住 vol
   → 回 **ACCEPT**（复用 `set_volume_rsp`）；
4. 本地音量变化（P1e 音量键）→ 回 **CHANGED**（复用 `volume_change_notification_rsp`），
   让手机/PC UI 跟着动；
5. 其余 pdu → `send_reject_response(..., AVRC_STS_BAD_CMD)`，行为与原厂 TG 一致；
6. 状态量放 `src/u2/u2_sink_policy.c` 的独立静态结构（不改任何与 vendor 共享的布局，
   热路径零新增分配）。

**待确认（`scripts/device/p1d_capture_avrcp.sh` 一次跑完即可判定）**：
`bt_rc_callbacks` 到底是 NULL（走第三支）还是非 NULL（走 `handle_rc_metamsg_cmd` →
`btif_rc_upstreams_evt` → Java AvrcpTarget，而 r36 的 `btif_rc_upstreams_evt` 里
**没有** `AVRC_PDU_SET_ABSOLUTE_VOLUME` 分支 → 落到 `default: send_reject_response(BAD_CMD)`）。
两种情形的修法不同：前者加第三支处理，后者要在 TG 路径里补分支。
脚本按 logcat 里出现哪一条日志来区分（见脚本头 Q2 的 a/b/c/d 四种情形）。

### P1d 实施细节（写代码前必须知道的 4 个事实，均已核对源码）

1. **`rc_notif[]` 的下标安全边界**：`btif_rc.c:60` `#define MAX_RC_NOTIFICATIONS AVRC_EVT_VOLUME_CHANGE`
   （= 0x0D = 13）→ 数组只有 13 个元素，合法下标 0..12；而写入用的是 `rc_notif[event_id-1]`，
   `VOLUME_CHANGE(13) → 下标 12`，**恰好是最后一个合法下标**。
   BTA 层 `bta_av_chk_notif_evt_id()` 已保证只有在册事件（我们的 caps 只有 0x0D）能到 btif，
   但我们自己的处理函数仍要加**显式边界检查**（`event_id >= 1 && event_id <= MAX_RC_NOTIFICATIONS`），
   任何越界写都会踩到 `btif_rc_cb_t` 后面的成员 —— 按项目铁律：宁可多一行检查。
2. **音量增益最终落在 `AudioTrack::setVolume()`**：
   `btif_a2dp_set_audio_track_gain(float)` → `BtifAvrcpSetAudioTrackGain(btif_media_cb.audio_track, gain)`
   → `trackHolder->track->setVolume(gain)`（`btif_avrcp_audio_track.cpp:123-134`，handle 为 NULL 时只打日志安全返回）。
   → 含义：**流还没开始（audio_track==NULL）时设的音量会静默丢失**。Windows 常常在连接时就下发音量，
   所以必须在策略层记住 abs_vol，并在 track 创建成功后**重放一次**。
3. **track 创建点（重放增益的钩子）**：`btif_media_task.c:2272`
   `btif_media_task_aa_handle_decoder_reset()` 的 `#ifdef USE_AUDIO_TRACK` 分支：
   `btif_media_cb.audio_track = BtifAvrcpAudioTrackCreate(track_frequency, channel_type)`。
   同一函数里 `btif_media_cb.sample_rate = btif_a2dp_get_track_frequency(sbc_cie.samp_freq)`
   —— 这就是 48k 硬约束在解码/输出侧的落点。
4. **P1c 的 48k 验收有三个独立证据源**（互为印证，避免单点误判）：
   ① `dumpsys media.audio_flinger` 的 Output thread `sampleRate`（**最权威**，HAL 实际值）；
   ② logcat（需 `setprop log.tag.bt_appl V`）里 `btif_media_task_aa_handle_decoder_reset`
      打印的 `\tsamp_freq:%d (48000)`；
   ③ snoop 里 AVDTP SET_CONFIG 的 SBC 参数第 1 字节 = `0x10`。
   取证脚本：`scripts/device/p1d_capture_avrcp.sh`（一次跑完同时回答 Q1 采样率 / Q2 AVRCP 落点）。

---

## 2026-11-04（续）· P1c 证伪与回退：48k-only SBC sink caps 让设备连不上

### 用户报告的现象（P1c 刷入后）
- **Windows**：能连上 U2，但**不被识别为可用的音频设备**（没有音频端点）
- **Android（nubia Z40）**：**根本连不上**

P1b 版本（caps = 48|44）时两端都能正常出声 → 唯一变量就是 P1c 的 caps，先取证再动手。

### 线上取证：AVDTP 层给出决定性答案

抓包 `sn_p1c.bin`（42547 B，993 条记录，md5 `f13e55ae4fc4a2913d2ad0090b9364c2`）。
为此把 `tools/dev/parse_snoop.py` 补强了（见下"工具修复"），解出完整协商过程：

```
[148] Connection_Request  bdaddr=50:AF:4D:D7:EE:4C COD=0x5A020C(手机)
[151] Connection_Complete status=0x00 handle=0x0032     → ACL 建立成功
[180] Remote_Name_Request_Complete name='HQC nubia Z40'
[211] Encryption_Change  enc=ON                          → 配对/加密成功
[221] AVDTP rx DISCOVER CMD
[222] AVDTP tx DISCOVER RSP :: SEID=1 inUse=0 media=audio tsep=SNK   ← 我们的 sink SEP 正常
[225] AVDTP rx GET_CAPABILITIES CMD  ACP_SEID=1
[226] AVDTP tx GET_CAPABILITIES RSP :: SBC freq[48k] mode[MONO|DUAL|STEREO|JOINT]
                                      blocks[4|8|12|16] subbands[4|8] alloc[SNR|LOUDNESS] bitpool 2..250
[316] AVDTP tx DISCOVER CMD                    ← 我们作为 CT 反查对端 SEP
[318] AVDTP rx DISCOVER RSP :: SEID=1 media=audio tsep=SRC
[321] AVDTP rx GET_CAPABILITIES RSP :: SBC freq[44.1k] mode[MONO|JOINT] subbands[8]
                                       alloc[LOUDNESS] bitpool 2..53
[335] Disconnection_Complete reason=0x13 (REMOTE_USER_TERMINATED)
```

**整份抓包里没有任何 `SET_CONFIGURATION`、没有 `OPEN`、没有 `START`。**
手机看到我们只有 48k、自己只有 44.1k → 采样率交集为空 → 静默放弃 → 断开。
Windows(C-TX4，COD=Computer，records 698+) 走的是同一条路：只到 GET_CAPABILITIES 就停 →
在 Windows 上就表现为"已连接，但没有可用音频端点"。

**根因不在我们的实现，而在对端的能力集**：AOSP r36 `btif/co/bta_av_co.c` 的
`bta_av_co_sbc_caps`（SRC）只广告 `A2D_SBC_IE_SAMP_FREQ_44`；Android 手机作为 source
就是这个能力集，Windows 的 SBC source 实测同样只给 44.1k。
→ **"只广告 48k"= 把自己做成砖**，P1c 的原始设计判断错误，我低估了 source 侧的现实。

### 处置：回退（单变量，开关早已预留）

`bld/bdroid_buildcfg/bdroid_buildcfg.h`：`U2_SBC_SINK_48K_ONLY` **TRUE → FALSE**，
并在注释里完整记录证伪证据与后续两条可行路线（双 SEP / 条件式拒绝，见 docs/05 §3.5）。

| 项 | 值 |
|---|---|
| 构建 | `--force --jobs 2`，313 obj，符号审计 229 UND / 0 未解析，DT_NEEDED 未变 |
| 产物 md5 | **`20f6380d54aea5d1ed0baa72fe7be537`**（1768488 B） |
| 字节校验 | `bta_av_co_sbc_sink_caps` @0x1a75ee = `30 0f f0 0c 03 fa 02` → samp_freq **0x30 = 48k\|44.1k** ✅ |
| 未受影响 | SRC caps `20 01 10 04 01 35 02`、`bta_avk_meta_caps_evt_ids = 0d`(VOLUME_CHANGE)、COD `24 04 14` |
| 刷入 | 原子（cp→`.bt_swap.tmp`→mv→sync），备份门控通过（原厂 `e0dd373e…` 在位），installed md5 复核一致 |
| 重启 | `adb reboot`（按坑册，不用反复 killall） |
| 重启后 | `boot_completed=1`、`bluetooth_on=1`、**STATE_ON**、name=U2、addr=00:00:46:03:35:01、BT pid 1540、`/data/tombstones` total 0 |

`mount -o ro,remount /system` 仍报 EBUSY（已知无害，/system 保持 rw）。

### 意外收获 1：P1d 的分支判断被活体日志证实

重启后 logcat 里出现：
```
E bt_btif_avrc: ### ASSERT : .../src/btif/src/btif_rc.c line 466 Callback is NULL (0) ###
```
`btif_rc.c:466` 正是 `HAL_CBACK(bt_rc_ctrl_callbacks, getrcfeatures_cb, &rc_addr, rc_features);`
→ **`bt_rc_ctrl_callbacks == NULL` 在真机上确认**。结合上一节的派发器分析，
P1d 的修复点就是第三支（`Neither CTRL, nor TG is up`）—— 不再是推测。
（`ASSERTC` 只打日志不 abort，所以没有 tombstone。）

### 意外收获 2：sink 模式下 AVRCP 通道确实建立了（利好 P1e）

同一批日志：
```
E bt_btif : bta_av_rc_create ACP handle exist for shdl:0
E bt_btif : bta_av_rc_opened rcb[1] shdl:1 lidx:1/0
```
且 `btif_rc.c:462-463`：`if (btif_av_is_sink_enabled()) getcapabilities_cmd(AVRC_CAP_COMPANY_ID);`
→ sink 场景下 r36 **会**建立 AVCTP 连接并作为 CT 主动发 GetCapabilities。
这意味着 **P1e 需要的 CT 通道天然存在**，不必自己再开一条 AVCTP 连接
（`btif_rc_cb.rc_connected/rc_handle` 也已就绪，`pass_through_cmd` 的 `CHECK_RC_CONNECTED` 能过）。

### 需求层面的结论（需要用户确认）

全局"48kHz 全链路"约束**在 SBC 上无法实现**——不是实现问题，是 Android/Windows 的
SBC source 只发 44.1k。建议把约束改写为：

> 在 source 支持的前提下全链路 48kHz（AAC/LDAC 为主路径）；
> SBC 场景以 source 实际能力为准（Android/Windows = 44.1k），**禁止任何形式的重采样**。

48k 的真正落点因此变成 **P2(AAC)** 与 **P3(LDAC)**：LDAC 48k@990kbps 本来就是 R3 的目标；
AAC 需要先实测手机的 AAC source caps（本次抓包里 nubia Z40 的 DISCOVER 应答**只有 1 个 SEP**
且是 SBC —— 这点可疑，可能是该机型只在特定条件下注册 AAC SEP，P2 第一件事就是把
手机的完整 SEP/caps 抓下来）。

若仍坚持"SBC 也要 48k 优先"，只有两条路（都在 docs/05 §3.5 策略 4）：
(a) 注册双 SEP（SEID1=48k-only, SEID2=48|44），属结构性改动，需独立阶段；
(b) caps 保持 48|44，但当对端 caps 含 48k 时拒绝其 44.1k 的 SET_CONFIG —— 本次抓包已证明
    **手机不会重试而是直接放弃**，所以 (b) 对 Android 极可能同样把设备做砖，只在
    Linux/BlueZ source 上验证过后才可考虑。

### 工具修复（本轮踩到并修好，避免以后再被骗）

`tools/dev/parse_snoop.py` 有两个错误，正是它们让第一眼看抓包时得出"没有 AVDTP"的错误结论：

1. **PSM off-by-two**：L2CAP Connection Request 的 payload 是 `code(1) id(1) len(2) PSM(2) scid(2)`，
   PSM 在 payload 偏移 **4**（即 `d[13],d[14]`）。原代码读的是 `d[11],d[12]`（= length 字段），
   于是所有连接请求都被误报成 `PSM 0x0004`，SDP(0x0001)/AVDTP(0x0019)/AVCTP(0x0017) 全被藏起来。
2. **AVDTP 头部位序照抄了规范文字而不是 Fluoride 实现**：真实布局来自
   `stack/avdt/avdt_defs.h`：
   ```c
   #define AVDT_MSG_PRS_HDR(p, lbl, pkt, msg)  lbl = *(p)>>4; pkt = (*(p)>>2)&3; msg = *(p++)&3;
   #define AVDT_MSG_PRS_SIG(p, sig)            sig = *(p++) & 0x3F;
   #define AVDT_MSG_BLD_HDR(p, lbl, pkt, msg)  *(p)++ = ((lbl)<<4)|((pkt)<<2)|(msg);
   ```
   即 octet0 = **label[7:4] | packet_type[3:2] | message_type[1:0]**，
   且 `CMD=0 / GRJ=1 / RSP=2 / REJ=3`（与规范正文里"00=general reject"的顺序**不同**）。
   按规范文字写的解码器会把我们的 GET_CAPABILITIES **应答**读成"命令 + 一般拒绝"。

新增能力：`--avdtp`（L2CAP CID↔PSM 映射 + AVDTP 信令全解码，含 SBC/AAC/Vendor codec
能力与配置、DISCOVER 的 SEP 列表含 **tsep=SRC/SNK**、REJECT 的 category+error code）、
`--events`（HCI 事件：Connection_Request 的 COD、Connection_Complete、
**Disconnection 原因码翻译**、Remote_Name、Encryption_Change）。
这两个开关在 P2/P3 验收 codec 协商时是主力工具。

### 本轮新入册的坑
- **改 A2DP caps 属于"能一次把设备做砖"的改动**，且砖得很安静：配对成功、ACL 成功、
  加密成功、DISCOVER/GET_CAPS 都正常，只在 source 心里放弃，logcat 里没有任何错误。
  → 任何 caps 改动，**必须先用 snoop 抓到 SET_CONFIGURATION 成功**才算数；
    "手机连不上"这类现象要先看 AVDTP 有没有 SET_CONFIG，再怀疑别的。
- **source 的能力集比想象的窄**：别假设"现代手机 SBC 支持 48k"。r36 SRC caps 只有 44.1k，
  Windows 实测也只有 44.1k。想拿 48k 请走 AAC/LDAC。
- PowerShell `Set-Content -Encoding ascii` 会写 **CRLF**，设备侧 `cut -d" " -f1` 读出来带 `\r`，
  导致 flash 脚本的 md5 门控比较失败（`ABORT: bt_new.so 与 bt_want.txt 不一致`）。
  → 写纯文本给设备用时一律 `[IO.File]::WriteAllText($p,$s,[Text.Encoding]::ASCII)`。

---

## 2026-11-04（再续）· P1d 实现 + 刷入：native AVRCP TG 绝对音量

### 用户定案（本轮两个决定）
1. **48kHz 策略 = 走 codec 路线**：SBC caps 保持 `48|44`，采样率由 source 决定
   （Android/Windows 实际给 44.1k）；48k 的目标改由 **P2(AAC) / P3(LDAC)** 承担；
   解码输出与 AudioTrack 始终等于协商值，**任何情况下不重采样**。
   双 SEP / 条件式拒绝两条路暂不做（留在 docs/05 §3.5 策略 4 作为 P2 之后的可选实验）。
2. **优先级维持原顺序**：P1d 音量 → P1e 线控 → P1f HFP。

### 实现（4 个文件，全部带开关，纯 native）

| 文件 | 改动 |
|---|---|
| `src/u2/u2_sink_policy.h/.c` | 新增音量状态机：`u2_sink_policy_rc_{get,set}_abs_vol` / `_on_track_created` / `_reset` / `_enabled`。状态放独立静态结构 `u2_rc_vol_t` 并带 head/tail canary（0x5532C0DE / 0xC0DE5532），破坏时只报不自愈 |
| `src/btif/src/btif_rc.c` | ① 新增 `u2_sink_rc_metamsg_cmd()`：最小 TG 实现，只做 `RegisterNotification(VOLUME_CHANGED)`→INTERIM 与 `SetAbsoluteVolume`→落增益+ACCEPT，其余 pdu 一律 `reject(BAD_CMD)`（与原厂 TG 的 default 一致）；② `BTA_AV_META_MSG_EVT` 派发器**第三支**改为先问 `u2_sink_policy_rc_enabled()`；③ `handle_rc_disconnect()` 里调 `u2_sink_policy_rc_reset()` |
| `src/btif/src/btif_media_task.c` | `btif_media_task_aa_handle_decoder_reset()` 里 `BtifAvrcpAudioTrackCreate()` 成功后调 `u2_sink_policy_rc_on_track_created()`，把此前存下的音量重放一次 |
| `bld/bdroid_buildcfg/bdroid_buildcfg.h` | 新增 `U2_AVRCP_TG_ABSVOL 1`（置 0 即完全回到原厂行为） |

复用的原厂设施（没有自己造 AVRCP 协议代码）：
- `AVRC_Ctrl_ParsCommand()` → `avrc_ctrl_pars_vendor_cmd()`：只认 SET_ABSOLUTE_VOLUME /
  REGISTER_NOTIFICATION，其余 pdu 直接返回 `BAD_CMD`，**并且已经把 volume 与
  `AVRC_MAX_VOLUME(0x7F)` 相与**，脏高位漏不进来。
- `send_metamsg_rsp()`：`AVRC_CMD_NOTIF(3)` → `AVRC_RSP_INTERIM`、`AVRC_CMD_CTRL(0)` →
  `AVRC_RSP_ACCEPT`；`AVRC_RSP_CHANGED` 分支还会自动用 `rc_notif[event_id-1].bNotify/label`
  做一次性变更通知（P1e 的本地音量键会用到）。
- `send_reject_response()`、`AVRC_BldResponse()`（`avrc_bld_tg.c:979` 已支持
  SET_ABSOLUTE_VOLUME 应答）。
- 线程模型与原厂一致：`btif_rc_handler` 在 btif task，而 r36 里 Java 调 HAL 的
  `update_audio_track_gain()`（`btav_interface_t` 成员，`btif_av.c:1265`）本来也是从
  非 media 线程调 `btif_a2dp_set_audio_track_gain()` → `AudioTrack::setVolume()`
  （经 proxy 写共享内存，跨线程安全）。

防御性细节：`MAX_RC_NOTIFICATIONS == AVRC_EVT_VOLUME_CHANGE(13)`，`rc_notif[]` 只有 13 个元素、
合法下标 0..12，而写入用 `[event_id-1]`（VOLUME_CHANGE 恰好落在最后一个合法下标）。
处理函数里仍显式挡了 `event_id != AVRC_EVT_VOLUME_CHANGE || <1 || >MAX_RC_NOTIFICATIONS`。

### 构建与刷入

| 项 | 值 |
|---|---|
| 构建 | `--force --jobs 2`，313 obj，**0 error / 0 warning**，符号审计 229 UND / 0 未解析 |
| 产物 md5 | **`94feb99d190028dc0877e2a6169fe2df`**（1 772 608 B，比回退版 +4120 B） |
| 新符号（unstripped） | `u2_sink_rc_metamsg_cmd@0x8cb88`、`u2_sink_policy_rc_{get_abs_vol@0x14f5e0, set_abs_vol@0x14f664, on_track_created@0x14f7a8, reset@0x14f7d4, enabled@0x14f83c}`、`u2_rc_canary_check@0x14f5fc`、`u2_rc_apply_gain_locked@0x14f718`、`u2_rc_vol@0x1b15b0(.data)` |
| 回归校验（字节级） | sink caps `30 0f f0 0c 03 fa 02`(48\|44) ✅ / SRC caps `20 01 10 04 01 35 02` ✅ / `bta_avk_meta_caps_evt_ids = 0d` ✅ / COD `24 04 14` ✅ |
| canary | 两个常量在产物里各出现 1 次 ✅ |
| 刷入 | 原子（cp→`.bt_swap.tmp`→mv→sync），备份门控通过，installed md5 复核一致；`adb reboot` |
| 重启后 | `boot_completed=1`、`bluetooth_on=1`、**STATE_ON**、name=U2、addr=00:00:46:03:35:01、**`/data/tombstones` total 0** |

⚠️ 本次刷入含**两个变量**（P1c 回退 + P1d），因为回退版已在机、P1d 叠在其上；
验收时用客观证据分别归因：snoop 里出现 `SET_CONFIGURATION`+`START` = 回退有效；
logcat 里出现 `u2_sink_rc_metamsg_cmd: SetAbsoluteVolume` + `rc gain: abs_vol=…` = P1d 有效。
若"没声音"，先把 `U2_AVRCP_TG_ABSVOL` 置 0 重刷一次即可把 P1d 单独摘出来。

### 待验（需要用户操作）
1. Android + Windows **都要重新配对**（caps 从 48k-only 改回 48|44，偏好有缓存）；
2. 播放音乐确认**出声恢复**（回到 P1b 的已知良好状态）；
3. 在 **Windows** 上拖音量滑块 → 预期滑块生效（P1d）；
4. 我这边跑 `scripts/device/p1d_capture_avrcp.sh` 取证归因。

### 本轮新入册的坑（docs/04 F5/F6/F7）
- **`setprop log.tag.X V` 之后看不到"刚才"的日志**：liblog 在**写入时**按 prop 过滤，
  prop 设置之前的 INFO/WARN 根本没进 buffer。必须先设 prop 再触发行为；`adb reboot` 会清掉 prop。
- **`ps -A` 在本机 toybox 上输出为空**（`ps -A -o PID,NAME` 同样无效）→ 一律用裸 `ps` + `grep -i -e blue`。
- **改 caps 后"连不上"却没有任何错误日志**：source 在 GET_CAPABILITIES 之后自行放弃，
  不发 SET_CONFIG、不报错、随后断开（HCI reason=0x13）。判据只有 snoop 里有没有
  `SET_CONFIGURATION`（用 `parse_snoop.py --avdtp` 看两端采样率交集）。
