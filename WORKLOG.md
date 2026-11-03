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
