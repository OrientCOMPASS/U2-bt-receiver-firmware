# KeenPlus U2 自研固件开发方案
## 基于 AOSP 8.1 · 蓝牙音频接收端 · 极低资源占用

| | |
|---|---|
| 文档版本 | v1.0 |
| 日期 | 2026-10-24 |
| 基线固件 | `B04_35_1.5_8_3inch_M304_ST7701SN_20251016.zip`<br>SHA256 `5d88c4b1f37053731afb6c16ede12aa2fb612f5a63ff26f792f911670e16b804`<br>1,915,550,646 B · 12 个条目 · 解压后 2.54 GiB |
| 目标平台 | AOSP **8.1**（`android-8.1.0_r80` 或同系稳定 tag），自编、自签、非 Treble |
| 文档性质 | **唯一交付文档**，自包含。所有"实测"字样表示已从基线固件二进制中验证 |

---

# 第一部分 · 基线固件现状

## 1. 硬件事实（全部实测）

| 项 | 值 | 来源 |
|---|---|---|
| SoC | **MT6735M**（Denali-2，4×Cortex-A53 @1.0 GHz，ARMv8） | `ro.board.platform=mt6735m`、DTB `compatible="mediatek,MT6735M"`、scatter `platform: MT6735M` |
| 项目代号 | **`tek6735m_65_d_n`** | scatter `project:`、preloader 文件名 |
| GPU | **Mali-T720 MP1**，内核驱动 `midgard`，DDK **`r12p1-01alp0`** | 内核字符串 ×170 |
| 内核 | **Linux 3.18.35**，`(ts01@blueshark02)`，**gcc 4.9 20150123 (prerelease)**，`#1 SMP PREEMPT Thu Oct 16 17:30:28 CST 2025` | boot.img |
| 内核布局 | gzip Image（6,409,360 B 区段）+ **appended DTB**（63,240 B，FDT v17，位于 boot.img 偏移 **6,348,168**），解压后 14.85 MiB | 实测 |
| 位数 | **64 位内核 + `ro.zygote=zygote64_32`**（cmdline `bootopt=64S3,32N2,64N2`）；`/system` 同时含 `lib` 与 `lib64` | boot header / default.prop |
| 面板 | **3.0" ST7701SN**，DSI **video mode**，BOE 屏；内核 LCM 驱动名 **`st7701s_fw_dsi_vdo_boe`**（内核中唯一的面板） | 文件名 + 内核字符串 + DTB |
| 显示时序 | DTB 内含完整 `lcm_params-dsi-CLK_HS_EXIT / CLK_HS_POST / CLK_HS_PRPR / CLK_TRAIL / CLK_ZERO / CONT_DET / DA_HS_EXIT / DSI_RMEM_CONTI` 等属性 | DTB |
| **BT/Wi-Fi combo** | **MT6627**（BT **4.0** + Wi-Fi 802.11 b/g/n **单频 2.4G** + GPS + FM） | `/system/etc/firmware/`：`ROMv2_lm_patch_1_0_hdr.bin`、`ROMv2_lm_patch_1_1_hdr.bin`、`mt6627_fm_v1_coeff.bin`、`mt6627_fm_v1_patch.bin`、`WIFI_RAM_CODE_6735`、`WMT_SOC.cfg` |
| 蓝牙传输 | **STP over UART**：节点 `/dev/stpbt`（0660 bluetooth net_bt_stack），配套 `/dev/stpwmt`、`/dev/stpgps`、`/dev/wmtWifi`、`/dev/fm`、`/dev/ttyMT2` | `ueventd.mt6735.rc` |
| WMT 服务 | `wmt_loader`（oneshot）+ `wmt_launcher -p /vendor/firmware/` + `stp_dump3` | `init.connectivity.rc` |
| PMIC | **mt6311 + mt6328** | 内核字符串 ×48 |
| 音频 | `audio.primary.mt6735m.so`（1,075,328 B）；**I2S1 引到 GPIO**（DTB `i2s1clk/dat/mclk/ws-gpio`） | vendor/lib/hw + DTB |
| 存储 | 8 GB eMMC，`boot_channel: MSDC_0`，`block_size: 0x20000` | scatter |
| 内存 | **1.5 GB**；`bs.fake.ram=1.5`、`bs.fake.rom=8`（厂商伪造上报值） | default.prop |
| zram | ramdisk 有 `enableswap.sh`，但内容是 `echo 0 > /sys/block/zram0/disksize` → **disksize 被设为 0，等于没启用** | ramdisk |
| TF 卡 | `msdc1` + `msdc1_ins`（插卡检测），`voldmanaged=sdcard1:auto,encryptable=userdata` | DTB / fstab |
| USB | `msdc0`/`msdc1` + `mt_usb`（`voldmanaged=usbotg:auto`），`persist.sys.usb.config=mtp` | fstab / default.prop |

## 2. 软件现状

```properties
ro.build.version.release = 7.0          ro.build.version.sdk = 24
ro.build.version.incremental = 1760605825
ro.build.date = 2025年 10月 16日 星期四 17:10:24 CST      ro.build.date.utc = 1760605824
ro.build.fingerprint = alps/full_tek6735m_65_d_n/tek6735m_65_d_n:7.0/NRD90M/1760605825:user/release-keys
ro.build.display.id = B04_35_1.5_8_3inch_M304_ST7701SN_20251016
ro.product.model / name / device = U2      ro.product.manufacturer = alps
ro.mediatek.version.branch  = alps-mp-n0.mp1                    # N0 = Nougat BSP
ro.mediatek.version.release = alps-mp-n0.mp1-V1.19_tek6735m.65.d.n_P133
ro.mediatek.chip_ver = S01      ro.mediatek.version.sdk = 4
ro.secure=1   ro.adb.secure=1   ro.debuggable=0   ro.allow.mock.location=0
ro.oem_unlock_supported = 1                                      # ★ 支持 OEM 解锁
ro.zygote = zygote64_32      ro.mount.fs = EXT4
ro.mtkrc.path = /vendor/etc/init/hw/
persist.sys.usb.config = mtp      persist.sys.timezone = Asia/Shanghai
persist.sys.install.by.pwd = 1    camera.disable_zsl_mode = 1
ro.mtk_audio_alac_support / ape_support（见 build.prop）
dalvik.vm.dex2oat-Xmx=512m   dalvik.vm.image-dex2oat-Xmx=64m
dalvik.vm.usejit=true  usejitprofiles=true  appimageformat=lz4  dexopt.secondary=true
pm.dexopt.first-boot=interpret-only  boot=verify-profile  install=interpret-only
```

- **非 Treble**：无 vendor 分区、无 odmdtbo；vendor 内容位于 `/system/vendor/`；
  `BOOTCLASSPATH` 中**没有** `android.hidl.*-V1.0-java.jar`。
- **dm-verity + FDE 均开启**：`fstab.mt6735` 中
  `/system … ext4 ro wait,verify`、
  `/data … ext4 noatime,nosuid,nodev,noauto_da_alloc,discard wait,check,resize,forceencrypt=/dev/block/platform/mtk-msdc.0/11230000.msdc0/by-name/metadata`；
  ramdisk 内含 `verity_key`。
- **HAL 形态是 legacy `hw_module_t`**（非 HIDL）：`/system/vendor/lib/hw/` 下是
  `audio.primary.mt6735m.so`、`hwcomposer.mt6735.so`、`gralloc.mt6735m.so`、`camera.mt6735m.so`、
  `gps.mt6735m.so`、`lights.mt6735m.so`、`sensors.mt6735m.so`、`memtrack.mt6737m.so`、
  `keystore/gatekeeper.mt6735m.so`、`power.mt6735m.so`、`radio.fm.mt6737m.so`、`flp.mt6737m.so`、
  `audio.usb/r_submix.mt6735m.so`；`/system/lib/hw/` 下是 `bluetooth.default.so`、
  `audio.a2dp.default.so`、`audio_policy.default.so`、`gralloc.default.so`、`keystore.default.so` 等。
- **蓝牙栈形态**：`/system/lib/hw/bluetooth.default.so`（1,735,356 B）+ `/system/lib/libbluetooth_jni.so`（110,820 B）
  + `/system/vendor/lib/libbt-vendor.so`（16,076 B）/ `libbluetooth_mtk.so`（30,268 B）/
  `libbluetooth_mtk_pure.so` / `libbluetooth_relayer.so` / `libbluetoothem_mtk.so`。
  即 **pre-Treble 的 `bt_vendor_interface_t` 形态**（不是 HIDL `IBluetoothHci`）。
- **蓝牙应用**：`/system/app/Bluetooth/Bluetooth.apk`（619,805 B，**AOSP 版**，dex-less）
  + `oat/arm/Bluetooth.odex`（8,651,232 B，ELF32）。odex 内含包路径
  `a2dp a2dpsink avrcp bip btservice dun gatt hdp hfp hfpclient hid map mapapi opp pan pbap pbapclient sap sdp util`，
  以及 `A2dpSinkService`×14、`A2dpSinkStateMachine`×8、`AvrcpControllerService`×16、`HeadsetClientService`×9。
  `resources.arsc`（536,164 B）含 `profile_supported_a2dp_sink`、`a2dp_sink_duck_percent`。

### 2.1 当前蓝牙音频能力（实测符号扫描）

| 能力 | 现状 | 证据 |
|---|---|---|
| A2DP Source（发射） | ✅ 完整 | `bta_av_co_*` ×75、`SBC_Encoder_Init`、`aacEnc*` ×207（Fraunhofer 编码器静态链入） |
| A2DP Sink 角色注册 | ✅ 在 | `init_sink`/`cleanup_sink`、`bt_av_sink_callbacks`、`btif_av_get_sink_interface`、`btif_media_sink_enque_buf`、`bta_av_audio_sink_getconfig`、`bta_av_co_audio_sink_supports_config`、`AVDT_SINK_A/D`、`BTIF_AV_SINK_CONFIG_REQ_EVT` |
| **Sink 解码（SBC）** | ❌ **缺失** | `OI_CODEC_SBC_DecodeFrame/DecoderReset` ×0；连日志串 `"OI_CODEC_SBC_DecoderReset failed"` 都不在二进制内 → 该代码块被预处理器裁掉 |
| **Sink PCM 输出** | ❌ **缺失** | `BtifAvrcpAudioTrack*` ×0；`UIPC_*` ×27（走的是 UIPC 转发路径） |
| AAC 解码 | ❌ | `aacDec*` / `aacDecoder_*` ×0 |
| LDAC | ❌ 完全没有 | 7.0 的 AOSP 不含 LDAC（8.0 才引入） |
| aptX | ❌ | 仅 `aptx_config` 一个字符串，无库 |
| AVDTP MTU | 未知 | 有 `bta_av_chk_mtu`、`cmtu`、`minmtu`、`TxAaMtuSize`、`port_select_mtu`、`stream_mtu`、`peer_mtu`、`configure_mtu_cb`（共 53 处），但 `bt_stack.conf` 里无 MTU 配置项 → 用编译期默认值 |

**根因**（AOSP 7.0 源码核对）：
- `btif/src/btif_media_task.c`（3526 行）中，sink 由 `#if (BTA_AV_SINK_INCLUDED == TRUE)` 控制（12 处），
  PCM 输出方式由 **`#ifdef USE_AUDIO_TRACK`（15 处）** 控制：
  定义 → 走 `BtifAvrcpAudioTrack*`（AudioTrack 直出）；未定义 → 走 UIPC 转发给 audio HAL。
- 本机 **未定义 `USE_AUDIO_TRACK`**，而 UIPC 路径需要 audio HAL 侧配合，
  但 `/system/vendor/lib/hw/` 里**没有 `audio.a2dp.mt6735m.so`**，
  `/system/lib/hw/audio.a2dp.default.so` 只有 30,364 B 且无任何符号（AOSP 薄封装）→ **UIPC 路径是死的**。
- 于是 sink 的解码段被裁掉，静态库 `libbt-qcom_sbc_decoder`
  （AOSP 7.0 `embdrv/sbc/decoder/Android.mk` 定义，14 个源文件，`main/Android.mk` 已链接）
  因无引用被 `--gc-sections` 回收。
- 结论：**基线固件的 A2DP Sink 是不可用的**，Java/JNI 层虽齐全（`A2dpSinkServiceJni`、`connectA2dpNative`、
  `informAudioFocusStateNative`、`informAudioTrackGainNative` 都在 `libbluetooth_jni.so` 里），但 native 媒体链路断裂。

### 2.2 音频通路配置（已就位，可复用）
`/system/etc/a2dp_audio_policy_configuration.xml`（2,220 B）已声明：
```xml
<mixPort name="a2dp input" role="sink">
    <profile format="AUDIO_FORMAT_PCM_16_BIT" samplingRates="44100"
             channelMasks="AUDIO_CHANNEL_IN_STEREO"/>
</mixPort>
<devicePort tagName="BT A2DP In" type="AUDIO_DEVICE_IN_BLUETOOTH_A2DP" role="source">
    <profile format="AUDIO_FORMAT_PCM_16_BIT" samplingRates="44100"
             channelMasks="AUDIO_CHANNEL_IN_STEREO"/>
</devicePort>
<route type="mix" sink="a2dp input" sources="BT A2DP In"/>
```
并由 `/system/etc/audio_policy_configuration.xml` 第 204 行 `<xi:include href="a2dp_audio_policy_configuration.xml"/>` 引入。
> ⚠️ profile 只写了 `samplingRates="44100"`。做 AAC sink 时若协商到 48 kHz，
> 必须改成 `44100|48000`，否则会走 AudioFlinger 重采样（增加延迟）或被拒。

### 2.3 蓝牙配置文件与调试钩子
`/system/etc/bluetooth/`：`bt_stack.conf`(2,639 B)、`bt_stack.conf.debug`、`bt_stack.conf.sqc`、`bt_did.conf`(2,637 B)
```ini
# bt_stack.conf 非注释部分
BtSnoopLogOutput=false
BtSnoopFileName=/sdcard/mtklog/btlog/btsnoop_hci.log
BtSnoopSaveLog=false
TraceConf=true
TRC_BTM=2 TRC_HCI=2 TRC_L2CAP=2 TRC_RFCOMM=2 TRC_OBEX=2 TRC_AVCT=2 TRC_AVDT=2
TRC_AVRC=2 TRC_AVDT_SCB=2 TRC_AVDT_CCB=2 TRC_A2D=2 TRC_SDP=2 TRC_GATT=2 TRC_SMP=2
TRC_BTAPP=2 TRC_BTIF=2 TRC_GAP=2 TRC_BNEP=2 TRC_PAN=2
[MtkBtConf]
MtkStackConfigOverride=/sdcard/btsc        # ★ MTK 运行时配置覆盖钩子（免重刷改栈配置）
[MtkBtFWLog]
C1 = 01 BE FC 01 00      # 厂商 HCI：关控制器固件日志（改 05 = 开）
C2 = 01 5F FC 2A 50 01 09 00 00 00
C201..C209 = 00 00 00 00 # 分组过滤：SYS/TIM/PKV/LC/LL/ANT/LMP/CO/VOICE
```

## 3. boot.img 精确参数（自编 boot.img 必须逐项照抄）

```
magic            ANDROID!
page_size        2048                        ← BOARD_KERNEL_PAGESIZE
header_version   0x0e000135（未初始化垃圾值 → 按 v0 处理）
kernel_size      6,409,360     kernel_addr  0x40080000   → kernel_off  0x00000000
ramdisk_size     2,340,058     ramdisk_addr 0x44000000   → ramdisk_off 0x03F80000
second_size      0             second_addr  0x40F00000   → second_off  0x00E80000
tags_addr        0x4E000000                             → tags_off    0x0DF80000
dt_size          0             recovery_dtbo_size 0      header_size 0
name[16]         "1760605825"               ← 构建时间戳（= ro.build.version.incremental）
cmdline[512]     "bootopt=64S3,32N2,64N2"   ← 注意：无 buildvariant=
extra_cmdline    ""
```

**ramdisk**（gzip cpio，解压后 5,614,080 B，63 个条目）关键内容：
```
default.prop  fstab.mt6735  enableswap.sh  verity_key  sepolicy
init  init.rc  init.environ.rc  init.mt6735.rc  init.mt6735.usb.rc
init.project.rc  init.connectivity.rc  init.aee.rc  init.modem.rc
init.rilproxy.rc  init.epdg.rc  init.microtrust.rc  init.common_svc.rc
init.usb.rc  init.usb.configfs.rc  init.recovery.mt6735.rc  multi_init.rc
init.zygote32.rc  init.zygote64_32.rc
factory_init.rc factory_init.project.rc factory_init.connectivity.rc factory_init.usb.rc
meta_init.rc meta_init.project.rc meta_init.connectivity.rc meta_init.modem.rc
ueventd.rc  ueventd.mt6735.rc
file_contexts.bin  property_contexts  seapp_contexts  selinux_version
sbin/{adbd,healthd,ueventd,watchdogd,fuelgauged_static,multi_init}  charger
```
`fstab.mt6735` 的分区映射（含 `verify` 与 `forceencrypt`，见 §2）。

## 4. 分区表（scatter 实测，26 个分区）

| # | 分区 | 大小 | 起始 | 文件 | region / operation |
|---|---|---|---|---|---|
| SYS0 | **preloader** | 256 KiB | 0x0 | `preloader_tek6735m_65_d_n.bin`(121,072 B) | **EMMC_BOOT_1** / BOOTLOADERS |
| SYS1 | pgpt | 512 KiB | 0x0 | — | EMMC_USER / INVISIBLE |
| SYS2 | **proinfo** | 3 MiB | 0x80000 | — | PROTECTED（校准） |
| SYS3 | **nvram** | 5 MiB | 0x380000 | — | BINREGION（校准） |
| SYS4 | protect1 | 10 MiB | 0x880000 | — | PROTECTED |
| SYS5 | protect2 | 10 MiB | 0x1280000 | — | PROTECTED |
| SYS6 | **lk** | 512 KiB | 0x1c80000 | **`lk.bin`（裸 bin，353,344 B）** | UPDATE，`empty_boot_needed: true` |
| SYS7 | para | 512 KiB | 0x1d00000 | — | INVISIBLE（= /misc） |
| SYS8 | **boot** | **16 MiB** | 0x1d80000 | `boot.img`(8,754,472 B) | UPDATE（余量 ~7.6 MiB） |
| SYS9 | recovery | 16 MiB | 0x2d80000 | `recovery.img`(10,745,132 B) | UPDATE |
| SYS10 | **logo** | 8 MiB | 0x3d80000 | `logo.bin`(**571,232 B**) | UPDATE |
| SYS11 | **expdb** | 10 MiB | 0x4580000 | — | INVISIBLE（**异常 last log，调试必读**） |
| SYS12 | seccfg | 512 KiB | 0x4f80000 | — | INVISIBLE |
| SYS13 | oemkeystore | 2 MiB | 0x5000000 | — | INVISIBLE |
| SYS14 | secro | 6 MiB | 0x5200000 | `secro.img`(135,168 B) | UPDATE |
| SYS15 | keystore | 8 MiB | 0x5800000 | — | INVISIBLE |
| SYS16 | **tee1** | 5 MiB | 0x6000000 | **`trustzone.bin`(169,984 B)** | UPDATE |
| SYS17 | **tee2** | 5 MiB | 0x6500000 | `trustzone.bin` | UPDATE |
| SYS18 | frp | 1 MiB | 0x6a00000 | — | INVISIBLE |
| SYS19 | nvdata | 32 MiB | 0x6b00000 | — | INVISIBLE |
| SYS20 | metadata | 37 MiB | 0x8b00000 | — | INVISIBLE（FDE 密钥） |
| SYS21 | **system** | **0xB8800000 = 2952 MiB** | 0xb000000 | `system.img` | UPDATE |
| SYS22 | cache | 0x19000000 = 400 MiB | 0xc3800000 | `cache.img` | UPDATE |
| SYS23 | **userdata** | **0xC0000000 = 3 GiB** | 0xdc800000 | `userdata.img` | UPDATE |
| SYS24 | flashinfo | 16 MiB | 0xFFFF0084 | — | RESERVED |
| SYS25 | sgpt | 512 KiB | 0xFFFF0004 | — | RESERVED |

**无 vendor、无 odmdtbo、lk 与 tee 各只有一份**（tee 有 tee1/tee2 两份内容相同）。

## 5. `/system` 内容清点（实测 2,471 个文件，合计 **2,197 MiB**）

| 目录 | 文件数 | 体积 | 主要内容与裁剪判断 |
|---|---|---|---|
| **`/preload`** | 15 | **992.96 MiB** | **全部是预装第三方 APK（`/preload/hotapps/`）→ 100% 删除**：<br>网易云音乐 184.91、QQ音乐 177.64、喜马拉雅 87.74、欧路词典 78.39、哔哩哔哩 72.66、快看漫画 64.55、酷狗 58.13、西瓜视频 55.06、百词斩 45.88、七猫免费小说 40.20、番茄小说 40.19、爱奇艺 31.63、京东天下 28.14、快压 16.61、点点复读机 11.21（MiB） |
| `/framework` | 162 | 229.04 MiB | framework/services/mediatek-*.jar + boot image + odex → 裁 telephony/ims 相关 jar |
| **`/app`** | 64 | 181.47 MiB | `webview/webview.apk` **99.99**、`LatinIME` 16.27、`BsHotApps` 1.09(+11.92 odex)、`Gallery2` 4.05(+11.33)、`DeskClock` 2.09(+8.99)、`Bluetooth` 0.6(+8.25)、`PrintSpooler`(+1.73)、`EasterEgg`(+1.05) |
| **`/media`** | 204 | 145.19 MiB | **`1080p.avi` 128.34 MiB（演示视频）→ 删**；`bootanimation.zip` 4.42、`shutanimation.zip` 4.42 → 重做；`Test.ogg` 0.58；铃声 |
| `/vendor/app` | 39 | 119.43 MiB | **`MiraVision` 57.21(+3.59 odex) → 删**；`EngineerMode` 0.61(+12.99 arm64 odex)；`Camera` 6.52(+9.85)；`Exchange2` 0.20(+8.70)；`MtkCalendar` 1.42(+3.83)；`BtTool` 0.28(+3.06)；`MTKLogger` 0.26(+1.83)；`Music` 0.74(+1.72)；`FileManager` 0.40(+0.81)；`Omacp`、`CalendarImporter`、`LocationEM2`、`YGPS`、`FTPreCheck`、`AtciService`、`MDMLSample`、`SensorHub`、`BatteryWarning`、`DrmProvider` |
| `/lib64` | 249 | 96.83 MiB | **64 位库 → 整体删除（见 D2）** |
| `/lib` | 250 | 76.04 MiB | 32 位库，含 `libbluetooth_jni.so`、`bluetooth`/`audio`/`media` 栈 |
| `/vendor/lib` | 244 | 71.89 MiB | MTK HAL 与私有库（保留） |
| `/priv-app` | 61 | 70.58 MiB | `Settings` 17.84、`SystemUI` 6.81(+12.02)、`PackageInstaller` 2.66(+4.97)、`Launcher3` 0.64(+4.50)、`DocumentsUI` 0.73(+4.17)、`EmergencyInfo` 1.82(+2.70)、`CalendarProvider`(+1.63)、`DownloadProvider`(+1.44)、`MediaProvider`(+1.30)、`BlockedNumberProvider`(+1.00)、`ValidationTools`(+0.81)、`ManagedProvisioning`(+0.73)、`SettingsProvider`(+0.55) |
| `/vendor/lib64` | 199 | 67.69 MiB | **64 位 vendor 库 → 整体删除（见 D2）** |
| `/fonts` | 141 | 35.24 MiB | 字体，按语言裁剪 |
| `/vendor/etc` | 78 | 32.39 MiB | `audio_param/`、`audio_device.xml`、`audio_policy_configuration.xml`、`a2dp_audio_policy_configuration.xml`、`media_codecs*.xml`、`mtk_omx_core.cfg`、`.tp/`（触摸参数）等 |
| `/usr` | 114 | 23.60 MiB | `share`（icu/zoneinfo）、`keylayout`、`idc` |
| `/vendor/bin` | 100 | 14.64 MiB | `wmt_loader`、`wmt_launcher`、`stp_dump3`、`mnld`、`MPED`、`atci_service`、`atcid`、`fuelgauged`、`nvram_daemon`、`pppd` 等 |
| `/bin` | 267 | 13.49 MiB | toybox + 各类 daemon |
| `/vendor/firmware` | 11 | 10.35 MiB | MT6627 BT/WiFi/FM 固件、`modem_1_ltg_n.img`、`catcher_filter_1_ltg_n.bin`、`dsp_1_ltg_n.bin`、`em_filter_1_ltg_n.bin`、`pcm_*`（DVFS）|
| `/tts` | 12 | 6.16 MiB | pico TTS 6 语种 → 删或只留 1 种 |
| `/vendor/priv-app` | 7 | 3.71 MiB | |
| `/etc` | 245 | 3.53 MiB | 各类 xml/conf，含 `bluetooth/` |
| `/vendor/framework` | 4 | 1.79 MiB | |
| `/xbin` | 2 | 1.26 MiB | |

**可直接删除的量：`/preload` 993 MiB + `1080p.avi` 128 MiB + `MiraVision` 61 MiB + `/lib64`+`/vendor/lib64` 165 MiB ≈ 1.41 GiB。**

## 6. ⚠️ 镜像格式注意（工具链必读）

本固件的 `system.img` / `userdata.img` / `cache.img` 是 **Android sparse 格式，但 `block_size = 4096`**，
而 AOSP `libsparse` / `simg2img` 把 512 写死在代码里 → **用 `simg2img` 会得到完全错误的结果**（只解出几十字节）。

实测 header 布局（28 字节）：
```
off  0  u32  magic        0xED26FF3A
off  4  u16  major = 1     off 6  u16 minor = 0
off  8  u16  file_hdr_sz = 28    off 10 u16 chunk_hdr_sz = 12
off 12  u32  block_size = 4096   ← ★ 与标准 libsparse 的唯一实质差异（标准是 512，写死在代码里）
off 16  u32  total_blks          输出块数。**注意单位不是恒定的**：实测同一批镜像里，
                                 有的 total_blks × 512 才等于分区大小，有的要 × block_size(4096)。
                                 → **不要依赖这个字段做校验**，一律以「走完 total_chks 个 chunk 后
                                    的实际输出大小 == scatter 分区大小」为准。
off 20  u32  total_chks          chunk 总数。**这是唯一可靠的循环上界**，必须用它。
                                 本固件实测：system=5463、userdata=60、cache=23
off 24  u32  image_crc = 0
★ 解析要点：
  · 用 struct.unpack_from 按固定偏移逐字段读，**不要用格式串一次解**（`'<IHHHHIII'` 与
    `'<IHHHHIIII'` 只差一个字符、字节数却差 4，极易数错）。
  · 循环以 total_chks 为上界；chunk 的 in-file 跨度用 `total_sz`（字节，含 12 字节头），
    DONT_CARE 的 `total_sz` 恒为 12（无数据体）。
  · 转换完必须校验：**输出大小 == scatter 分区大小**，且 **ext4 superblock 的
    `s_blocks_count_lo × block_size == 输出大小`、`s_magic == 0xEF53`**。两个都过才算成功。
```
chunk header（12 字节）：`u16 type`（0xCAC1 FILL / 0xCAC2 RAW / 0xCAC3 DONT_CARE / 0xCAC4 CRC32）、
`u16 reserved`、`u32 chunk_sz`（输出块数，以 block_size 为单位）、`u32 total_sz`（**本 chunk 在文件中的字节数，含 12 字节头**）。
实测语义：DONT_CARE 的 `total_sz` 恒为 12、无数据体、输出 `chunk_sz × block_size` 个 0；
FILL/RAW 的数据体 = `total_sz − 12` 字节，**原样拷进输出**（即使 type 标成 FILL，存的也是字面数据而非 4 字节填充值）。

转换后实测校验（本固件 `system.img`）：
- 走完 **5463** 个 chunk（2738 FILL + 2725 DONT_CARE），输出 **3,095,396,352 B = 0xB8800000**，
  与 scatter 的 `system` 分区大小**逐字节一致**；
- ext4 superblock：`s_magic=0xEF53`、`s_inodes_count=186208`、`s_blocks_count_lo=743916`、
  `s_log_block_size=2`（块 4096）、`s_volume_name="system"`、`s_state=1`、`s_feature_incompat=0x42`；
- 同一转换器在另外三个镜像上也验证通过（输出分别为 512 MiB / 2.5 GiB / 400 MiB，
  均与各自 scatter 分区大小一致，ext4 superblock 均合法）。

**转换工具**：`scripts/mtk_sparse2raw.py`（随本文档交付，含 `check_ext4()` 自检）。
读取 ext4 内容无需 root：`debugfs -R "dump /<path> <out>" system.img`、`debugfs -R "ls -l /<dir>" system.img`。

---

# 第二部分 · 开发目标

## 7. 功能目标（必须全部实现）

| ID | 目标 | 验收判据 |
|---|---|---|
| **F1** | 自编 AOSP **8.1** 固件可启动、可日常使用 | 进桌面；显示/触摸/按键/USB/TF 卡/充电/电量/深睡唤醒/重启不丢配置 全绿 |
| **F2** | **蓝牙 A2DP Sink（接收端）**：手机/电脑把 U2 当蓝牙音箱 | 配对后可选为"媒体音频"输出；出声；`tshark` 抓到 AVDTP `Set Configuration` |
| **F3** | Sink 支持 **SBC** | 默认编码，44.1/48 kHz 均正常 |
| **F4** | Sink 支持 **AAC** | 手机开发者选项可锁 AAC；**iPhone/iPad 能高音质推流**（iOS 只发 SBC/AAC） |
| **F5** | Sink 支持 **SBC-XQ**（DualChannel + 高 bitpool，~450–550 kbps） | 发送端为 Linux/PipeWire 时可协商到 |
| **F6** | **一拖二**：2 台设备同时连接，1 台放音，可切换 | 两台同时保持 ACL；切换 <1 s；来回切 200 次无泄漏 |
| **F7** | **A2DP Source（发射）**：SBC + AAC + **LDAC**（+ 可选 aptX/aptX HD） | 连 LDAC 耳机时开发者选项可选到 LDAC |
| **F8** | **AVRCP**：显示曲名/艺人/专辑、响应播放控制、绝对音量 | 手机端能看到 U2 作为播放器；U2 能暂停/切曲 |
| **F9** | **保留本地播放能力**：本地音频/视频文件、TF 卡、USB OTG | 与原机功能等价或更好 |
| **F10** | Wi-Fi（STA + 可选 AP）、FM 收音 | 联网正常；FM 可搜台播放 |
| **F11** | 可 OTA / 可重刷 / 可回滚 | 提供 `update.zip` + scatter 包 + 原厂救砖包 |

## 8. 资源目标（★ 与功能同等重要，必须量化验收）

| ID | 指标 | 基线实测 | **目标** | 测量方法 |
|---|---|---|---|---|
| **R1** | `/system` 占用 | 2,197 MiB（其中 `/preload` 993 MiB） | **≤ 620 MiB** | `du -sb` 挂载后的 system |
| **R2** | 刷入后 `/data` 首次可用空间 | — | **≥ 2.4 GiB**（userdata 3 GiB） | `df /data` |
| **R3** | **开机后静置 RAM 占用**（无应用） | 原厂 ~700–900 MiB | **≤ 380 MiB** | `dumpsys meminfo`（total PSS）+ `cat /proc/meminfo` |
| **R4** | **蓝牙接收播放时整机 RAM** | — | **≤ 620 MiB** | 同上，播放中测 |
| **R5** | `com.android.bluetooth` 进程 PSS | — | **≤ 70 MiB** | `dumpsys meminfo com.android.bluetooth` |
| **R6** | `system_server` PSS | — | **≤ 130 MiB** | `dumpsys meminfo system_server` |
| **R7** | `surfaceflinger` + SystemUI PSS | — | **≤ 90 MiB** | `dumpsys meminfo` |
| **R8** | **冷启动到桌面** | 原厂 ~60–90 s | **≤ 30 s** | `dmesg` 时间戳 / `logcat -b events \| grep boot_progress` |
| **R9** | 常驻进程数 | 原厂 ~45–60 | **≤ 22** | `ps -A \| wc -l` |
| **R10** | 常驻 native 服务数 | — | **≤ 12** | `getprop \| grep init.svc` 中 running 的数量 |
| **R11** | 蓝牙连接→出声延迟 | — | **≤ 1.5 s** | 秒表 + `dumpcap` 时间戳 |
| **R12** | A2DP 音画延迟（Sink） | — | **≤ 180 ms** | 录制对比 |
| **R13** | 30 分钟连续播放 glitch 次数 | — | **≤ 2** | 人工听 + `dumpsys media.audio_flinger` underrun 计数 |
| **R14** | 蓝牙接收时 CPU 占用（4×A53） | — | **≤ 35%（单核等效）** | `top -n 5` |
| **R15** | 待机功耗（息屏、蓝牙保持连接） | — | 相对原厂 **不劣化** | 电流表 |

## 9. 非目标（明确不做，避免范围蔓延）

- ❌ **不做 GMS / Google 服务**（1.5 GB 内存扛不住，且与 R3/R6 冲突）
- ❌ **不做电话/短信/SIM/移动数据**（U2 是播放器形态；即使基线含 modem 固件也一律裁掉）
- ❌ **不做 Treble / 不重划分区表 / 不动 GPT**（见 D4）
- ❌ **不升级到 Android 9+**（Mali-T720 的 DDK 已停在 r1x 世代，且 9 起要求 GKI 5.x 内核）
- ❌ **不自编内核**（见 D3；仅按需追加 config 项或 patch，产物仍走 prebuilt 流程）
- ❌ **不做 A2DP Sink 的多路同时混音**（native 单例架构 + BT 4.0 单天线共 2.4G，两路 SBC 已占 ~60% 空口）
- ❌ **不做 LDAC 接收**（需要 Sony 未开放的解码器；若坚持要做，见 §14.6 的可选路径）
- ❌ **不做相机**（无摄像头；`camera.disable_zsl_mode=1` 也说明相机非重点）
- ❌ 不保留 MTK 的 `MiraVision`、`EngineerMode`、`MTKLogger`、`Exchange2`、`MtkCalendar`、`Omacp`、`AtciService`、`MDMLSample`、`LocationEM2`、`YGPS`、`FTPreCheck`、`BsHotApps`、`/preload` 全部预装应用

---

# 第三部分 · 技术方案

## 10. 五个关键决策（每条都给了理由与验证方法）

### D1 · 基线用 **AOSP 8.1 自编**，而不是在 7.0 上打补丁
| | 在 7.0 上补 sink | **自编 8.1**（选定） |
|---|---|---|
| Sink + SBC 解码 | 需打开 `USE_AUDIO_TRACK` 并让 SBC 解码器链入，**且 7.0 无 codec 框架** | ✅ **AOSP 8.1 的 `btif/src/btif_a2dp_sink.cc`（593 行）自带 OI SBC 解码** |
| AAC Sink | 需按 7.0 的 `a2d_*` API **从零自写**（7.0 的 `stack/a2dp/` 只有 `a2d_api.c`/`a2d_int.h`/`a2d_sbc.c`） | ✅ 只需回移 **一个文件** `a2dp_aac_decoder.cc`（3,511 B，安卓 9 引入） |
| LDAC 发射 | ❌ 7.0 完全没有 LDAC（8.0 才进 AOSP），要回移整套 vendor codec 层 | ✅ 8.1 自带 `a2dp_vendor_ldac{,_encoder,_abr}.cc` + `external/libldac` |
| `A2dpSinkStreamHandler` | ❌ 7.0 无此类，要自己写 | ✅ 8.1 自带（340 行），只需改多设备 |
| codec 框架 | ❌ 无 | ✅ `a2dp_codec_config.cc`（1280 行），未来加编码只需加 case |
| aptX 发射 | ❌ | ⚠️ 8.1 栈支持，需补 `libaptX_encoder.so`/`libaptXHD_encoder.so`（2023 年高通已开源进 AOSP） |

### D2 · **32 位单架构**（`TARGET_ARCH=arm`，不做 64 位 secondary）
理由与收益：
- **存储**：删掉 `/lib64`（96.83 MiB）+ `/vendor/lib64`（67.69 MiB）= **165 MiB**；
  odex 从 `arm`+`arm64` 双份变单份（基线 `/app`+`/priv-app`+`/vendor/app` 的 arm64 odex 合计 >90 MiB）。
- **内存**：只跑一个 zygote（不是 `zygote64_32` 两个），省下 **60–100 MiB**；
  32 位指针/对象头更紧凑，Java 堆占用低约 10–15%。
- 代价：无法运行 64 位 only 的 APK（对本产品无影响）；单进程地址空间 ≤3 GB（1.5 GB 机器上无实际差别）。
- 内核仍是 64 位（`bootopt=64S3,32N2,64N2` 不变），只是 userspace 全 32 位。
  → `ro.zygote=zygote32`，ramdisk 只放 `init.zygote32.rc`。
- ⚠️ 验证点：MTK 的 `hwcomposer.mt6735.so` / `audio.primary.mt6735m.so` 在基线里是 **32 位**（`/system/vendor/lib/hw/`），
  与 32 位 userspace 天然匹配。**若发现某 HAL 只有 64 位版本，则该项需回退到 D2' （保留 64 位）**。

### D3 · **复用基线的内核 3.18.35 + Mali r12p1 userland + legacy HAL**
这是"最低资源占用"目标的最大杠杆，也是风险最集中的一处。

**为什么可行**：
- 内核与 Android userspace 版本**解耦**：AOSP 8.1 官方支持 3.18 内核（8.x 的最低要求就是 3.18）。
- 8.1 的 `SurfaceFlinger` 通过 **HWC2 passthrough**（`android.hardware.graphics.composer@2.1-impl` 的
  passthrough 模式）加载 legacy `hw_module_t` 的 `hwcomposer.<board>.so` → 基线的 `hwcomposer.mt6735.so` 可用。
- 音频同理：`android.hardware.audio@2.0-impl` 的 passthrough 可包装 legacy `audio.primary.mt6735m.so`。
- **8.1 的蓝牙栈不用 HIDL**：`android.hardware.bluetooth@1.0`（HIDL `IBluetoothHci`）是 **Android 9** 才引入的。
  8.1 的 `bluetooth.default.so` 通过 legacy `bt_vendor_interface_t`（`hw_get_module("bt-vendor")`）加载
  `libbt-vendor.so` → **基线的 `libbt-vendor.so` + `libbluetooth_mtk*.so` + MT6627 固件链路可原样复用**。
- Mali r12p1 的 `libGLES_mali.so` 是 2015 年的 DDK，但其 EGL/GLES 接口稳定，
  在 8.1 上通常可用（**必须实测，见 R 风险 R2**）。

**收益**：
- 不需要移植 `st7701s_fw_dsi_vdo_boe` 面板驱动（**省 3–5 天 + 消除"点不亮屏"这个最大不确定性**）；
- 不需要换 Mali userland（省掉 29 MB 的 r18p0 blob，且 r12p1 的 `libGLES_mali.so` 更小）；
- 不需要引入任何 HIDL 服务进程（**每个 HIDL service 常驻 3–8 MiB**，基线方案要 8–10 个 → 省 40–70 MiB RAM，直接服务 R3/R9/R10）；
- 不需要处理 VNDK / vendor sepolicy 分片 / `manifest.xml` / `compatibility_matrix.xml`。

**代价与必须做的内核适配**（3.18.35 → 支撑 8.1 userspace）：
```
必须确认/追加的 config（逐项在 .config 里核对，缺则补，然后重编内核）：
  CONFIG_CGROUPS / CONFIG_MEMCG / CONFIG_CGROUP_SCHED / CONFIG_CGROUP_CPUACCT
  CONFIG_CGROUP_FREEZER / CONFIG_BLK_CGROUP / CONFIG_FAIR_GROUP_SCHED
  CONFIG_NAMESPACES / CONFIG_PID_NS / CONFIG_NET_NS / CONFIG_UTS_NS / CONFIG_IPC_NS
  CONFIG_FHANDLE / CONFIG_SIGNALFD / CONFIG_TIMERFD / CONFIG_EPOLL / CONFIG_UNIX
  CONFIG_ANDROID_BINDER_IPC / CONFIG_ASHMEM / CONFIG_ION / CONFIG_SYNC_FILE
  CONFIG_ZRAM / CONFIG_BLK_DEV_LOOP / CONFIG_BLK_DEV_DM / CONFIG_DM_VERITY
  CONFIG_TUN / CONFIG_PPP / CONFIG_PPPOLAC / CONFIG_PPPOPNS
  CONFIG_NETFILTER* / CONFIG_NF_CT_* / CONFIG_IP_NF_* / CONFIG_IP6_NF_*
  CONFIG_SCHED_DEBUG / CONFIG_PROC_PAGE_MONITOR / CONFIG_PAGE_IDLE_FLAG
  CONFIG_MEMBARRIER / CONFIG_SECCOMP / CONFIG_SECCOMP_FILTER / CONFIG_AUDIT
  CONFIG_STRICT_KERNEL_RWX（8.1 的 VTS 要求，自编固件可不开）
可能要打的 patch：
  · binder：8.1 需要 /dev/binder /dev/hwbinder /dev/vndbinder 三个节点。
    **非 Treble 下 hwbinder/vndbinder 可以不存在**，但 binder 驱动必须支持多 context（3.18 已支持）
  · ashmem / ion / sync_fence：3.18 的 MTK 分支通常已带，核对即可
  · dm-verity：若保留 verify 则需 CONFIG_DM_VERITY + verity_key；本方案默认关闭（见 §11）
```
> **回退预案（D3'）**：若 r12p1 Mali userland 或 legacy HAL passthrough 在 8.1 上跑不通，
> 则改用「新内核（3.18.79 世代）+ Mali r18p0 userland（`libGLES_mali.so` 29.3 MB，内含 `Mali-T720` 支持，
> 版本串 `r18p0-00cet0`）+ HIDL HAL」。此路线需要额外移植 `st7701s_fw_dsi_vdo_boe` 面板驱动
> 到新内核，并引入 8–10 个 HIDL 服务进程（RAM +40–70 MiB，R3 目标需放宽到 ≤450 MiB）。

### D4 · **非 Treble，不重划 GPT**
- `ro.board.first_api_level = 24`（设备首发 API 级别），配合 `ro.treble.enabled=false`，
  8.1 的 framework 会按"升级设备"放宽 Treble 要求。自编固件本来也不跑 VTS。
- vendor 内容继续放在 `/system/vendor/`（`TARGET_NO_VENDOR_IMAGE := true`），
  沿用基线的 26 分区布局 → **完全不动 GPT，砖风险降到最低**。
- `ro.board.first_api_level=24` 还允许继续使用 legacy HAL（HIDL 不是强制）。

### D5 · **关闭 dm-verity 与 FDE**
基线的 `/system … verify` 与 `/data … forceencrypt=…/metadata` 必须处理，否则自编 system 无法启动：
- ramdisk 的 `fstab.mt6735`：`/system` 去掉 `verify`；`/data` 去掉 `forceencrypt=…`（保留 `noatime,nosuid,nodev,noauto_da_alloc,discard`）
- `BoardConfig.mk`：`BOARD_VERITY_DISABLE := true`、`TARGET_HW_DISK_ENCRYPTION := false`
- `userdata` 分区必须**擦除或重刷**（7.0 的 FDE 数据结构与 8.1 不兼容）
- 出货评估：如需防篡改，改用 **AVB 1.0 + 自签 vbmeta footer**（8.1 支持），而不是 dm-verity + 原厂 key

---

# 第四部分 · 实施

## 11. 阶段 0 · 备份与工具链（0.5 天，**不可跳过**）

```bash
# 工具
git clone https://github.com/bkerler/mtkclient && cd mtkclient
python3 -m pip install -r requirements.txt
# MT6735 的 BootROM exploit 已有现成实现：https://github.com/MTK-bypass/exploit_mt6735

# 机器关机 → 按住音量上 → 插 USB
python3 mtkclient printgpt                       # 确认免鉴权可用，比对 §4 的分区表
python3 mtkclient rl out_full u2_full_backup     # ★ 全盘备份（唯一后悔药）
for p in preloader lk tee1 tee2 secro boot recovery logo system userdata cache \
         nvram proinfo nvdata protect1 protect2 para seccfg metadata expdb; do
  python3 mtkclient r $p u2_full_backup/$p.img   # 再单独备份一次关键分区
done
```
- 备份**存两份到不同介质**（本机 + 云端/U 盘）。没有备份不许做任何刷写。
- 编译机：Ubuntu 20.04/22.04 x86_64，≥8 核，≥32 GB RAM，**SSD ≥600 GB**，**JDK 8**（8.1 必须）。
```bash
sudo apt install -y git-core gnupg flex bison build-essential zip curl zlib1g-dev \
  libc6-dev-i386 x11proto-core-dev libx11-dev lib32z1-dev libgl1-mesa-dev \
  libxml2-utils xsltproc unzip fontconfig python-is-python3 ccache \
  openjdk-8-jdk android-tools-adb android-tools-fastboot \
  e2fsprogs e2fslibs abootimg device-tree-validator
```
- 源码：
```bash
mkdir -p ~/aosp-u2 && cd ~/aosp-u2
repo init -u https://android.googlesource.com/platform/manifest -b android-8.1.0_r80
repo sync -c -j8 --no-tags --no-clone-bundle
```

## 12. 阶段 1 · 引导链验证（1–2 天）★ 闸门 G0

**已知约束**（基线 `lk.bin` 345.1 KiB 实测字符串）：
```
U2 lk.bin 含：ANDROID! / bootimg / bootimg_hdr
              androidboot.bootreason / hardware / mblogenable / serialno / usbconfig / verifiedbootstate
U2 lk.bin 缺：androidboot.veritymode   androidboot.init_rc   androidboot.atm
```
Android 8.x 的 init 依赖 `androidboot.veritymode=` 决定 dm-verity 行为。**基线 lk 不传这个参数。**

按成本从低到高依次尝试，**任一成功即可**：

| 实验 | 做法 | 判据 |
|---|---|---|
| **A** | 8.1 的 ramdisk **去掉 fstab 里的 `verify` 与 `forceencrypt`**（D5），boot.img 打 **v0 header**，cmdline 用基线原值 `bootopt=64S3,32N2,64N2`，只刷 boot 分区 | 串口/`expdb` 能看到 init 启动日志 |
| **B** | 在 A 的基础上，cmdline 追加 `androidboot.veritymode=disabled`（若 lk 会覆盖我们写的 cmdline 则无效，转 C） | 同上 |
| **C** | patch 基线 `lk.bin`：在其字符串表的空闲区（连续 `0x00`）写入 `androidboot.veritymode=disabled` 与 `androidboot.init_rc=`，并在 cmdline 拼接函数处补引用（需 ARM64 反汇编，IDA/Ghidra） | 同上 |
| **D** | 移植 lk 源码到本板级配置重编（需向 ODM/方案商索取 `tek6735m_65_d_n` 的 lk 源码） | 同上 |

⛔ **禁止**：直接刷入其它项目的 `lk`/`preloader`/`tee`。`preloader` 负责 DRAM 初始化且与板级 DDR 强耦合，
`lk` 含板级配置，`trustzone.bin`(169,984 B) 与别的项目的 `tee.img` 格式不同。刷错可能连 SP Flash Tool 都识别不到。

**每次失败后必须读 `expdb`**：`python3 mtkclient r expdb expdb.bin`（10 MiB，含 last log 与 kernel panic）。

**验收门 G0**：自编 8.1 boot.img 能引导到 init 并打印日志（此时可以黑屏，只要 adb/串口活着）。

## 13. 阶段 2 · 内核适配（3–7 天）

**基线内核保留**（D3），只做 8.1 适配：
1. 取源码：优先向 ODM 索取 `alps-mp-n0.mp1-V1.19_tek6735m.65.d.n_P133` 的内核源码；
   退路是公开的 MT6735 3.18 内核树（如 `LCM-MTK/android_kernel_mediatek_mt6735`、
   `itzmanish/ALPS-MP-M0.MP1-V1.0.2_VZ6735_65T_I_M_KERNEL` 为 3.18.19），
   **但必须确认其中含 `st7701s_fw_dsi_vdo_boe` 面板驱动**，否则要从基线内核反推/重写。
2. 按 D3 的清单核对并补齐 `.config`（`ARCH=arm64`，工具链 **gcc 4.9**，与基线一致）。
3. **保留 appended DTB 布局**：产物是 `cat Image.gz <u2>.dtb > kernel`，`dt_size` 在 header 里填 0
   （基线就是这样，`dt_size=0` 而 DTB 实际在偏移 6,348,168）。
4. **保留基线的 DTB**（63,240 B）。它含 `st7701s` 的完整 DSI 时序（`lcm_params-dsi-*`）、
   `i2s1*-gpio`、`msdc0..3`、`mt6311/mt6328` regulator、`keypad`、`usb0_base`、`bat_meter` 等板级描述。
5. 若走回退预案 D3'，才需要移植面板驱动到新内核，并把 `lcm_device_list[]` 加上 `st7701s_fw_dsi_vdo_boe`。

**验收门 G1**：自编内核 + 基线 DTB → **屏幕点亮**（logo 或 kernel console）。
点不亮 90% 是 LCM/DSI 问题：`dmesg | grep -iE "lcm|dsi|disp"`，读 `expdb`，接串口。

## 14. 阶段 3 · device tree 与 blob 组装（1 周）

### 14.1 目录
```
device/keenplus/u2/
├── AndroidProducts.mk        # COMMON_LUNCH_CHOICES := u2-userdebug u2-user
├── u2.mk                     # product 定义（裁剪清单在这里，见 §15）
├── BoardConfig.mk            # ★ 见 14.2，数值全部来自 §3/§4 实测
├── device.mk                 # PRODUCT_PACKAGES / COPY_FILES / 属性
├── bdroid_buildcfg.h         # 蓝牙编译期配置（见 §16.1）
├── overlay/                  # 资源覆盖（见 §16.2）
├── sepolicy/                 # 自定 te 规则
├── prebuilt/
│   ├── kernel                # 自编（§13）
│   ├── lk.bin  trustzone.bin  preloader_tek6735m_65_d_n.bin  secro.img   # ★ 基线原样
│   └── logo.bin              # 重做（3 寸）
├── proprietary-files.txt     # 见 14.4
└── extract-files.sh
vendor/keenplus/u2/           # extract-files.sh 的产物
```

### 14.2 `BoardConfig.mk`（**数值已全部按实测填好**）
```makefile
# ---------- 架构：32 位单架构（决策 D2）----------
TARGET_ARCH := arm
TARGET_ARCH_VARIANT := armv7-a-neon
TARGET_CPU_VARIANT := cortex-a53
TARGET_CPU_ABI := armeabi-v7a
TARGET_CPU_ABI2 := armeabi
TARGET_CPU_SMP := true
# 不设 TARGET_2ND_ARCH → ro.product.cpu.abilist=armeabi-v7a,armeabi
# ro.zygote=zygote32（ramdisk 只放 init.zygote32.rc）
# 注意：内核仍是 arm64（bootopt=64S3,32N2,64N2 不变），只是 userspace 全 32 位

TARGET_BOARD_PLATFORM := mt6735m            # ← ro.board.platform 实测值
TARGET_NO_BOOTLOADER := true
BOARD_USES_MTK_HARDWARE := true

# ---------- 内核：自编 prebuilt ----------
TARGET_PREBUILT_KERNEL := device/keenplus/u2/prebuilt/kernel
BOARD_KERNEL_CMDLINE := bootopt=64S3,32N2,64N2 androidboot.veritymode=disabled
BOARD_KERNEL_PAGESIZE := 2048                        # ← 实测
BOARD_KERNEL_BASE := 0x40080000                      # ← = header 的 kernel_addr
BOARD_MKBOOTIMG_ARGS := --kernel_offset 0x00000000 \
                        --ramdisk_offset 0x03f80000 \
                        --second_offset 0x00e80000 \
                        --tags_offset 0x0df80000
# ★ offset 的语义是「相对 BOARD_KERNEL_BASE 的偏移」，mkbootimg 计算 addr = base + offset。
#   基线 header 实测：kernel_addr 0x40080000 / ramdisk_addr 0x44000000 /
#                    second_addr 0x40F00000 / tags_addr 0x4E000000
#   取 base = kernel_addr = 0x40080000，则四个 offset 恰为：
#     kernel_off  = 0x40080000 − 0x40080000 = 0x00000000
#     ramdisk_off = 0x44000000 − 0x40080000 = 0x03F80000
#     second_off  = 0x40F00000 − 0x40080000 = 0x00E80000
#     tags_off    = 0x4E000000 − 0x40080000 = 0x0DF80000
#   （基线 header 里存的正是这四个值，可直接对照验证）
BOARD_BOOT_HEADER_VERSION := 0                       # ← 实测（基线 header_version 是垃圾值，按 v0）
BOARD_USES_LEGACY_MKBOOTIMG := true                  # MTK 常需要；编不过再去掉
BOARD_CUSTOM_BOOTIMG_MK :=                           # 若 MTK 有专用 mkbootimg 脚本再填

# ---------- 分区：沿用基线布局，不动 GPT（决策 D4）----------
BOARD_BOOTIMAGE_PARTITION_SIZE     := 0x1000000      # 16 MiB
BOARD_RECOVERYIMAGE_PARTITION_SIZE := 0x1000000      # 16 MiB
BOARD_SYSTEMIMAGE_PARTITION_SIZE   := 0xB8800000     # 2952 MiB
BOARD_USERDATAIMAGE_PARTITION_SIZE := 0xC0000000     # 3 GiB
BOARD_CACHEIMAGE_PARTITION_SIZE    := 0x19000000     # 400 MiB
BOARD_FLASH_BLOCK_SIZE             := 0x20000        # scatter block_size
TARGET_USERIMAGES_USE_EXT4 := true
BOARD_CACHEIMAGE_FILE_SYSTEM_TYPE := ext4
BOARD_SYSTEMIMAGE_JOURNAL_SIZE := 0                  # 省空间
# ★ 目标：system 实际内容 ≤620 MiB（R1），可用 ext4 稀疏 + 减小 inode 数进一步压缩
BOARD_SYSTEMIMAGE_EXT4_INODE_COUNT := 65536

# ---------- 非 Treble ----------
TARGET_NO_VENDOR_IMAGE := true                       # vendor 放 system/vendor 下
BOARD_VNDK_VERSION :=                                # 留空 = 非 Treble

# ---------- 关闭 verity / 加密（决策 D5）----------
BOARD_VERITY_DISABLE := true
TARGET_HW_DISK_ENCRYPTION := false

# ---------- 图形（legacy HAL + HWC2 passthrough）----------
TARGET_USES_HWC2 := true
NUM_FRAMEBUFFER_SURFACE_BUFFERS := 2                 # ★ 3 会多占 ~8 MiB/帧，小屏用 2 够
TARGET_FORCE_HWC_FOR_VIRTUAL_DISPLAYS := false
TARGET_RUNNING_WITHOUT_SYNC_FRAMEWORK := false
BOARD_HAS_FLIPPED_SCREEN := false
# 小屏（3" 720p 级）可关掉不必要的合成层
MAX_VIRTUAL_DISPLAY_DIMENSION := 0

# ---------- 蓝牙 ----------
BOARD_HAVE_BLUETOOTH := true
BOARD_HAVE_BLUETOOTH_MTK := true
BOARD_BLUETOOTH_BDROID_BUILDCFG_INCLUDE_DIR := device/keenplus/u2
BOARD_BLUETOOTH_BDROID_HCILP_INCLUDED := 0           # 省电，见 §16.6

# ---------- 音频 ----------
BOARD_USES_GENERIC_AUDIO := false
BOARD_USES_ALSA_AUDIO := false                       # MTK 走 tinyalsa

# ---------- SELinux ----------
BOARD_SEPOLICY_DIRS += device/keenplus/u2/sepolicy
# 调试期可临时 permissive：BOARD_KERNEL_CMDLINE += androidboot.selinux=permissive
# ★ 出货必须 enforcing

# ---------- 其它 ----------
TARGET_NO_RECOVERY := false
TARGET_NO_RADIOIMAGE := true                         # 无 modem（非目标）
BOARD_HAS_NO_SELECT_BUTTON := true
TARGET_RECOVERY_PIXEL_FORMAT := "RGBA_8888"
TARGET_SCREEN_WIDTH  := <按面板实测填>                # 从 DTB 的 lcm 参数或 logo.bin 尺寸反推
TARGET_SCREEN_HEIGHT := <按面板实测填>
```

### 14.3 ramdisk 侧必改项
| 文件 | 改动 |
|---|---|
| `fstab.mt6735` | `/system` 去掉 `verify`；`/data` 去掉 `forceencrypt=…/by-name/metadata`；其余保持 |
| `init.zygote*.rc` | **只保留 `init.zygote32.rc`**，删除 `init.zygote64_32.rc` |
| `enableswap.sh` | 基线是 `echo 0 > /sys/block/zram0/disksize`（**等于没开**）→ 改为：<br>`echo 1 > /sys/block/zram0/reset`<br>`echo 805306368 > /sys/block/zram0/disksize`（**768 MiB**，见 §15.3 的取值理由）<br>`mkswap /dev/block/zram0 && swapon -p 32767 /dev/block/zram0` |
| `default.prop` | `ro.zygote=zygote32`；删掉 `bs.fake.ram`/`bs.fake.rom`（或按产品需要保留）；`ro.debuggable` 按 build variant |
| `init.mt6735.rc` 等 | 删掉 modem/ril/camera/nfc 相关 service（见 §15.1） |
| `verity_key` | 关闭 verity 后可删 |

### 14.4 blob 清单（`proprietary-files.txt`）
**全部取自基线固件的 `/system`**（用 `debugfs` 提取，无需 root）：
```
# ---- 图形（legacy HAL，配 HWC2 passthrough）----
vendor/lib/hw/hwcomposer.mt6735.so            (202,476 B)
vendor/lib/hw/gralloc.mt6735m.so              (48,120 B)
vendor/lib/hw/memtrack.mt6737m.so
vendor/lib/egl/libGLES_mali.so                (r12p1；★ 必须与内核 DDK 同世代)
vendor/lib/egl/libGLESv1_CM_mali.so   vendor/lib/egl/libGLESv2_mali.so
vendor/lib/libged.so   vendor/lib/libgpu_aux.so   vendor/lib/libgralloc_extra.so
vendor/lib/libion.so   vendor/lib/libion_mtk.so   vendor/lib/libbwc.so
vendor/lib/libdpframework.so   vendor/lib/libm4u.so   vendor/lib/libpq_prot.so
vendor/lib/libui_ext.so   vendor/lib/libgui_ext.so   vendor/lib/libpower.so

# ---- 音频 ----
vendor/lib/hw/audio.primary.mt6735m.so        (1,075,328 B)
vendor/lib/hw/audio.usb.mt6735m.so   vendor/lib/hw/audio.r_submix.mt6735m.so
vendor/lib/libaudio_param_parser*.so   vendor/lib/libaudiocustparam.so
vendor/lib/libaudiosetting.so   vendor/lib/libaudiotoolkit*.so
vendor/lib/libaudiocompensationfilter*.so   vendor/lib/libaudiocomponentengine*.so
vendor/lib/libaudiodcrflt.so   vendor/lib/libaudiomtkdcremoval.so
vendor/lib/libaed*.so   vendor/lib/libcvsd_mtk.so   vendor/lib/libmsbc_mtk.so
vendor/lib/libtinyalsa.so   vendor/lib/libtinycompress.so   vendor/lib/libtinyxml.so
vendor/etc/audio_device.xml          ← ★ 板级音频路由，必须用本机的
vendor/etc/audio_param/*             ← ★ 板级调音参数，必须用本机的
vendor/etc/audio_em.xml   vendor/etc/audio_policy.conf（若 MTK 仍在用）

# ---- 蓝牙（legacy bt_vendor_interface_t，8.1 可直接用）----
vendor/lib/libbt-vendor.so                    (16,076 B)
vendor/lib/libbluetooth_mtk.so                (30,268 B)
vendor/lib/libbluetooth_mtk_pure.so   vendor/lib/libbluetooth_relayer.so
vendor/lib/libbluetoothem_mtk.so   vendor/lib/libbluetooth_hw_test.so
etc/firmware/ROMv2_lm_patch_1_0_hdr.bin      ← MT6627 BT 固件
etc/firmware/ROMv2_lm_patch_1_1_hdr.bin
etc/firmware/WMT_SOC.cfg
etc/bluetooth/bt_did.conf   etc/bluetooth/bt_stack.conf（可沿用，见 §16.5）

# ---- Wi-Fi / FM / GPS（按需）----
etc/firmware/WIFI_RAM_CODE_6735
etc/firmware/mt6627_fm_v1_coeff.bin   etc/firmware/mt6627_fm_v1_patch.bin
vendor/lib/hw/radio.fm.mt6737m.so   vendor/lib/hw/flp.mt6737m.so   vendor/lib/hw/gps.mt6735m.so
vendor/bin/wmt_loader  vendor/bin/wmt_launcher  vendor/bin/stp_dump3
vendor/bin/mnld  vendor/bin/MPED
etc/wifi/*（wpa_supplicant.conf / p2p_supplicant_overlay.conf / wpa_supplicant_overlay.conf）

# ---- 其它 HAL ----
vendor/lib/hw/lights.mt6735m.so   vendor/lib/hw/sensors.mt6735m.so
vendor/lib/hw/keystore.mt6735m.so vendor/lib/hw/gatekeeper.mt6735m.so
vendor/lib/hw/power.mt6735m.so    vendor/lib/libSoftGatekeeper.so
vendor/lib/libnvram*.so   vendor/lib/libcustom_*.so
vendor/bin/fuelgauged  vendor/bin/nvram_daemon  vendor/bin/permission_check

# ---- 触摸 ----
vendor/etc/.tp/*（触摸参数）

# ---- 明确不取（服务 §9 非目标与 §8 资源目标）----
#   camera.mt6735m.so 及一切 camera 相关
#   modem_1_ltg_n.img / catcher_filter_1_ltg_n.bin / dsp_1_ltg_n.bin / em_filter_1_ltg_n.bin（~10 MiB）
#   MiraVision / EngineerMode / MTKLogger / Exchange2 / MtkCalendar / Omacp / AtciService /
#   MDMLSample / LocationEM2 / YGPS / FTPreCheck / SensorHub / CalendarImporter / DrmProvider
#   /preload/hotapps/*（993 MiB）、/media/1080p.avi（128 MiB）
#   /lib64/*、/vendor/lib64/*（165 MiB，决策 D2）
#   /tts/lang_pico/*（6.16 MiB，除非产品要 TTS）
```

## 15. 阶段 4 · 系统裁剪（**这是"最低资源占用"的主战场**，1–2 周）

### 15.1 移除的模块（`u2.mk` 里用 `PRODUCT_PACKAGES` 白名单，而不是继承 `full_*` 再减）
建议直接以 `aosp_arm.mk` 为起点做**加法式**配置，而不是从 `full_mt6735.mk` 做减法。

```makefile
# ---- 移除：电话/短信/SIM/modem ----
PRODUCT_PACKAGES_DEBUG :=                      # 清空
# 不加：TeleService TelephonyProvider Mms BlockedNumberProvider CarrierConfig
#       EmergencyInfo rild ril-daemon libril librilutils rildmdlogger
#       Stk SimAppDialog ImsService telephony-common.jar voip-common.jar ims-common.jar
#       mediatek-telephony-*.jar mediatek-ims-common.jar
# init.rc：删掉 ril-daemon / rild / mdlogger / emdlogger / ccci_* 全部 service
# 属性：ro.radio.noril=yes（关键，让 framework 跳过 telephony 初始化）

# ---- 移除：相机 ----
# 不加：Camera Camera2 MtkCamera libcamera* camera.mt6735m.so cameraserver provider@2.4
# 属性：config.disable_camera=true（★ 让 system_server 不加载 CameraService）

# ---- 移除：NFC / 打印 / 投射 ----
# 不加：Nfc NfcNci Tag com.android.nfc_plugins PrintSpooler PrintRecommendationService
# 属性：config.disable_print=true

# ---- 移除：多余的 provider / 应用 ----
# 不加：CalendarProvider MtkCalendar CalendarImporter Exchange2 Email
#       ManagedProvisioning ValidationTools EasterEgg BsHotApps MiraVision
#       EngineerMode MTKLogger AtciService MDMLSample LocationEM2 YGPS FTPreCheck
#       SensorHub DrmProvider Gallery2（若用自研播放器）
# 保留：MediaProvider DownloadProvider DocumentsUI SettingsProvider PackageInstaller
#       Settings SystemUI Launcher3（或自研 Launcher）Bluetooth Jelly（浏览器，可选）
#       Music（或自研播放器）FileManager DeskClock（可选）LatinIME

# ---- 移除：WebView？----
# 基线 webview.apk = 99.99 MiB。若产品需要网页/在线书城 → 保留但换轻量实现；
# 若纯本地播放 + 蓝牙接收 → 删掉，省 100 MiB 存储 + 若干 RAM。
# ★ 这是 R1 达标与否的最大单项，需求方必须明确。

# ---- 移除：调试/日志 ----
# 不加：MTKLogger aee* libaed bugreport dumpstate（出货版）
# 属性：persist.log.tag.=S  ro.logd.size=64K  debug.atrace.tags.enableflags=0
# 关掉：dropbox / tombstone 的过量保留（ro tombstoned.max_tombstone_count=3）

# ---- 移除：TTS / 字体 / 铃声 ----
# /tts/lang_pico 6 语种 6.16 MiB → 全删（或只留 en-US）
# /fonts：只留 NotoSansCJK-Regular + Roboto-Regular/Medium/Bold + DroidSansFallback
#         删掉 NotoSans 的其它语系（Armenian/Cherokee/…）、NotoColorEmoji（若不需要）
#         目标 35.24 MiB → ≤14 MiB
# /media：删 1080p.avi（128 MiB）；bootanimation.zip 4.42 MiB → 自制 ≤400 KiB
#         shutanimation.zip 4.42 MiB → 删；铃声/通知音只留 2–3 个（目标 ≤2 MiB）
```

### 15.2 内存裁剪（服务 R3–R7）
```makefile
# ---- 低内存设备标志 ----
PRODUCT_PROPERTY_OVERRIDES += \
    ro.config.low_ram=true \
    ro.lmk.critical=0 \
    ro.lmk.critical_upgrade=false \
    ro.lmk.upgrade_pressure=100 \
    ro.lmk.downgrade_pressure=100 \
    ro.config.max_starting_bg=2 \
    ro.sys.fw.bg_apps_limit=4 \
    ro.sys.fw.use_trim_settings=true \
    ro.sys.fw.trim_empty_percent=100 \
    ro.sys.fw.trim_cache_percent=100 \
    ro.sys.fw.empty_app_percent=50

# ---- LMK 阈值（1.5 GB + 768 MiB zram，单位：页，4 KiB/页）----
PRODUCT_PROPERTY_OVERRIDES += \
    ro.lmk.low=18432 \
    ro.lmk.low_minfree=18432 \
    ro.lmk.medium=24576 \
    ro.lmk.critical_minfree=36864

# ---- ART / dexopt：牺牲首启速度换常驻内存 ----
PRODUCT_PROPERTY_OVERRIDES += \
    dalvik.vm.heapstartsize=1m \
    dalvik.vm.heapgrowthlimit=96m \
    dalvik.vm.heapsize=256m \
    dalvik.vm.heaptargetutilization=0.75 \
    dalvik.vm.heapminfree=512k \
    dalvik.vm.heapmaxfree=8m \
    dalvik.vm.usejit=false \
    dalvik.vm.usejitprofiles=false \
    dalvik.vm.dex2oat-Xms=64m \
    dalvik.vm.dex2oat-Xmx=256m \
    dalvik.vm.image-dex2oat-Xms=48m \
    dalvik.vm.image-dex2oat-Xmx=48m \
    dalvik.vm.dexopt.secondary=false \
    dalvik.vm.appimageformat= \
    pm.dexopt.first-boot=quicken \
    pm.dexopt.boot=verify \
    pm.dexopt.install=quicken \
    pm.dexopt.bg-dexopt=speed-profile \
    pm.dexopt.inactive=verify \
    pm.dexopt.shared=quicken
# ★ usejit=false + 出厂前对所有预置 APK 跑一次 speed-profile 预编译：
#   JIT 编译器本身常驻 ~20-30 MiB，关掉它对 R3 收益明显；代价是首次运行慢，
#   用出厂预编译抵消。

# ---- 关掉不需要的系统服务（每个省 5–20 MiB system_server 堆）----
PRODUCT_PROPERTY_OVERRIDES += \
    config.disable_camera=true \
    config.disable_print=true \
    config.disable_telephony=true \
    config.disable_consumerir=true \
    config.disable_networkstats=false \
    config.disable_atlas=true
# 另在 device.mk 里不装 statsd / traced / perfprofd / tombstoned 的多余组件
# 8.1 无 statsd（9 才有），但要关掉 systrace/perfetto 相关的常驻项

# ---- SystemUI 裁剪 ----
# overlay 里关掉：volume dialog 动画、recent apps（若不需要）、
#               notification shade 的模糊效果、qs tiles 只留 4 个（wifi/bt/亮度/静音）
# 目标：SystemUI PSS ≤35 MiB

# ---- zram ----
# 768 MiB（不是 1 GiB）：1.5 GB 物理内存下，zram 元数据 + 压缩池本身也吃内存，
# 768 MiB 是收益/开销的平衡点。用 lz4 压缩（CONFIG_ZRAM_LZ4_COMPRESS=y），比 lzo 快且省 CPU。
```

### 15.3 存储裁剪汇总（服务 R1）
| 项 | 基线 | 目标 | 手段 |
|---|---|---|---|
| `/preload` | 992.96 MiB | **0** | 不打包 |
| `/media/1080p.avi` | 128.34 MiB | **0** | 不打包 |
| `/lib64` + `/vendor/lib64` | 164.52 MiB | **0** | D2 |
| arm64 odex | ~90 MiB | **0** | D2 |
| `MiraVision` | 60.80 MiB | **0** | 不打包 |
| `webview` | 99.99 MiB | 0 或 100 | **需求方决策** |
| `/fonts` | 35.24 MiB | ≤14 | 只留 CJK + Roboto |
| `/tts` | 6.16 MiB | 0 | 不打包 |
| `/media` 其余（动画/铃声） | ~16.85 MiB | ≤7 | 自制小动画、精简铃声 |
| `EngineerMode`/`MTKLogger`/`Exchange2`/`Camera`/`MtkCalendar`/其它 vendor app | ~60 MiB | 0 | 不打包 |
| modem/dsp/catcher 固件 | ~10 MiB | 0 | 不打包（`TARGET_NO_RADIOIMAGE`） |
| `/usr`（icu/zoneinfo/keylayout） | 23.60 MiB | ≤12 | 裁 zoneinfo（只留 Asia/Shanghai + UTC）、裁 icu 数据（`icu-data` 精简版） |
| `/framework` | 229.04 MiB | ≤150 | 裁 telephony/ims jar、boot image 只编 32 位 |
| 其余（`/lib`、`/vendor/lib`、`/bin`、`/etc`…） | ~330 MiB | ~330 | 基本保留 |
| **合计** | **2,197 MiB** | **≈ 510–610 MiB** | **R1 目标 ≤620 MiB 可达成** |

### 15.4 启动时间裁剪（服务 R8）
- `init.*.rc` 里删掉所有不用的 service（modem/ril/camera/nfc/atci/mdlogger/emdlogger/thermal_extra…），
  每少一个常驻服务省 ~20–60 ms 启动 + 1–3 MiB RAM。
- `class main` 的服务改成 `class late_start`，让桌面先出来。
- 关掉开机动画或用 ≤400 KiB 的静态图（基线是 4.42 MiB 的 zip，解压+播放耗时明显）。
- `pm.dexopt.first-boot=quicken`（不是 `interpret-only`，也不是 `speed`）：首次开机 dex2oat 时间可控。
- 出厂前对全部预置 APK 跑 `speed-profile` 预编译，避免首启集中 dex2oat。
- `ro.sys.boot_completed` 后延迟启动非关键服务（`on property:sys.boot_completed=1`）。

## 16. 阶段 5 · 蓝牙音频接收端（功能核心）

### 16.1 `bdroid_buildcfg.h`
```c
#ifndef _BDROID_BUILDCFG_H
#define _BDROID_BUILDCFG_H

#define BTM_DEF_LOCAL_NAME        "KeenPlus U2"
#define BTM_INQ_DB_LENGTH         8          /* 减小 inquiry 数据库，省内存 */

/* ---- A2DP ---- */
#define BTA_AV_INCLUDED           TRUE
#define BTA_AV_SINK_INCLUDED      TRUE       /* ★ Sink 角色 */
#define BTA_AV_MAX_STREAMS        2          /* ★ 一拖二：允许 2 条 A2DP 流 */
#define USE_AUDIO_TRACK                      /* ★ PCM 走 AudioTrack 直出（8.1 已默认如此，
                                                保留宏定义以兼容 MTK patch） */

/* ---- 关掉不用的 profile，直接省 RAM 与启动时间（服务 R3/R9）---- */
#define BTIF_HF_CLIENT_INCLUDED   FALSE      /* HFP Client：不做车载免提 */
#define BTA_HF_CLIENT_INCLUDED    FALSE
#define BTIF_HH_INCLUDED          FALSE      /* HID Host */
#define BTA_HH_INCLUDED           FALSE
#define BTA_HL_INCLUDED           FALSE      /* 健康设备 */
#define BTIF_HL_INCLUDED          FALSE
#define BTA_MIP_INCLUDED          FALSE
#define BTIF_MAP_INCLUDED         FALSE      /* MAP：消息访问 */
#define BTA_MAP_INCLUDED          FALSE
#define BTIF_PAN_INCLUDED         FALSE      /* PAN：蓝牙上网 */
#define BTA_PAN_INCLUDED          FALSE
#define SAP_SERVER_INCLUDED       FALSE      /* SAP */
#define BTA_SDP_INCLUDED          FALSE
#define PBAP_INCLUDED             FALSE      /* 电话簿 */
#define BIP_INCLUDED              FALSE      /* 基础成像 */
#define DUN_INCLUDED              FALSE      /* 拨号网络 */
#define BTA_PR_INCLUDED           FALSE      /* 打印机 */

/* ---- BLE：本产品不需要，关掉可省 ~10-15 MiB 与一个常驻线程 ---- */
#define BLE_INCLUDED              FALSE
#define BTA_GATT_INCLUDED         FALSE
#define BTIF_GATT_INCLUDED        FALSE
#define BTA_BLE_INCLUDED          FALSE
/* ⚠️ 若产品要 BLE（例如手机 App 配网/遥控），改回 TRUE 并保留 GATT */

/* ---- 省电 ---- */
#define BT_HCILP_INCLUDED         FALSE      /* 已由 BOARD_BLUETOOTH_BDROID_HCILP_INCLUDED=0 控制 */
#define BT_WAKE_VIA_PROC          TRUE
#define LPM_BT_DEFAULT_IDLE_THRESHOLD_HOST  300
#define LPM_BT_DEFAULT_IDLE_THRESHOLD_HC    300

/* ---- AVDTP MTU：★ 若将来要做 LDAC 接收，必须 ≥679，建议 1024 ---- */
/* 8.1 的默认值在 stack/avdt/avdt_defs.h 的 AVDT_MTU；若默认 <679 需在此覆盖或改源码 */

#endif
```
> **注意**：8.1 的 AOSP `system/bt` 已经把 sink 实现在 `btif/src/btif_a2dp_sink.cc`，
> 与 7.0 的 `btif_media_task.c` 不同，`USE_AUDIO_TRACK` 在 8.1 已不再是开关（AudioTrack 是唯一路径）。
> 保留该宏只为兼容 MTK 可能残留的 patch。

### 16.2 资源 overlay
`device/keenplus/u2/overlay/packages/apps/Bluetooth/res/values/config.xml`
```xml
<resources>
    <!-- ★ Sink 总开关 -->
    <bool name="profile_supported_a2dp_sink">true</bool>
    <!-- ★ 一拖二的切换依赖 AVRCP Controller 向对端发 PASS_THRU PAUSE -->
    <bool name="profile_supported_avrcp_controller">true</bool>
    <!-- 保留发射能力（F7） -->
    <bool name="profile_supported_a2dp">true</bool>
    <bool name="profile_supported_avrcp_target">true</bool>

    <!-- 关掉不用的 profile（与 bdroid_buildcfg.h 对应，双保险） -->
    <bool name="profile_supported_hfpclient">false</bool>
    <bool name="profile_supported_hid">false</bool>
    <bool name="profile_supported_hidd">false</bool>
    <bool name="profile_supported_hdp">false</bool>
    <bool name="profile_supported_map">false</bool>
    <bool name="profile_supported_mapmce">false</bool>
    <bool name="profile_supported_pbap">false</bool>
    <bool name="profile_supported_pbapclient">false</bool>
    <bool name="profile_supported_pan">false</bool>
    <bool name="profile_supported_sap">false</bool>
    <bool name="profile_supported_opp">true</bool>   <!-- 保留 OPP 以便用蓝牙传文件到机器 -->

    <!-- Sink 行为 -->
    <bool name="a2dp_sink_automatically_request_audio_focus">true</bool>
    <integer name="a2dp_sink_duck_percent">25</integer>
</resources>
```
用 **AOSP 的 `packages/apps/Bluetooth`**，不用 MTK 的 `MtkBluetooth.apk`
（后者带 MTK 私有的 OPP UI、`a2dp_sink_mbs_label` 等，增加变量且体积更大）。

### 16.3 Sink + SBC（开箱即用）
8.1 的 `system/bt` 实测包含：
```
btif/src/btif_a2dp_sink.cc        593 行，内含 OI_CODEC_SBC_DecoderReset / OI_CODEC_SBC_DecodeFrame
embdrv/sbc/decoder/               14 个源文件 → libbt-qcom_sbc_decoder（main/Android.mk 已链接）
stack/a2dp/a2dp_codec_config.cc   含 case BTAV_A2DP_CODEC_INDEX_SINK_SBC → A2dpCodecConfigSbcSink
packages/apps/Bluetooth           A2dpSinkService / A2dpSinkStateMachine / A2dpSinkStreamHandler
```
→ **只要 §16.1、§16.2 的配置生效，Sink + SBC 立即可用，无需改任何代码。**

**验证**：
```bash
adb shell dumpsys bluetooth_manager | grep -iE "A2dpSink|Profile.*state|sink codec"
# 期望：A2dpSinkService 已启动；"initialized Sink codec SBC"
adb shell cmd bluetooth_manager 2>/dev/null | head -40
# 手机搜索 U2 → 配对 → 应显示为"媒体音频"输出设备 → 播放 → U2 出声
sudo dumpcap -i bluetooth0 -w sbc.pcap
tshark -r sbc.pcap -Y "avdtp.signalid == 3" -V | grep -iA12 "Media Codec"
```

### 16.4 AAC Sink（回移一个文件，1–2 周）
8.1 的 `a2dp_codec_config.cc` 里 `A2DP_IsSinkCodecValid` / `A2DP_IsPeerSinkCodecValid` /
`A2DP_IsSinkCodecSupported` / `A2DP_BuildSrc2SinkConfig` **已经有 AAC 分支**（source 侧要解析对端 sink 能力），
缺的只是解码器（8.1 无 `a2dp_aac_decoder.cc`，它是安卓 9 引入的）。

| 动作 | 内容 |
|---|---|
| 新增文件 | 从 AOSP **`pie-release`** 拷 `system/bt/stack/a2dp/a2dp_aac_decoder.cc`（3,511 B）+ `stack/include/a2dp_aac_decoder.h` |
| 依赖 | 9 的实现用 `aacDecoder_Open(TT_MP4_LATM_MCP1, 1)`，源码注释写明 *"Nothing to do — the library is statically linked"*；8.1 树里已有 `external/aac`（含 `libAACdec`）→ **零外部二进制依赖** |
| `a2dp_aac.cc` | 补 7 个 sink 侧函数：`A2DP_IsSinkCodecValidAac`、`A2DP_IsPeerSinkCodecValidAac`、`A2DP_IsSinkCodecSupportedAac`、`A2DP_InitCodecConfigAacSink`、`A2DP_SinkCodecIndexAac`、`A2DP_CodecIndexStrAacSink`、`A2DP_GetSinkTrackChannelTypeAac`。<br>**偷懒做法**：`git diff oreo-mr1-release pie-release -- stack/a2dp/a2dp_aac.cc stack/include/a2dp_aac.h` 抽 hunk |
| `a2dp_codec_config.cc/.h` | 加 `case BTAV_A2DP_CODEC_INDEX_SINK_AAC: codec_config = new A2dpCodecConfigAacSink(codec_priority); break;`；在 `A2dpCodecs::init()` 的 sink 优先级数组加入 `SINK_AAC`；新增 `class A2dpCodecConfigAacSink`（照 `A2dpCodecConfigSbcSink` 抄） |
| `btif_a2dp_sink.cc` | 8.1 此文件**硬编码 OI SBC**（`#include "oi_codec_sbc.h"`、自定义 `tBT_SBC_HDR`、直调 `OI_CODEC_SBC_DecodeFrame`）。两种改法：<br>**① 最小侵入（推荐先做）**：按 `A2DP_GetCodecType(p_codec_info)` 分支，SBC 走原逻辑、AAC 调 `a2dp_aac_decoder_decode_packet()`；注意 8.1 用 `tBT_SBC_HDR`，pie 用 `BT_HDR`，AAC 分支要自己解析 RTP payload（1 字节 MPEG 头 + N 个 AU，每 AU 前 2 字节大端长度）<br>**② 回移抽象层**：搬 9 的 `tA2DP_DECODER_INTERFACE` + `a2dp_sbc_decoder.cc` + 整体替换成 pie 的 `btif_a2dp_sink.cc`（655 行）。⚠️ pie 的 `bta_av_co_get_decoder_interface()` 只有声明、定义在 HAL 侧，需自己实现 shim |
| `system/bt/Android.bp` | `srcs += "stack/a2dp/a2dp_aac_decoder.cc"`；`shared_libs += "libFraunhoferAAC"`；确认 include 路径含 `external/aac/libAACdec/include` |
| **audio policy** | `a2dp_audio_policy_configuration.xml` 的 `a2dp input` 与 `BT A2DP In` 的 profile：`samplingRates="44100"` → **`"44100|48000"`** |
| 不用改 | MTK audio HAL；主 `audio_policy_configuration.xml`（Sink 的 PCM 经 `btif_avrcp_audio_track`(AudioTrack) 写到普通媒体输出，**不走** `AUDIO_DEVICE_IN_BLUETOOTH_A2DP` 输入设备） |

**验证**：
```bash
# 手机开发者选项 → 蓝牙音频编解码器 → 锁 AAC（能锁上 = 协商成功）
sudo dumpcap -i bluetooth0 -w aac.pcap
tshark -r aac.pcap -Y "avdtp.signalid == 3" -V | grep -iA14 "Media Codec"
#   期望 Media Codec = MPEG-2/4 AAC，ObjectType = AAC LC，44100 或 48000，Bit Rate ≈256000
adb shell dumpsys media.audio_flinger | grep -iA4 "output"      # 采样率应随流改变
# 客观验证：-6 dBFS 440 Hz 正弦过链路，解出 RMS ≈0.3536、peak ≈0.5000，能量集中在 440 Hz
```

### 16.5 SBC-XQ（1–2 天，零新依赖）
SBC-XQ = **DualChannel（而非 JointStereo）+ bitpool 加倍**，把码率推到 ~450–550 kbps。
改 `stack/a2dp/a2dp_sbc.cc` 里 sink caps 的 `channelMode`（保留 `A2DP_SBC_CHANNEL_MODE_DUAL`）
与 bitpool 上限。**注意**：手机端基本不会主动发 SBC-XQ，只有 Linux/PipeWire 会；
所以这条只在发送端是 Linux 时生效，**优先级低于 AAC**。

`bt_stack.conf` 可直接沿用基线（内容只有 trace level + MTK 段，与编码无关）。
调试期善用 **`MtkStackConfigOverride=/sdcard/btsc`**：往 `/sdcard/btsc` 放同名 ini 即可**免重刷改栈配置**
（若使用 AOSP 8.1 栈，该 MTK 扩展可能失效，此时改用 `setprop` + `bt_stack.conf` 重刷）。

### 16.6 一拖二（2–3 天）
8.1 的 `A2dpSinkStateMachine` 是**单设备设计**：
```java
private BluetoothDevice mCurrentDevice / mTargetDevice / mIncomingDevice / mPlayingDevice;  // 四个互斥字段
List<BluetoothDevice> getConnectedDevices() { if (getCurrentState()==mConnected) devices.add(mCurrentDevice); }  // 最多返回 1 台
```
源码注释明确写了四者互斥（*"when mIncomingDevice is not null, both mCurrentDevice and mTargetDevice are null"*）。

**改造清单**：
| 文件 | 改动 |
|---|---|
| `A2dpSinkStateMachine.java` | 四个单字段 → `HashMap<BluetoothDevice, DeviceState> mDevices` + `BluetoothDevice mActiveDevice`；`getConnectedDevices()` 返回全部；`getConnectionState(d)` 按 map 查；扁平三态 → per-device 状态机（**照抄同版本的 source 侧 `A2dpService`，它用的就是 `HashMap<BluetoothDevice, StateMachine>`**）；新增准入判断（`mDevices.size() >= MAX_CONNECTED` 时拒绝或踢最久未活跃者） |
| `A2dpSinkService.java` | `mStateMachine` 单实例 → Map；`connect()/disconnect()/getConnectionState()` 按 device 路由 |
| `A2dpSinkStreamHandler.java` | ★ **两处硬编码必须改**：<br>`if (avrcpService.getConnectedDevices().size() == 1)` → 改成 `contains(mActiveDevice)`<br>`sendAvrcpPause()` 里的 `connectedDevices.get(0)` → 改成 `mActiveDevice`<br>三个方法都要带 device 参数，不能依赖"当前设备"的隐式语义 |
| **切换动作** | 全部用现成 API，**native 零改动**：`AvrcpControllerService.sendPassThroughCmd(oldDevice, PASS_THRU_CMD_ID_PAUSE, KEY_STATE_PRESSED/RELEASED)`；兜底 `btif_a2dp_sink_set_rx_flush(true)` 丢残留帧 + 对旧流发 AVDTP SUSPEND。新流开始时会自然触发 `btif_a2dp_sink_update_decoder(codec_info)` + `BtifAvrcpAudioTrackCreate()` 重建解码器与 AudioTrack（两台采样率/编码不同也能正确切换） |
| `bdroid_buildcfg.h` | `BTA_AV_MAX_STREAMS 2`；`AVDT_NUM_LINKS` 默认已是 **2**（`system/bt/include/bt_target.h`），一拖二不用改；一拖 n 才需要 `-DAVDT_NUM_LINKS=n`（配套 `AVDT_NUM_SEPS`、`AVDT_NUM_TC_TBL`） |
| 切换策略 | **抢占式**（新流立即接管，默认）/ **礼貌式**（给当前设备 3–5 s 宽限）/ **手动式**（UI 点选）。建议默认抢占 + UI 可锁定 |

⛔ **不要做"两路同时出声混音"**：`btif_a2dp_sink_cb`、SBC 解码器上下文、`rx_audio_queue`、
AudioTrack handle、focus_state **全是全局单例**，per-stream 化等于重写 sink 层；
且 **MT6627 是单天线、与 Wi-Fi 共 2.4 GHz**，两路 SBC 已占 ~60% 空口，必卡。

**一拖二专项测试**：
| 用例 | 期望 |
|---|---|
| A 已连并播放 → B 发起连接 | B 连接成功，**A 不断线**、继续播放 |
| A 播放中 → B 开始推流 | A 收到 AVRCP PAUSE 并停；B 出声；切换 <1 s；无爆音 |
| B 播放中 → A 再按播放 | 反向切换成功；来回切 20 次无泄漏 |
| A 播放中 → B 断开 | A 不受影响 |
| 两台同时按播放（竞争） | 有确定行为（后到者等待或被拒），不死锁、不双声 |
| A=SBC 44.1k，B=AAC 48k | 切换后采样率/编码正确重建（`dumpsys media.audio_flinger` 核对） |
| 息屏 10 分钟 | 两条 ACL 都保持；恢复播放正常 |
| 长跑 | 连续切换 200 次，`dumpsys meminfo com.android.bluetooth` 无泄漏 |

### 16.7 Source（发射）侧：LDAC / aptX
- **LDAC**：8.1 的 AOSP 已含 `a2dp_vendor_ldac.cc` + `a2dp_vendor_ldac_encoder.cc` + `a2dp_vendor_ldac_abr.cc`
  与 `external/libldac`（`libldacBT_enc` + `libldacBT_abr`，Apache-2.0）。
  需确认 `config.xml` 里 `a2dp_source_codec_priority_ldac` 非 0（8.1 默认 5001，最高优先级），
  以及 MTK 是否强制走 offload（`bta_av_offload_req/rsp` 存在）；不支持 offload 就走软编，4×A53 完全够。
  **EQMID 档位建议 `A2DP_LDAC_QUALITY_MID`（660 kbps）或 ABR**：MT6627 是 BT 4.0 + 单天线共 2.4G，
  990 kbps 要占 ~70% 空口，本机 Wi-Fi 关不掉 → 990 不稳。
- **aptX / aptX HD**：8.1 的栈支持，但需要 `libaptX_encoder.so` / `libaptXHD_encoder.so`。
  2023 年高通已把 aptX/aptX HD 的**编码器**开源进 AOSP，直接编即可。
- **AVDTP MTU**：LDAC 要求 **≥679 字节**（Sony 编码器低于此值会**静默失败**：链路显示 active、
  接收端无任何错误日志、就是没声音；SBC/AAC 包小不受影响，所以症状看起来像编码 bug）。
  基线 `bt_stack.conf` 无 MTU 配置项 → 用编译期默认值。
  **实测方法**：`tshark -Y "l2cap.cmd_code == 0x02 || avdtp" -V | grep -i mtu`；若 <679，抬到 1024。

### 16.8（可选）LDAC **接收**
8.1 的 AOSP **没有** LDAC sink 解码器；android11+ 的 `a2dp_vendor_ldac_decoder.cc` 只是
`dlopen("libldacBT_bco.so")` 的空壳，而 **AOSP 的 `external/libldac` 只有 `libldacBT_enc` + `libldacBT_abr`，
从来没有解码库**（Sony 未开放）。Linux 侧（PipeWire/bluez-alsa）同样只有编码器。

若必须做：用 **`hegdi/libldacdec`**（MIT，clean-room，按 ATRAC9 逆向，含 `imdct.c`/`huffCodes.c`/
`spectrum.c`/`bit_allocation.c`），自写 ~300 行 shim 实现 AOSP 要求的 6 个符号
（`ldac_BCO_init/cleanup/decode_packet/start/suspend/configure`），编成 `libldacBT_bco.so`，
再回移 android11 的 `a2dp_vendor_ldac_decoder.cc` + `SINK_LDAC` 相关 case。
**同时必须解决 §16.7 的 MTU 问题。** 工期 +2 周。
⚠️ 商标：**"LDAC" 是 Sony 注册商标**，产品与文档只能写 "LDAC-compatible"，不得宣称认证；商用需授权。

## 17. 阶段 6 · 产品化（3–5 天）

| 项 | 做法 |
|---|---|
| **自研 Launcher** | 3 寸屏专用：大图标、极简层级。目标 ≤15 MiB、PSS ≤25 MiB（替代 Launcher3 的 0.64+4.50 MiB 与更高 PSS） |
| **音箱模式** | 一个开关：进入后 `SCAN_MODE_CONNECTABLE_DISCOVERABLE` + 延长可发现超时 + 暂停本地播放并释放音频焦点 + 屏幕常暗 |
| **设备管理 UI** | 列出已连设备、标记"正在播放"、点击切换、长按断开、显示当前编码/采样率/码率（数据取自 `dumpsys bluetooth_manager` 或 btif 回调） |
| **设备名** | `BTM_DEF_LOCAL_NAME`（bdroid_buildcfg.h）+ `PRODUCT_DEFAULT_DEVICENAME` |
| **音量** | 开 AVRCP 绝对音量 + 本地做上限保护；`persist.bluetooth.disableabsvol=true` 可回退。避免"手机拉满仍很小声" |
| **不休眠** | Sink 播放期间持 `PARTIAL_WAKE_LOCK`；`settings put global stay_on_while_plugged_in 3`；`dumpsys deviceidle whitelist +com.android.bluetooth` |
| **自动重连** | 开机按优先级依次 `connect()`；用 `A2dpSinkPriorityKey`（8.1 已有）存优先级 |
| **本地播放** | 保留/自研播放器；确认 `MediaProvider` + `DocumentsUI` 在，TF 卡与 USB OTG 热插拔正常 |
| **logo.bin** | 按 3 寸分辨率重做（基线是 571,232 B）；用 MTK 的 logo 打包工具或从基线 `logo.bin` 反解格式后替换位图 |
| **sepolicy** | 从 permissive 回到 **enforcing**，`dmesg \| grep avc` 逐条补规则。**出货必须 enforcing** |
| **OTA** | 提供 `update.zip`（recovery 刷 system+boot）；保留原厂 scatter 包作救砖包 |

## 18. 阶段 7 · 刷入、验收与测试

### 18.1 刷入（严格分步，每步可单独回滚）
```bash
# 1) 只刷 boot —— 验证 G0/G1
python3 mtkclient w boot   out/target/product/u2/boot.img
# 2) 刷 system —— 验证 G2
python3 mtkclient w system out/target/product/u2/system.img
# 3) 清 userdata（7.0 的 FDE 数据结构与 8.1 不兼容，必须清）
python3 mtkclient erase userdata
# 4) logo / recovery / cache
python3 mtkclient w logo     out/target/product/u2/logo.bin
python3 mtkclient w recovery out/target/product/u2/recovery.img
python3 mtkclient w cache    out/target/product/u2/cache.img
```
⛔ **永远不要刷**：`preloader`、`lk`、`tee1`、`tee2`、`secro`、`seccfg`、
`nvram`、`proinfo`、`nvdata`、`protect1`、`protect2`、`metadata`、`frp`、`pgpt`、`sgpt`。
> 前 5 个是引导与安全链，后 8 个含射频/触摸/传感器校准与用户数据，覆盖后**无法恢复**。

**sparse 注意**：`mtkclient w` 与 SP Flash Tool 都能直接吃 sparse 镜像。
但**不要用 `simg2img` 处理基线镜像**（§6：这批的 `block_size=4096`，标准 libsparse 写死 512，结果完全错误）。
需要 raw 时用 `scripts/mtk_sparse2raw.py`。

### 18.2 验收门
| 门 | 内容 | 不通过怎么办 |
|---|---|---|
| **G0** | 自编 8.1 boot.img 能引导到 init 打印日志 | §12 的实验 A→B→C→D；全失败则本项目不成立 |
| **G1** | 屏幕点亮 | 90% 是 LCM/DSI；`dmesg \| grep -iE "lcm\|dsi\|disp"`、读 `expdb`、接串口 |
| **G2** | 进桌面，且显示/触摸/按键/Wi-Fi/蓝牙配对/扬声器/耳机/TF/USB OTG/充电/深睡唤醒/重启不丢配置 全绿 | `lshal`（若走 D3'）、`logcat -b all`、`dmesg \| grep avc`、逐项查 HAL |
| **G3** | F2+F3：手机把 U2 当音箱（SBC），可控暂停/切曲/音量 | §16.3 |
| **G4** | F4：AAC 协商成功，30 分钟 glitch ≤2 | §16.4 |
| **G5** | F6：一拖二全部专项用例通过 | §16.6 |
| **G6** | F7：连 LDAC 耳机能选到 LDAC | §16.7 |
| **G7** | **R1–R15 全部达标** | §19 |

### 18.3 测试矩阵
| 发送端 | SBC | AAC | SBC-XQ | LDAC(收) | 备注 |
|---|---|---|---|---|---|
| 安卓（小米/华为/三星，开发者选项锁编码） | ☐ | ☐ | — | — | 主场景 |
| **iPhone / iPad** | ☐ | ☐ | — | — | **只发 SBC/AAC；AAC 是 iOS 唯一高音质路径 → 优先级最高** |
| Windows 11 | ☐ | ☐ | — | — | |
| macOS | ☐ | ☐ | — | — | 只发 AAC |
| Linux / PipeWire | ☐ | ☐ | ☐ | ☐ | 唯一能验证 SBC-XQ 与 LDAC 接收的发送端 |
| 另一台 U2 | ☐ | ☐ | — | — | 验证 source+sink 双向 |

每项测：配对、连接、出声、暂停/继续、上下曲、音量联动、切换发送端、
连续 30 分钟 glitch 计数、息屏 10 分钟不断连、拔耳机行为、TF 卡插入时的行为。

### 18.4 资源指标测量脚本（R1–R15 的统一测法）
```bash
#!/bin/sh
# measure.sh —— 在设备上跑，输出 R3/R5/R6/R7/R9/R10/R14
echo "== R1 /system 占用 =="; df /system
echo "== R2 /data 可用 ==";   df /data
echo "== R3/R5/R6/R7 内存 =="; dumpsys meminfo | head -40
echo "== R9 进程数 ==";       ps -A | wc -l
echo "== R10 常驻服务 ==";    getprop | grep -c "\[init\.svc\..*\]: \[running\]"
echo "== R14 CPU ==";         top -n 3 -d 2 | head -20
echo "== R8 启动耗时 ==";     logcat -b events -d | grep -E "boot_progress"
echo "== 音频链路 ==";        dumpsys media.audio_flinger | grep -iA6 "output thread"
echo "== underrun（R13）==";  dumpsys media.audio_flinger | grep -i underrun
echo "== 蓝牙状态 ==";        dumpsys bluetooth_manager | grep -iE "A2dpSink|codec|state|profile"
```
```bash
# 主机侧
adb shell df /system /data                       # R1 R2
adb shell cat /proc/meminfo | head -5            # R3 交叉验证
adb shell dumpsys meminfo com.android.bluetooth  # R5
adb shell dumpsys meminfo system_server          # R6
adb shell dumpsys meminfo systemui surfaceflinger# R7
adb shell ps -A | wc -l                          # R9
sudo dumpcap -i bluetooth0 -w t.pcap             # R11 R12 R13 抓包
tshark -r t.pcap -Y "avdtp.signalid == 3" -V | grep -iA14 "Media Codec"
tshark -r t.pcap -z conv,bluetooth               # 链路码率
adb pull /dev/block/... 或 python3 mtkclient r expdb expdb.bin   # 崩溃后必读
```

## 19. 资源目标落地检查表（★ 与功能同等重要）

| ID | 指标 | 目标 | 主要手段 | 检查点 |
|---|---|---|---|---|
| R1 | `/system` | ≤620 MiB | §15.3 全表（最大四项：`/preload` 993 MiB、`1080p.avi` 128 MiB、`lib64`+`vendor/lib64` 165 MiB、`MiraVision` 61 MiB） | `du -sb`；`ls -l /system/preload` 应不存在 |
| R2 | `/data` 可用 | ≥2.4 GiB | 不预装应用、不预置媒体 | `df /data` |
| R3 | 静置 RAM | ≤380 MiB | 32 位单 zygote（D2）、`low_ram=true`、关 telephony/camera/print、砍 SystemUI、`usejit=false`、无 HIDL 服务群（D3） | `dumpsys meminfo` total PSS |
| R4 | 播放时 RAM | ≤620 MiB | zram 768 MiB lz4、LMK 阈值、`bg_apps_limit=4` | 同上，播放中测 |
| R5 | bluetooth PSS | ≤70 MiB | 关掉 §16.1 里全部不用的 profile（HFP client/HID/HDP/MAP/PAN/SAP/PBAP/BIP/DUN/**BLE+GATT**） | `dumpsys meminfo com.android.bluetooth` |
| R6 | system_server | ≤130 MiB | `config.disable_*`、不加载无用 manager | `dumpsys meminfo system_server` |
| R7 | SF+SystemUI | ≤90 MiB | `NUM_FRAMEBUFFER_SURFACE_BUFFERS=2`、关动画/模糊、精简 QS tiles | `dumpsys meminfo` |
| R8 | 冷启动 | ≤30 s | 砍 init service、`late_start`、小开机动画、出厂预编译 APK | `logcat -b events \| grep boot_progress` |
| R9 | 常驻进程 | ≤22 | 32 位单 zygote、无 HIDL 服务进程、砍 MTK 服务 | `ps -A \| wc -l` |
| R10 | 常驻 native 服务 | ≤12 | 只留 `wmt_launcher`/`mnld`(可选)/`servicemanager`/`surfaceflinger`/`audioserver`/`mediaserver`(按需)/`vold`/`netd`/`healthd`/`lmkd`/`adbd`(调试版) | `getprop \| grep init.svc` |
| R11 | 连接→出声 | ≤1.5 s | AVDTP 协商不重试、AudioTrack 预创建 | 秒表 + `dumpcap` 时间戳 |
| R12 | 音画延迟 | ≤180 ms | 调小 rx 队列与 AudioTrack buffer（延迟/卡顿的权衡点） | 录制对比 |
| R13 | 30 min glitch | ≤2 | 解码线程优先级、rx 队列深度、关掉 Wi-Fi 扫描抖动 | `dumpsys media.audio_flinger` underrun |
| R14 | 接收时 CPU | ≤35% | AAC 软解在 A53 上很轻；避免 AudioFlinger 重采样（**必须补 `samplingRates="44100\|48000"`**） | `top` |
| R15 | 待机功耗 | 不劣化 | `LPM_BT_DEFAULT_IDLE_*`、`BT_WAKE_VIA_PROC`、允许 sniff（**注意：做 LDAC 660k+ 时 sniff 会引起爆音，需按码率动态禁用**） | 电流表 |

## 20. 风险登记册

| # | 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|---|
| **R1** | **基线 lk 不传 `androidboot.veritymode`/`init_rc`，8.1 的 init 起不来** | 高 | G0 失败 | §12 四个实验按序做；**实验 A（去掉 fstab 的 verify/forceencrypt）成本最低，先做** |
| **R2** | **Mali r12p1 的 `libGLES_mali.so` 在 8.1 上不工作**（EGL/GLES 版本、gralloc1 接口、`libged`/`libgpu_aux` 的 ABI） | 中 | 黑屏/SF 崩溃 | 先做 host 侧 `readelf -d` 依赖面分析；不行则走 **D3'**（换 r18p0 userland + 新内核 + 移植面板驱动），R3 目标放宽到 ≤450 MiB |
| **R3** | legacy HAL 经 HWC2/audio@2.0 **passthrough** 包装失败 | 中 | 无显示/无声 | 用 AOSP 的 `*-impl` passthrough 版；`logcat \| grep -iE "passthrough\|hwcomposer\|Hwc2"`；退路同 R2 |
| R4 | 32 位单架构下某 MTK blob 只有 64 位版本 | 低 | 该功能不可用 | 组装前对每个 blob 跑 `readelf -h` 确认 ELFCLASS32；发现 64 位 only 则该模块回退 D2' |
| R5 | 内核缺 8.1 需要的 config（cgroup/namespace/binder 多 context） | 中 | init 卡住 | §D3 的 config 清单逐项核对；用 `lxc-checkconfig` 风格的脚本自检 |
| R6 | dm-verity 关不干净（`verity_key` 或 lk 侧仍校验） | 中 | 卡开机 | `BOARD_VERITY_DISABLE`、fstab 去 `verify`、必要时 patch lk（实验 C） |
| R7 | FDE 关闭后 `/data` 结构不兼容 | 中 | 开机循环 | **必须 `mtkclient erase userdata`**；`metadata` 分区一并擦 |
| R8 | AAC 分帧解析写错（LATM/MPEG 头 + 2 字节 AU 长度） | 中 | 有声但爆音/静音 | host 上先用 `libFraunhoferAAC` 单测；参考 pie 的 `a2dp_aac_decoder_decode_packet()` |
| R9 | 48 kHz AAC 被 audio policy 拒 | 中 | 48k 流无声 | §16.4：profile 补 `44100\|48000` |
| R10 | 一拖二时 AVRCP CT 与部分手机兼容性差 | 中 | 切不动/手机异常 | 先在 3 款主流手机验证；保留"手动切换"兜底 |
| R11 | **LDAC 因 AVDTP MTU <679 静默失败**（链路 active、无错误日志、就是没声音） | 高（若没预判） | F7 的 LDAC 不通 | §16.7 抓包确认；抬到 1024 |
| R12 | LDAC 990 kbps 在 BT4.0 + 单天线共 2.4G 下断续 | 高 | 体验差 | **固定 660 kbps 或 ABR**；播放时降低 Wi-Fi 扫描频率 |
| R13 | 1.5 GB 内存下 Sink 与本地播放并发不稳 | 中 | 卡顿/断流 | zram 768 MiB、`bg_apps_limit=4`、Sink 模式禁本地播放抢焦点 |
| R14 | sepolicy 长期 permissive | 中 | 安全隐患 | G2 之后必须回 enforcing，逐条补 |
| R15 | 误刷 preloader/lk/tee/nvram 导致硬砖 | 低 | 不可恢复 | §18.1 的"永远不要刷"清单；§21 三层回滚；备份存两份 |
| R16 | `/preload` 或 `BsHotApps` 有隐藏的开机拉起逻辑，删掉后开机异常 | 低 | 开机报错 | 检查 `init.*.rc` 与 `PRODUCT_COPY_FILES` 里对 `/preload` 的引用，一并删除 |

## 21. 回滚与救砖

| 层级 | 触发 | 恢复 | 耗时 |
|---|---|---|---|
| **L1** | 自编 boot 起不来 | `python3 mtkclient w boot u2_full_backup/boot.img` | <2 min |
| **L2** | 自编 system 异常 | `python3 mtkclient w system <基线 system.img>`（sparse 可直接刷） | <10 min |
| **L3** | 全砖 / GPT 损坏 | **SP Flash Tool + 基线 scatter 全量刷**（`MT6735M_Android_scatter.txt`，preloader 必须用原厂 `preloader_tek6735m_65_d_n.bin`） | 10–20 min |

**五条铁律**
1. `preloader` / `lk` / `tee1` / `tee2` / `secro` / `seccfg` **永远不动**；
2. `nvram` / `proinfo` / `nvdata` / `protect1` / `protect2` / `metadata` / `frp` **永远不覆盖**（校准与用户数据，覆盖不可恢复）；
3. 动手前 `mtkclient rl out_full` 全盘备份，**存两份到不同介质**；
4. 每次只改一个变量（先 boot → 再 system → 再 userdata）；
5. **每次失败都读 `expdb`**（10 MiB，异常 last log）：`python3 mtkclient r expdb expdb.bin`。

## 22. 工期与里程碑

| 阶段 | 内容 | 工期 | 累计 | 门 |
|---|---|---|---|---|
| 0 | 备份 + 工具链 + 源码 | 0.5–1 天 | 1 天 | |
| 1 | **引导链验证** | 1–2 天 | ~3 天 | **G0** |
| 2 | 内核适配（config 补齐 + 重编） | 3–7 天 | ~2 周 | **G1** |
| 3 | device tree + blob 组装 | 1 周 | ~3 周 | |
| 4 | **系统裁剪**（R1/R3/R8/R9 的主战场） | 1–2 周 | ~5 周 | **G2** |
| 5a | Sink + SBC | 1–2 天 | ~5 周 | **G3** |
| 5b | AAC Sink 回移 | 1–2 周 | ~7 周 | **G4** |
| 5c | SBC-XQ + 延迟调优 | 1–2 天 | ~7 周 | |
| 5d | 一拖二 | 2–3 天 | ~7.5 周 | **G5** |
| 5e | Source：LDAC + aptX | 1–3 天 | ~8 周 | **G6** |
| 6 | 产品化（Launcher/UI/电源/logo） | 3–5 天 | ~9 周 | |
| 7 | 资源指标收敛 + 全量验证 | 3–5 天 | **~10 周** | **G7（R1–R15）** |
| 可选 | LDAC 接收（自制 `libldacBT_bco.so`） | +2 周 | ~12 周 | |
| 可选 | 回退 D3'（换内核 + r18p0 + HIDL） | +2–3 周 | — | |

**里程碑**：M1=G1（点亮屏幕，~2 周）· M2=G3（能当蓝牙音箱，~5 周）·
M3=G4+G5（AAC + 一拖二，~7.5 周）· M4=G7（资源达标 + 全量通过，~10 周）

## 23. 交付物

1. `device/keenplus/u2/` + `vendor/keenplus/u2/` 两棵树（含 `README`、`extract-files.sh`、`proprietary-files.txt`）
2. 内核树 + `.config`（标注相对基线的增量）+ 必要的 patch 集
3. **patch 集**（每个独立可 apply、可回退）：
   `01-boot-chain.patch`（fstab/verity/zygote32/enableswap）
   `02-bluetooth-sink.patch`（bdroid_buildcfg.h + overlay + profile 开关）
   `03-aac-sink.patch`（a2dp_aac_decoder 回移 + codec_config + btif 分支 + audio policy 采样率）
   `04-multidevice-sink.patch`（一拖二）
   `05-sbcxq.patch`
   `06-source-ldac-aptx.patch`
   `07-trim-system.patch`（§15 的全部裁剪）
   `08-product.patch`（Launcher/UI/电源/logo）
4. `logo.bin` 生成脚本（3 寸分辨率）
5. scatter 刷机包 + 一键刷入脚本 + **原厂救砖包**（含 §21 五条铁律）
6. 测试报告：§18.3 矩阵 + §18.4 资源测量输出 + tshark 抓包证据 + 30 分钟长跑 glitch 计数
7. 本文档的更新版（把实测结果回填到 §19 的检查表与 §20 的风险状态）

---

# 附录 A · boot.img 打包参数速查（照抄，勿改）
```
--kernel_offset  0x00008000     --ramdisk_offset 0x03f80000
--second_offset  0x00e80000     --tags_offset    0x0df80000
--pagesize       2048           --header_version 0
--cmdline        "bootopt=64S3,32N2,64N2 androidboot.veritymode=disabled"
--board          ""             （基线 name[16] 是构建时间戳，无实义）
DTB              appended 在 Image.gz 之后（cat Image.gz u2.dtb > kernel），header 的 dt_size 填 0
```

# 附录 B · AOSP 源码位置索引

| 用途 | 8.1（`oreo-mr1-release`） | 9（`pie-release`，AAC 回移的来源） |
|---|---|---|
| sink 媒体路径 | `system/bt/btif/src/btif_a2dp_sink.cc`（593 行，硬编码 OI SBC） | 同名（655 行，`tA2DP_DECODER_INTERFACE`） |
| AudioTrack 封装 | `system/bt/btif/src/btif_avrcp_audio_track.cc`（接口带 `void* handle`，**本身支持多实例**） | 同 |
| SBC 解码器 | `system/bt/embdrv/sbc/decoder/`（14 源文件 → `libbt-qcom_sbc_decoder`，`main/Android.mk` 已链接） | 同 + `stack/a2dp/a2dp_sbc_decoder.cc` |
| codec 框架 | `system/bt/stack/a2dp/a2dp_codec_config.cc`（1280 行，含 `SINK_SBC` case） | 同 + `SINK_AAC` case |
| AAC | `stack/a2dp/a2dp_aac.cc`（**已有 sink 侧解析函数**）+ `a2dp_aac_encoder.cc`；`aacEnc*` 静态链入 | + **`stack/a2dp/a2dp_aac_decoder.cc`（3511 B）** + `stack/include/a2dp_aac_decoder.h` |
| LDAC source | `stack/a2dp/a2dp_vendor_ldac{,_encoder,_abr}.cc` + `external/libldac`（`libldacBT_enc` + `libldacBT_abr`，Apache-2.0） | 同 |
| LDAC sink | ❌ 无 | ❌ 无（android11 起才有 `a2dp_vendor_ldac_decoder.cc`，且只是 `dlopen("libldacBT_bco.so")` 空壳；AOSP 从未包含该库） |
| aptX | `stack/a2dp/a2dp_vendor_aptx{,_hd}.cc` + `*_encoder.cc`（需 `libaptX_encoder.so`/`libaptXHD_encoder.so`） | 同 |
| profile 开关 | `packages/apps/Bluetooth/res/values/config.xml` → `profile_supported_*` | 同 |
| Sink Java | `packages/apps/Bluetooth/src/com/android/bluetooth/a2dpsink/{A2dpSinkService,A2dpSinkStateMachine,A2dpSinkStreamHandler}.java` | 同 |
| per-device 状态机模板 | `packages/apps/Bluetooth/src/com/android/bluetooth/a2dp/A2dpService.java`（`HashMap<BluetoothDevice, StateMachine>`） | 同 |
| 构建配置入口 | `system/bt/Android.mk` → `BOARD_BLUETOOTH_BDROID_BUILDCFG_INCLUDE_DIR` / `-DHAS_BDROID_BUILDCFG` | 同 |
| AVDTP 链路数 | `system/bt/include/bt_target.h` → `AVDT_NUM_LINKS`（**默认 2**）、`AVDT_NUM_SEPS`(6)、`AVDT_NUM_TC_TBL`(6)、`MAX_L2CAP_LINKS`(7) | 同 |
| 低内存配置参考 | `build/target/product/` 下的 `android_*.mk`、`device/generic/…` 的 `low_ram` 配置 | 同 |

# 附录 C · 待外部确认项

| # | 事项 | 找谁 / 怎么查 | 拿不到怎么办 |
|---|---|---|---|
| 1 | `tek6735m_65_d_n` 的**内核源码**（含 `st7701s_fw_dsi_vdo_boe`） | ODM（构建者 `ts01@blueshark02`）或方案商 | 用公开 MT6735 3.18 树 + 自行补面板驱动（+1–2 周） |
| 2 | `tek6735m_65_d_n` 的 **lk 源码**（若 §12 需要走实验 D） | 同上 | 走实验 A/B/C（patch 二进制或不依赖 veritymode） |
| 3 | 是否有摄像头（决定是否保留 camera 栈） | 拆机 / `dmesg \| grep -i imgsensor` / `ls /dev/MAINAF` | 默认按"无摄像头"裁剪（`config.disable_camera=true`） |
| 4 | 喇叭/功放型号（影响 `audio_device.xml` 与调音） | 拆机看丝印 | 沿用基线的 `audio_device.xml` + `audio_param/`，听感异常再调 |
| 5 | `libaptX_encoder.so` / `libaptXHD_encoder.so` | 2023 年高通已开源 aptX/aptX HD 编码器进 AOSP，直接编 | 放弃 aptX，只保 LDAC |
| 6 | 面板确切分辨率/接口参数 | 基线 DTB 的 `lcm_params-dsi-*` 属性（已解出）+ `logo.bin` 尺寸反推 | 用 DTB 值 |
| 7 | LDAC 商标/授权（若商用） | Sony LDAC 开发者计划 | 只标称 "LDAC-compatible"，不宣称认证 |
