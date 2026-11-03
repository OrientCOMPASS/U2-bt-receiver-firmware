# docs/00 — 设备事实（U2 手机，实测，勿凭记忆）

> 所有条目均由 `adb shell` / root executor 实测取得（2026-11-03 ~ 11-04）。
> 改动固件后必须复核的条目以 **[需复核]** 标注。

## 1. 型号与系统

| 项 | 值 |
|---|---|
| 产品名 / 设备名 | `U2` |
| 平台 | `MT6735`（32 位 armv7-a 用户态；`ro.product.cpu.abilist=armeabi-v7a,armeabi`） |
| 内核 | `3.18.35+`，**aarch64**（`uname -m=aarch64`）→ **32 位进程跑在 64 位内核上**，我们的 `.so` 是 32 位 ARM（正确） |
| Android | `7.0`，`ro.build.version.sdk=24`，`ro.build.version.release=7.0` |
| Build ID / 指纹 | `NRD90M` / `alps/full_tek6735m_65_d_n:7.0/NRD90M/V1558990489:user/release-keys` |
| Build 类型 | `user` + **`ro.secure=0`、`ro.debuggable=1`、`ro.adb.secure=0`**（工程机风格） |
| Bootloader | `ro.boot.flash.locked=1`（**锁 BL**；`ro.boot.verifiedbootstate=green`） |
| Boot banner | `alps-mp-n0.mp8-V6.119_6735_66.n_n_alps-mp-n0.mp8-V6.119`（任务书写 V7，**实测 V6.119**，以实测为准） |
| 安全补丁 | `ro.build.version.security_patch=2017-09-05` |
| SELinux | `enforcing`，但 **shell 域 permissive**（`u:r:shell:s0`） |
| 内存 | `MemTotal ≈ 990 MB` |
| 分区 | `/system` = `mmcblk0p20`，ext4，可 `mount -o rw,remount` |
| 时钟 | **系统时钟不可信**（logcat 里混杂 2015-01-01 / 2026-11-04）；排序按 pid，勿按 mtime |

## 2. 蓝牙适配器 [需复核]

| 项 | 值 |
|---|---|
| 地址 | `00:00:46:03:35:01`（`bt_config.conf` 与运行时一致） |
| 名称 | `U2` |
| 扫描模式 | `bt_config.conf` 里 `ScanMode=0`；**但 Java 每次都会下发"只可连接"**（实测 `disc=0x0 conn=0x1`）→ P1b 起由我们的 `.so` 强制顶回 `0x03`（inquiry+page scan） |
| COD | 原厂 `bta_dm_cfg.dev_class = {0x5A,0x02,0x0C}` → **0x5A020C（手机）**；P1b 起改为 `{0x24,0x04,0x14}` → **0x240414（Audio/Video · Loudspeaker）**，SDP 记录建立后 Fluoride 会再或上 Object Transfer 位变成 `0x340414`（外观差异，主/次类不变） |
| DID | `vendorId=0x0046`、`productId=0x1200`、`vendorIdSource=0x0002`(USB-IF) |
| Vendor HAL | `/vendor/lib/libbt-vendor.so`(13844B) + `/vendor/lib/libbluetooth_mtk.so`(30268B) |
| BT 固件 | `/system/etc/firmware/mt6627_patch_e1_hdr.bin`（`ro.bt.chip=mt6627`）；MTK FW log `C1/C2` |
| HAL 模块 | `/system/lib/hw/bluetooth.default.so`（唯一 HAL 实现，`bluetooth` module id） |
| APK | `/system/app/Bluetooth/Bluetooth.apk`，`com.android.bluetooth`（**另有 `Bluetooth.odex` 8.6MB**） |
| 其他 BT 包 | `com.mediatek.bluetooth.dtt`、`com.android.bluetoothmidiservice` |

## 3. 原厂 `bluetooth.default.so`（P0 前） [需复核]

- md5 `e0dd373e4632316a0348e6ec0a288347`，1735356 B
- 备份：设备 `/data/local/tmp/bt_stock_backup.so`；Windows `C:\u2\bt_stock.so`；沙箱 `devlibs/bt_stock.so`
- 关键结论：**原厂 native 层已编入 A2DP SINK**（未定义符号里出现 `AudioTrack` 相关构造/析构、`_ZN7android10AudioTrackC1E…`），即 MTK 构建开了 `BTA_AV_SINK_INCLUDED=TRUE`；但 **Java APK 从不调用 sink 路径**。
- DT_NEEDED：`libcutils.so libdl.so liblog.so libz.so libpower.so libprotobuf-cpp-full.so libmedia.so libutils.so libchrome.so libc++.so libc.so libm.so`
- Thumb-2（MTK 库普遍是 `-mthumb`）→ 反汇编需 `llvm-objdump --triple=thumbv7a-none-linux-androideabi`
- 符号化偏移换算：**bias = exec load base − 0x70000**

## 4. Java 层 Profile 现状（P0 实测） [需复核]

`dumpsys bluetooth_manager` 里 **恰好 7 个 profile，全部是 source/host 侧**：

```
Gatt, Headset, A2dp(=A2DP SRC), Hid, Health, Pan, Map
```

- `grep -i -e sink -e A2dpSink -e AvrcpController -e HfpClient` → **0 命中**
- 即：**没有任何 sink/CT 侧 profile 被启用**。P1 的 sink 能力必须由我们在 native 侧主动注册（见 docs/05）。

## 5. 蓝牙配置 [需复核]

- `/system/etc/bluetooth/bt_stack.conf`：`BtSnoopLogOutput=false`、`TraceConf=true`、`TRC_*=2`（仅到 WARNING）
- `/system/etc/bluetooth/bt_stack.conf.debug`：`TRC_*=6` + MTK FW log C1/C2（**调试期用它的内容覆盖，或直接改 TRC_***）
- snoop 落盘目录：`/sdcard/mtklog/btlog/btsnoop_hci.log`（MTK）
- A2DP 编解码偏好：原厂只有 SBC/AAC 声明，**无 LDAC**（R3 要自己加）
- **`getprop log.tag` = `E`**（全机日志最低优先级被压到 ERROR）→ 我们自己的 `LOG_INFO/LOG_WARN`
  默认**看不见**；调试前必须 `setprop log.tag.<自己的TAG> V`（详见 docs/04 C12）
- **btsnoop 落盘**（P1b 起已打开，是验证射频侧参数的主要手段，不需要第二台蓝牙设备）：
  - `BtSnoopLogOutput=true`
  - `BtSnoopFileName` 原厂写的是 `/sdcard/mtklog/btlog/btsnoop_hci.log`，**该目录不存在** → 必须改成
    AOSP 默认 `/data/misc/bluedroid/btsnoop_hci.log`（详见 docs/04 C13）
  - conf 备份在设备 `/data/local/tmp/bt_stack.conf.stock`（md5 `6d85e7288d03525a7b60e8162b72b39d`）
  - 取文件：`scripts/device/dump_snoop.sh`（root cp 到 `/data/local/tmp` + chmod 666）→
    Windows `adb pull` → tar → uguu → 沙箱 `tools/dev/parse_snoop.py`

## 6. 常用命令速查（本机实测可用）

| 目的 | 命令 |
|---|---|
| 开蓝牙 | `service call bluetooth_manager 6`（`svc bluetooth enable` **不支持**） |
| 关蓝牙 | `service call bluetooth_manager 8` |
| 看状态 | `dumpsys bluetooth_manager`；`settings get global bluetooth_on` |
| 干净重启 BT | **`adb reboot`**（反复 killall 后适配器可能不回 STATE_ON） |
| remount /system | `mount -o rw,remount /system`（root executor 里执行） |
| 原子替换 .so | `cp new /system/lib/hw/.bt_swap.tmp && mv /system/lib/hw/.bt_swap.tmp /system/lib/hw/bluetooth.default.so && sync` |
| 崩溃取证 | `ls -l /data/tombstones/`、`logcat -b crash`、`dmesg` |
| 设备类(COD) | 见 `bta/dm/bta_dm_cfg.c` → `bta_dm_cfg.dev_class` → `BTM_SetDeviceClass` |
