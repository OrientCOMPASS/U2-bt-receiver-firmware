# Nougat BT Swap — 在官方 7.0 固件上落地自研蓝牙栈

> 状态：**计划已立项，实验证据充分**（见 §3）。本文档是"7.0 换芯"路线的
> 权威计划；8.1 完整固件路线（`build.yml` + `docs/FIRMWARE_BUILD.md`）继续保留，
> 两条路线互不冲突。

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
| `/system/app/Bluetooth/Bluetooth.apk`(+odex) | 我们编译或暂留基线 | 需要 hidden-API framework；见 §5 R3 |
| `/system/lib/hw/audio.a2dp.default.so` | 我们编译 | A2DP 音频 HAL |
| `/system/lib/libFraunhoferAAC.so` 或静态编入 | external/aac @7.0 | AAC 解码依赖；先确认基线是否已带（B2 步骤） |
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
- **B1 基线解剖**（工具已就绪：`scripts/baseline_probe.py` + `baseline-probe.yml`，
  纯 stdlib 的 zip/ext4/ELF32 解析器，无需 root/loop-mount）：产出
  `build.prop` 精确版本、蓝牙文件清单、`bluetooth.default.so` 的
  DT_NEEDED/符号表、依赖闭包库全量提取（B3 真链接的输入）。据此确定 AOSP
  源码 tag（如 `android-7.0.0_r36`）。
- **B2 补丁移植**：把 4+1 个补丁从 r80 移植到 7.0 树
  （栈位置不同：`hardware/broadcom/bluetooth`；无 `a2dp_codecs` 框架，
  AAC sink 需要按 7.0 的 `btif_a2dp_sink`/`bta_av` 结构重写挂接点）。
  产出 `patches/nougat/` 系列 + harness 以 `--tag` 参数复验编译。
- **B3 真链接验证**：从基线 `system.img` 提取全部 DT_NEEDED .so，
  用它们（而非 stub）链接我们的模块；`llvm-nm` 对比未定义符号 ⊆ 基线导出
  符号。**链接零缺口 = 上机前最强的可行性证明**。
- **B4 镜像手术 + 重打包**：CI（root runner）loop-mount `system.img`，替换
  §2 清单文件，恢复权限/SELinux xattr，容量校验（≤2952 MiB 分区），走既有
  `repack_firmware.py` + 官方 scatter → SP_Flash_Tool 可刷 zip → GitHub Release。
- **B5 上机验证**（用户侧）：刷机、开关蓝牙、A2DP Sink SBC/AAC 连接出声、
  双设备（F6 受 7.0 单状态机限制，见 §5 R4）、延迟/稳定性按 R 指标抽测。

## 5. 风险与对策

| # | 风险 | 对策 |
|---|---|---|
| R1 | MTK 在 7.0 的 `bluetooth.default` 里有私有改动（非纯 AOSP） | B1 对比基线 .so 与纯 AOSP 7.0 编译产物的符号/字符串差异；差异集中在 vendor 交互层则安全，若涉栈内部则评估保留 MTK 版 HAL 外壳、只换栈对象 |
| R2 | SELinux 拒绝新栈的访问（7.0 策略是 MTK 定死的） | 进程名/域不变（`bluetooth`），大概率兼容；B5 用 `logcat -b events`/`dmesg` 抓 avc denial，必要时用 magisk 式策略补丁或退回 superuser 调试 |
| R3 | `Bluetooth.apk` 需 hidden-API framework（7.0） | 首选：Java 层不动（AAC sink 协商全在原生栈），只换 .so；确需改 APK 时，用基线 `framework.jar`（dex2jar）+ 7.0 源码树编译 |
| R4 | F6 双设备在 7.0 与 8.1 同样受单 A2DP 状态机限制 | 与 8.1 路线一致：交付设备感知 StreamHandler 设计（`patches/MULTIDEVICE.md`），7.0 先落地 F1–F5、F7–F8 |
| R5 | 基线是 7.1 而非 7.0 | B1 先读 `build.prop` 再定 tag，harness `--tag` 已参数化 |
| R6 | 换件后 `dex2oat`/odex 失效导致 APK 不启动 | 保留原 odex 则必须不改 APK（R3 首选路径自洽）；改 APK 就删对应 odex 让 ART 重编 |

## 6. 与既有产物的关系

- `scripts/bt_standalone_build.sh --tag <7.0 tag>`：B2 的编译验证直接复用。
- `scripts/repack_firmware.py` / `mtk_fw_zip.py` / `mtk_scatter.py`：B4 直接复用
  （scatter 已按官方文件逐字节保留）。
- `build.yml`（完整 8.1）：保留，等算力到位后仍是"真 8.1 固件"的正路。
