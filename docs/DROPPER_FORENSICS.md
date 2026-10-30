# U2 预装第三方应用「复活」问题取证报告（dropper forensics）

**结论先行**：不存在网络下载器。5+1 个第三方应用以**本地载荷**形式藏在
`/system/vendor/operator/app/`（PMS 不扫描的目录，历次清单全部漏检），
由固件内置的 MTK USP 框架 + 厂商魔改 PackageInstaller 在**首次开机时静默安装**。
移除载荷目录即彻底根治（debloat v2）。

## 症状（用户实测，v0 刷机 + 全分区 Download Only 含 userdata 擦除）

- 开机后出现 5 个未预装记录的应用：夸克、豌豆荚、海贝音乐、萌新社区、万能联播。
- 系统相机（com.mediatek.camera）被卸载。

## 取证过程（fw-probe v1–v8，全部 CI 静态解剖，run 号见括号）

| 轮次 | 假设 | 结果 |
|---|---|---|
| v1–v3b | APK 层面有下载器 | 排除（40934434361：odex 字符串扫无下载逻辑） |
| v4 | ExactCalculator 被注入安装代码 | 部分成立：其 odex 引用 `persist.sys.install.by.pwd`（v1 移除） |
| v5 | app/priv-app/vendor-app 里有静默安装器 | 排除 |
| v6 | vendor/bin 原生守护进程（downloader/autobt/starter）拉包 | 排除（40939824435：字符串无毒；disableapplist.txt 无消费者） |
| v7 | 全镜像按应用名/包名 grep | **命中**：5 个 APK 实体在 `vendor/operator/app/`（40940965846） |
| v8 | 无限制全盘 `*.apk` 扫描 + 载荷目录解剖 + 安装钩子字符串 | **第 6 个载荷**：讯飞输入法 `xunfeishurufa.apk`；机制坐实（40942885130） |

### 为什么七轮清单都没看见载荷

三个盲区叠加：
1. `vendor/operator/app` 不是 PackageManagerService 扫描根 → 永远不出现在任何「已安装应用」清单里；
2. 隐藏 APK 查找规则排除了 `*/app/*` → 恰好把 `vendor/operator/app/<X>/` 滤掉；
3. 大文件列表被 `head -60` 截断 → `vendor/` 排序靠后被切掉。

教训：**下结论「不存在」之前必须做无限制全盘扫描**。

## 载荷清单（probe v8，共 203 MiB）

| 目录 | 包名 | 应用 | 大小 |
|---|---|---|---|
| vendor/operator/app/HiBy | com.hiby.music | 海贝音乐 4.0.2 | 44 MiB |
| vendor/operator/app/WDJ | com.wandoujia.phoenix2 | 豌豆荚 8.3.2.2 | 36 MiB |
| vendor/operator/app/kuake | com.quark.browser | 夸克 3.0.0.942 | 32 MiB |
| vendor/operator/app/mxsq | uni.UNI557FDDF | 萌新社区 1.0.0 | 41 MiB |
| vendor/operator/app/wanneglianbo | com.iqiyi.wlanplay | 万能联播 4.2.216 | 20 MiB |
| vendor/operator/app/xunfeishurufa | com.iflytek.inputmethod | 讯飞输入法 10.0.4 | 33 MiB |

目录内**只有这 6 个 APK**，无任何配置文件（probe v8 configs 清单为空）。

## 执行机制

1. **services.odex 内嵌 MTK USP 框架**：`com.mediatek.usp.CustomOperator`，
   字符串含 `/operator/app`、`installApk`、`enabledDisableApps`、
   `mOperatorAppInstallDir`、`operator_map_list`、`gsm.sim.operator.numeric` 等。
   开机/SIM 变化时按运营商包配置安装 `vendor/operator/app` 下的 APK、
   并启用/停用指定应用（相机被卸载与此路径或下述魔改安装器有关）。
2. **厂商魔改 PackageInstaller**（priv-app，产品名 tek6735m_65_d_n）：
   odex 引用 `persist.sys.install.by.pwd`（boot ramdisk default.prop 出厂 =1），
   以及非 AOSP 的 `getBlockUninstallForUser` / `startPermsServiceForUninstall` /
   `android.permission-group.CAMERA` 字符串 —— 静默安装/卸载的落地执行器。
3. **ValidationTools**（priv-app，v0 已移除）：`AutoInstallService` /
   `AutoUninstallApkService`，产线变体。
4. init.rc 引用的 `/vendor/bin/usp_service` 守护进程：镜像中未发现对应二进制
   字符串证据；即便存在，载荷移除后也无物可装。

**全程无网络参与**：APK 随镜像出厂，首开本地静默安装。因此 hosts 屏蔽
（debloat/hosts-block.txt）保持空置休眠即可。

## 修复（firmware-debloat-v2）

- `debloat/remove-list.txt` v2 = v1 的 7 项 + 6 个载荷目录 = 13 项；
  预期计数 before=87 / removed=13 / after=74（fw_inventory 已把
  `vendor/operator/app` 加入 APP_ROOTS）。
- 输入法安全性：讯飞移除后，原生 AOSP 键盘 `app/LatinIME`
  （com.android.inputmethod.latin）仍在镜像中，不受清单影响。
- 相机：`vendor/app/Camera` 在 v1 起已按清单移除（U2 无摄像头硬件，用户确认）；
  若刷 v2 后仍观察到「相机被卸载」类行为，说明执行器另有配置源，需上报复测。
- 纵深防御备选（未启用）：`mtk_bootimg.py --set-prop` 把
  `persist.sys.install.by.pwd` 改为 0 —— 载荷已删，非必需。

## 证据文件（CI artifact：fw-probe-report）

- v7：`v7_appname_files.txt`（17 处命中）、`v7_prop_fragments_sorted.txt`
- v8：`vendor_operator_tree.txt`、`vendor_operator_du.txt`、
  `apks_outside_roots.txt` + `apks_outside_manifests.txt`（全盘 APK 扫描）、
  `services_install_strings.txt`（USP CustomOperator）、
  `pkginstaller_hook_strings.txt`（魔改安装器）

## 后续（v2 上机判决 → v3 stub 方案，2026-10-30 晚）

**v2 判决：删除载荷目录导致首启挂死。** 用户实测（全分区 Download Only 含
userdata 擦除）：

| 包 | 载荷目录 | 刷写 | 启动 |
|---|---|---|---|
| v0 / v1 | 完整保留 | ✓ | ✓ 正常（首启安装 5+1 载荷） |
| btswap-raw（v2 基座+BT 换栈） | 已删除 | ✓ 100% | ✗ 过厂商动画后循环进度条 |
| **v2-raw（v2 原样，无换栈）** | 已删除 | ✓ 100% | ✗ **同样卡循环进度条** |

v2-raw 与 btswap-raw 同症 → **BT 换栈无罪，v2 基座自身的改动即元凶**。
v2 相对 v1 的唯一内容差异 = 删除 `vendor/operator/app/` 下 6 个载荷目录
（sparse/raw 编码差异已被 v2-raw 排除）。结论：**MTK USP 首启定制流程
（services.odex 内 CustomOperator + 魔改 PackageInstaller）需要载荷目录树
存在**；目录被清空后该流程无法完成，启动卡在初始化循环。出厂
`ro.debuggable=0`+`ro.adb.secure=1`+全新 /data（ADB_ENABLED=0）意味着挂死
现场没有 adb —— 取证需 fw-debugboot.yml 产出的调试 boot.img（只刷 boot
分区，秒级，system 不动，挂死复现且 adb 常开）。

**v3 修复：stub 替换（保结构、换内容）。** 保持 6 个载荷的路径、文件名、
包名与出厂完全一致，仅把 APK 内容换成 ~600 B 的有效 stub（二进制
manifest-only，`android:hasCode=false`，jarsigner v1 签名，SELinux xattr
从原文件克隆）：

- USP/安装器首启流程照旧走通（有物可装），但装入的是零第三方代码的空壳；
- 编码器逐字节镜像本固件真实 aapt 输出的 AXML 布局（probe v8 实物对照），
  CI 用 fw_inventory 解析器 + runner aapt2 双重回读校验；
- 副作用（可接受）：设置→应用里会出现 6 个无图标的小条目；日后想侧载同
  包名真应用需先卸载 stub（签名不同）。
- 管线：`debloat/stub-list.txt` + `scripts/make_stub_apk.py` +
  fw-debloat.yml `operator_stubs` 输入；rawpack 增加 stub 记录级断言
  （expect_apks=80 / expect_stubs=6）。

## v3 上机判决与回退决策（2026-10-30 深夜）

**v3（stub 替换）同样卡初始化循环动画** —— 「USP 需要目录树存在」假设被
上机结果否定：目录树、文件名、包名全部保留、仅内容换成有效签名 stub，
首启依然挂死。

综合判决表（全分区 Download Only 含 userdata 擦除）：

| 基座 | 载荷状态 | 启动 |
|---|---|---|
| v0 / v1 | 原厂完整（203 MiB，6 APK） | ✓ |
| v2 / v2-raw | 目录已删除 | ✗ 循环进度条 |
| btswap-raw（v2 基座） | 目录已删除 | ✗ 同上（基座连带） |
| v3-raw | 路径/文件名/包名保留，内容=2.5 KB stub | ✗ 同上 |

唯一判别变量 = **载荷 APK 内容是否原厂完好**。首启流程对载荷做内容级
校验（版本号/签名/内嵌清单之类；services.odex USP 字符串
`Operator pack not valid:` 佐证校验存在），删除和掉包都过不了。确切
机制需要 adb 日志定位 —— fw-debugboot.yml 的调试 boot（只刷 boot 分区，
adb 常开）就是为此准备的取证工具，随时可用。

**决策（用户拍板）**：回退 v1 基座（上机验证可启动），BT 换栈重建在 v1
上（fw-btswap base_tag=firmware-debloat-v1-20261030, expect_apks=80,
raw_system=true）；6 个载荷应用照常首启安装，由用户手动卸载。

注意事项：
- 若手动卸载后被 USP 重装（重启后复活），说明安装流程可重复触发，需拿
  日志再战（debug boot + `pm uninstall` / 冻结路线）。
- 讯飞输入法载荷也在 6 个之列：卸载前先确认当前输入法已切到原生
  LatinIME（设置→语言和输入法），避免卸掉正在用的键盘。
