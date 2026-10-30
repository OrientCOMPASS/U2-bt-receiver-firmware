# Nougat BT Swap — 在官方 7.0 固件上落地自研蓝牙栈

> **状态：B4 已交付（2026-10-30）——可刷包已发布，等待 B5 上机验证。**
> 8.1 全量构建路线终止（构建机始终未到位，v0–v4 实验固件与 workflows 已删除，
> 仓库回滚至 3c8b5b2）。基线直改（debloat）路线已产出可刷固件并在真机验证：
> `firmware-debloat-20261029` 刷入后**正常开机**——这同时证明了本路线 B4 的
> 两大前置：verity-off boot 重打包被 lk/init 接受；system.img 手术后 e2fsck/
> 挂载/启动全链路无损。
>
> **功能范围（用户 2026-10-30 确认，已按此交付）**：A2DP **Sink** +
> **AAC 解码** + **LDAC 660 kbps 接收**（DEVELOPMENT_PLAN.md §16.8）。
> 实际实现比原计划更简：`hegdi/libldacdec`（MIT clean-room）**直接静态链入**
> `bluetooth.default.so`（vendored 于 `third_party/libldacdec/`，无需
> `libldacBT_bco.so` shim）；AAC = FDK LATM（RFC 3016）静态链入；
> Java 层未动（R3 首选路径成立）。
>
> **执行载体**：`bt_standalone_build.sh --tag android-7.0.0_r36`（nougat 树）
> + `scripts/bt_symbol_gate.py`（B3 链接零缺口闸门）+ `.github/workflows/bt70.yml`
> （编译→链接→符号门）。补丁系列最终收敛为 `patches/nougat/system_bt/` 两个
> 补丁：**0001 控制面**（AVDTP/BTA 协商接受 AAC/LDAC sink）+ **0002 数据面**
> （解码挂接与 AudioTrack 渲染）；LDAC/AAC 解码器均为静态链入。
> B4 由 `.github/workflows/fw-btswap.yml` 完成（基于 fw-debloat v2 手术流水线，
> 仅替换 `/system/lib/hw/bluetooth.default.so` 一个文件）；B5 待用户上机。

## 1. 为什么走这条路

| 约束 | 事实 |
|---|---|
| 完整 AOSP 8.1 构建 | 需要 ~300 GB 磁盘 / 32 GB RAM / 多核，首次 1–3 h+，目前没有这样的算力（见 `INCREMENTAL_BUILD.md`） |
| 官方基线固件 | Android **7.0**，MT6735M，可直接 SP_Flash_Tool 刷写，机器上验证过 |
| 我们的核心功能 | F1–F8 的主体在**蓝牙原生栈**（A2DP Sink SBC/AAC/SBC-XQ、双流、AVRCP），Java 层改动较少 |
| 平台架构 | AOSP 蓝牙栈与芯片厂商解耦：`bluetooth.default`（平台侧）通过 **`libbt-vendor.so`**（MTK 私有，基线固件里已带）驱动 MT6625/6630 组合芯片。`bt_vendor_lib.h` 接口在 7.0/8.1 之间稳定 |

结论：**不必重建整个系统**——只把我们修改过的蓝牙模块编出来，换进官方 7.0
`system.img`，其余分区原样保留。这就是用户提出的"手动增量"思路在 7.0 上
真正可落地的形态。

## 2. 换件清单（预期）

| 文件（基线 system.img 内路径） | 来源 | 说明 |
|---|---|---|
| `/system/lib/hw/bluetooth.default.so` | 我们编译（7.0 源码 + 移植补丁） | 7.0 时代协议栈**整体**编进这一个 .so（无独立 libbluetooth.so） |
| `/system/lib/libbluetooth_jni.so` | 我们编译 | JNI 层；若 Java 层不改则可暂留基线原件（见 §5 R3） |
| `/system/app/Bluetooth/Bluetooth.apk`(+odex)（镜像内路径 `/app/Bluetooth/`） | 暂留基线原件 | 实测 APK 619,805B + odex 8.65MB；Java 层不动（§5 R3/R6） |
| `/system/lib/hw/audio.a2dp.default.so` | **B3 再定** | B1 实测：导出面仅 `HMI`+aeabi 符号、用 socket 通信，疑非 AOSP 标准 `audio_a2dp_hw`；移植栈 sink 音频优先直写 AudioTrack（MTK 已验证路径），本文件可能无需替换（§4.1） |
| AAC 解码 | **静态编入**我们的 .so | B1 实测：基线**无** `libFraunhoferAAC.so`（仅 stagefright 软解插件 `libstagefright_soft_aacdec.so`，栈不可直用）→ 7.0 树 `external/aac` 源码静态链进 `bluetooth.default.so`，不新增系统库 |
| MTK 私有件（preloader/lk/logo/secro/tee/libbt-vendor.so/…） | **原样保留** | 芯片适配层不动 |

## 3. 已验证的实验证据（2026-10-28）

用 `scripts/bt_standalone_build.sh` 在**无完整 AOSP 树**的环境里，把打完补丁的
r80 `system/bt` 全栈编译成了 ARM/bionic 目标码：

- 环境 = NDK r25c（clang 14，"based on r450784"，正是 8.1 同代工具链）
  + 11 个 AOSP 仓库的 blobless/sparse 头文件检出（共 ~90 MB 源码）
- 结果：**451/451 个 TU 全部编译通过**（system/bt 全目录 + external/aac +
  external/libldac，源文件清单与 Android.bp 的 glob 完全一致）
- 耗时/资源：2 核 / 1 GB RAM 沙箱约 10 分钟；标准 GitHub runner 完全可承载
  （`.github/workflows/bt-compile.yml` 每次 push 自动验证）
- 豁免（真实构建同样不编或需 build 期代码生成）：`hci_layer_android.cc`
  （HIDL 生成头）、`osi/metrics.cc`（protoc 生成头）、test/tools/vendor_libs/
  service/vnd 子树、aac 的 arm//mips/ 汇编目录
- 该实验同时**证伪了旧结论的一半**：`bluetooth.default` 的平台依赖
  （libchrome/libcutils/libaudioclient/libbinder…）全部是 *shared* 依赖，
  编译期只需头文件，链接期留给运行时——所以"只编蓝牙部分"成立

> 注意边界：这证明的是**可编译、可出 ELF**。8.1 的产物不能直接放进 7.0
> 系统（ABI 不匹配），所以可部署路线 = 把补丁移植到 7.0 源码再走同一套
> 编译方法，并用**从基线固件提取的真实 .so** 做链接期符号覆盖检查。

## 4. 里程碑

- **B0（已完成）** 独立编译环境验证：`bt_standalone_build.sh` + CI workflow。
- **B1 基线解剖（✅ 已完成，run 40630128218，真实官方固件）**：工具
  `scripts/baseline_probe.py` + `baseline-probe.yml`（纯 stdlib 的
  zip/sparse/ext4/ELF32 解析器，无需 root/loop-mount）。实测结果见 §4.1，
  **AOSP tag 定为 `android-7.0.0_r36`**（基线为 7.0 NRD90M，R5 解除）。
- **B2 补丁移植（✅ 已完成）**：把补丁从 r80 移植到 7.0 树
  （栈位置不同：`hardware/broadcom/bluetooth`；无 `a2dp_codecs` 框架，
  AAC sink 需要按 7.0 的 `btif_a2dp_sink`/`bta_av` 结构重写挂接点）。
  产出 `patches/nougat/` 系列 + harness 以 `--tag` 参数复验编译。
- **B3 真链接验证（✅ 已完成，run 41001545274）**：从基线 `system.img` 提取全部 DT_NEEDED .so，
  用它们（而非 stub）链接我们的模块；`llvm-nm` 对比未定义符号 ⊆ 基线导出
  符号。**链接零缺口 = 上机前最强的可行性证明**。
- **B4 镜像手术 + 重打包（✅ 已完成，run 41003456394 全绿，release
  `firmware-btswap-20261030`）**：实测记录见 §4.3。
- **B5 上机验证**（用户侧）：刷机、开关蓝牙、A2DP Sink SBC/AAC 连接出声、
  双设备（F6 受 7.0 单状态机限制，见 §5 R4）、延迟/稳定性按 R 指标抽测。

### 4.1 B1 实测结果（官方固件 `B04_35_1.5_8_3inch_M304_ST7701SN_20251016.zip`）

**版本指纹**（build.prop 实测）：

| 属性 | 值 | 结论 |
|---|---|---|
| ro.build.version.release / sdk | **7.0 / 24** | R5 解除：不是 7.1 |
| ro.build.id（fingerprint 内） | **NRD90M** user/release-keys | AOSP tag 基准 |
| ro.build.version.security_patch | 2019-05-05 | MTK 自行回合的补丁级别 |
| ro.mediatek.version.release | alps-mp-n0.mp1-V1.19_tek6735m.65.d.n_P133 | MTK N0.MP1 V1.19 树 |
| ro.board.platform / device / model | mt6735m / U2 / U2 | 与开发方案一致 |
| ro.build.date | 2025-10-16 | 近期用旧 MTK 树重出 |

**→ B2 checkout tag：`android-7.0.0_r36`**（7.0 系末代 tag，NRD90M 同系；
harness `--tag` 直接传参）。

**system.img 形态**：MTK 变体 sparse（block_size=4096，数据块标 0xCAC1、
空洞标 0xCAC3，共 5,463 chunk）→ 探针自动转 raw 3,095,396,352 B
（=2952 MiB，与 scatter 分区尺寸一致）；ext4：4096B 块、743,916 块、
23 组、2,493 文件/369 目录；lib 243 个 + vendor/lib 225 个。

**蓝牙部件清单**（替换目标 = 前 5 项；MTK 私有链不动）：

| 文件 | 大小 | 处置 |
|---|---|---|
| `/system/lib/hw/bluetooth.default.so` | 1,735,356（导出 805/未定义 259） | **换**：7.0 移植版合并栈 |
| `/system/lib/libbluetooth_jni.so` | 110,820 | 首选**不换**（仅经 `hw_get_module` 用 HAL，与栈零符号耦合）；`/app/Bluetooth/lib/arm/` 下是 31B 符号链接 |
| `/app/Bluetooth/Bluetooth.apk` + `oat/arm/*.odex` | 619,805 + 8.65M | **不换**（R3/R6 首选路径：Java 层不动，odex 保留） |
| `/system/lib/hw/audio.a2dp.default.so` | 30,364 | **B3 再定**：导出仅 `HMI`+aeabi 辅助符号、未定义含 `connect/send/setsockopt`，疑似 MTK 自定义而非 AOSP `audio_a2dp_hw` |
| `/system/lib/{libchrome,libprotobuf-cpp-full,libevent}.so` | 837,680 / 697,944 / 239,324 | **保留可用**：换入栈可直接 DT_NEEDED 基线已有库 |
| `/vendor/lib/libbt-vendor.so` → `libbluetooth_mtk.so` → `libnvram*` | 13,844 → 30,268 → … | **不动**：导出标准 `BLUETOOTH_VENDOR_LIB_INTERFACE`；固件下载（`GORM_FW_Init_Thread`/`BT_InitDevice`）与 NVRAM 全在链内自洽 |
| `/vendor/lib/libbluetooth{em_mtk,_relayer,_hw_test,_mtk_pure}.so` | 17–31K | **不动**：工程模式工具，全镜像 grep 无一导入 `BTA_/BTIF_/BTM_/GATT_` 栈符号 |
| `/system/etc/bluetooth/bt_stack.conf`、`bt_did.conf`、`/system/etc/a2dp_audio_policy_configuration.xml` | ~2–3K | 保留；B2 对照 MTK 特有键与移植栈的解析代码 |

**位宽**：`bluetooth.default.so` **只有 32 位版**（无 `/system/lib64/hw/`
对应物）；`/vendor/lib64/*` 实为与 `/vendor/lib/*` 同尺寸的 **ELF32 复制品**
（探针按 ELF32LE 解析成功）。→ 交付物只需 32 位（armv7a），与独立编译
harness 目标一致。

**ABI 决定性结论（R1 基本解除）**：
1. 全镜像没有任何库导入 `bluetooth.default.so` 的 805 个栈导出符号；JNI 层
   只经 `hw_get_module` 拿 HAL 结构体 → **替换只需保 HAL（`bt_interface_t`）
   + vendor dlopen 约定，栈导出面可自由变化**。
2. MTK 私有依赖集中在交互层：`libcustom_prop`（`property_get` 包装，与
   libcutils 同 ABI）、`libmedia`（`android::AudioTrack` 全套符号 →
   **MTK 栈的 sink 音频很可能直写 AudioTrack**）、`libstagefright`（DT_NEEDED
   在列、栈符号导入极少）。纯 AOSP 7.0 Fluoride 核心 + MTK 外挂，
   与"只换栈、vendor 链不动"的策略兼容。
3. **音频路径待办（B2/B3）**：基线 `audio.a2dp.default.so` 非 AOSP 标准形态，
   移植栈的 sink 输出二选一——(a) 学 MTK 直写 AudioTrack（基线已验证可行，
   无需动 audio HAL）；(b) 换 AOSP `audio_a2dp_hw` 版 HAL。优先 (a)，
   改动面最小且与保留的 audioserver/audio_policy 完全兼容。

**连接芯片**：MT6627 combo（`ROMv2_lm_patch_1_{0,1}_hdr.bin`、
`WIFI_RAM_CODE_6735`、`mt6627_fm_v1_*.bin`、`WMT_SOC.cfg` 均在
`/vendor/firmware/`）→ F10 WiFi/FM 用基线原件；BT 固件下载由不动的
`libbt-vendor` 链负责。

**产物位置**：CI run 40630128218 → artifact `baseline-probe-report`
（92KB：`report.json` + 19 库×导出/未定义符号表 37 份）、
`baseline-bt-extracted`（16.4MB：B3 真链接输入的全部 .so）。

### 4.2 B4 彩排实测（2026-10-28，run 40713604032）：8.1 产物直接被符号门否决

`swapfw.yml` 全流水线上机彩排（stream 解包 2.46 GiB 基线 system →
sparse→raw → loop-mount → 符号门 → …）。**boot 重打包链路全绿**：
`mtk_bootimg.py disable-verity` 在 CI 上与沙箱产出逐字节一致
（8,746,670 B，kernel md5 与原厂一致，fstab `verify` 旗标移除，CMS
testkey 重签）；确定性由 `--renumber-inodes` + 归档 mtime 恢复保证。

**符号门按设计拦截了 8.1 通用构建的 `bluetooth.default.so`**：

```
[gate] FAIL: NEEDED libs absent from baseline /system:
  ['android.hardware.bluetooth@1.0.so', 'libaudioclient.so',
   'libhidlbase.so', 'libhidltransport.so', 'libhwbinder.so',
   'libtinyxml2.so']
```

即 §3 注意事项的实锤：8.1（Treble 世代）产物 DT_NEEDED 了 HIDL 运行时
（`android.hardware.bluetooth@1.0` / `libhidl*` / `libhwbinder`）与 8.1 的
`libaudioclient`，这些在 7.0 基线 `/system` 里**不存在**，dlopen 必失败。
结论：**full 换芯只能用 B2（7.0 源码移植 + 独立编译）的产物**；`swapfw.yml`
的 full 模式保留符号门作为强制闸门，`bootonly` 模式（原厂 system +
verity-off 重签 boot）先行上机验证 lk/init 是否接受重签 boot。

### 4.3 B4 落地实录（2026-10-30）：栈产物 → swap 包 → 全绿发布

**栈产物（bt70 run 41001545274，commit e9867bf）**：
`bluetooth.default.so` 4,398,296 B，ELF32 ARM，443/443 TU；符号门 PASS
（missing=[]，strong_und=175）；DT_NEEDED 恰为 9 个基线 `/system/lib` 已有库
（liblog/libz/libdl/libcutils/libutils/libhardware/libmedia/libm/libc）；
tinyxml2 静态链入（hidden visibility，dynsym 0 条目）；codec 优先级
INT：LDAC > AAC > SBC。

**关键发现（HMI）**：AOSP HAL 入口宏 `HAL_MODULE_INFO_SYM` 展开后导出符号名
就是 `HMI`（`hardware.h:207`），stock `libbluetooth_jni.so` 以
`HAL_MODULE_INFO_SYM_AS_STR="HMI"` dlsym 之——产物验证必须查 `HMI` 而非宏字面量。

**swap 包（fw-btswap.yml，base=`firmware-debloat-v2-20261030`）**：
仅改 `/system/lib/hw/bluetooth.default.so`（cat 原地写，inode/mode/SELinux
xattr 保留：`u:object_r:system_file:s0`）；零填充空闲块（stock 栈残块不出货）；
e2fsck 清洁；img2simg 回稀疏（1,189,549,592 B < 3,095,396,352 B 分区）；
非 system 分区 10 项 md5 与 base 逐一相同；kernel md5 原厂、fstab verity-off。
发布：`U2_7.0_bt_sink_v0.zip` 618,511,945 B，md5 `9f60c1e8360ea883302b778025ea241b`
+ `VERIFICATION.md`（完整审计链）。

**首跑失败教训（已修）**：
1. run 41002423353 —— 断言 `com.android.phone` 必在：官方基线**本就不带**
   Telephony（BT 接收器产品；fw-debloat 日志早有 "not present in baseline
   (skipped)" 注记）。改为只断言基线实有包 + APK 记录数恰为 74（与 base 相同）。
2. run 41002971414 —— 1–18 步全绿仅 upload-artifact 失败：`cpio -idm` 保留
   ramdisk 归档权限位（部分目录 mode 000），runner 无法 scandir；验证后删除
   `pkgrd/`、`pkgboot/` 临时目录即愈。

**B5（用户侧）**：SP Flash Tool → zip 内 scatter → Download Only；开机后配对、
连手机音频，`dumpsys bluetooth_manager` / logcat `bta_av_co` 应见
`setconfig AAC` 或 `setconfig LDAC`（手机支持时）；救援 = 回刷 debloat-v2。

## 5. 风险与对策

| # | 风险 | 对策 |
|---|---|---|
| R1 | MTK 在 7.0 的 `bluetooth.default` 里有私有改动（非纯 AOSP） | **B1 已解除大半**：全镜像无库导入其 805 个栈导出符号，JNI 只走 `hw_get_module`；私有依赖集中在 `libcustom_prop`/`libmedia`(AudioTrack)/`libstagefright` 交互层。替换只需保 HAL + vendor dlopen 约定（§4.1） |
| R2 | SELinux 拒绝新栈的访问（7.0 策略是 MTK 定死的） | 进程名/域不变（`bluetooth`），大概率兼容；B5 用 `logcat -b events`/`dmesg` 抓 avc denial，必要时用 magisk 式策略补丁或退回 superuser 调试 |
| R3 | `Bluetooth.apk` 需 hidden-API framework（7.0） | 首选：Java 层不动（AAC sink 协商全在原生栈），只换 .so；确需改 APK 时，用基线 `framework.jar`（dex2jar）+ 7.0 源码树编译 |
| R4 | F6 双设备在 7.0 与 8.1 同样受单 A2DP 状态机限制 | 与 8.1 路线一致：交付设备感知 StreamHandler 设计（`patches/MULTIDEVICE.md`），7.0 先落地 F1–F5、F7–F8 |
| R5 | 基线是 7.1 而非 7.0 | **B1 已解除**：build.prop 实测 7.0/SDK24/NRD90M → tag `android-7.0.0_r36`（§4.1） |
| R6 | 换件后 `dex2oat`/odex 失效导致 APK 不启动 | 保留原 odex 则必须不改 APK（R3 首选路径自洽）；改 APK 就删对应 odex 让 ART 重编 |

## 6. 与既有产物的关系

- `scripts/bt_standalone_build.sh --tag <7.0 tag>`：B2 的编译验证直接复用。
- `scripts/repack_firmware.py` / `mtk_fw_zip.py` / `mtk_scatter.py`：B4 直接复用
  （scatter 已按官方文件逐字节保留）。
- `build.yml`（完整 8.1）：保留，等算力到位后仍是"真 8.1 固件"的正路。
