# AOSP 8.1 构建避坑清单（Ubuntu 16.04 live 构建机实录）

> 2026-10-28 实测于 KeenPlus U2 项目的 8.1 构建机：
> AMD Ryzen 9 7940H（8C/16T）、14 GB RAM、USB3 外置盘（ext4，直写 348 MB/s，
> 2 TB 分区）、Ubuntu 16.04.6 **live 会话**（aufs cow 层 ≈ 内存盘）。
> 所有脚本存放于构建盘 `<USB>/tools/`（setup_env.sh / boot_tar.sh / nosleep.sh /
> build81.sh / st.sh），live 重启后依次重跑 setup_env.sh、nosleep.sh 即可复原。

## 1. 源码获取：国内镜像现状（2026-10 实测）

| 通道 | 状态 | 实测/规则 |
|---|---|---|
| USTC git（`mirrors.ustc.edu.cn/aosp/`） | ⚠️ 后端劣化 | 浅 fetch 90s 无进展；**官方规则：并发 >4 可能封 IP**；避开每日 04:30–06:00 同步窗口 |
| **USTC monthly tar**（`aosp-monthly/aosp-latest.tar`） | ✅ 首选 | 101 GB 全 refs 打包，HTTP 直下 30–47 MB/s，`wget -c` 断点续传 |
| TUNA git | ❌ 排队制 | `Waiting in queue (Position: 242)`，598 仓逐个排队不可行 |
| TUNA tar | ❌ | 15s 下载 0 字节（限流） |
| SUSTech | ❌ 已下线 | 全路径 404（帮助页仍在） |
| NJU / Aliyun / Tencent / Huawei | ❌ | 无 AOSP git 镜像 |
| USTC LUG 代理（`gerrit-googlesource.proxy.ustclug.org`） | ✅ 仅 repo 工具 | 只代理 git-repo，不代理 AOSP 本体 |
| GitHub | ⚠️ 抽风 | `GerritCodeReview/git-repo` 克隆成功过；隔 30 分钟 TLS 被断——不可依赖，小仓库可多试 |

**定案流程**（boot_tar.sh 全自动）：
`wget -c tar → sha256 校验 → 解压出 AOSP/.repo → git -C .repo/manifests.git
remote set-url 指回 USTC → repo init -b android-8.1.0_r80 --no-tags -c →
repo sync -l -j8`（**本地 checkout，零网络**；monthly tar 为全 refs 镜像，
android-8.1.0_r80 tag 在包内，管道会先行验证）。

## 2. repo / python / git 组合坑

- 16.04 系统 python3 = **3.5.2**，现代 repo（stable）要求 ≥3.6 →
  用 python-build-standalone **3.11**（构建盘 `tools/python311/`）显式调用：
  `python3.11 <git-repo>/repo …`。
- repo launcher 从**本地 git-repo 克隆**引导时必须能解析 `--repo-rev`
  指定的分支：浅克隆（--depth=1 单分支）会因缺 `main`/`stable` 引用而失败
  （实测 `unknown branch "main"`）。→ 完整克隆 GitHub `GerritCodeReview/git-repo`。
- 新版 `repo init -u` **不会**给镜像根路径自动补 `platform/manifest`：
  必须写全 `…/aosp/platform/manifest`（v3 实测 404）。
- git 2.7.4 + repo stable + py3.11 组合可用；若后续版本抱怨 git 过老，
  回落 `git -C tools/git-repo checkout v2.17.3`（全量克隆含所有 tag）。
- git 全局身份先配好（build 过程有 git 调用）：本项目统一
  `OrientCOMPASS <316423573+OrientCOMPASS@users.noreply.github.com>`。

## 3. 内存（14 GB 无原生 swap 的最大死因）

- **swapfile 32 GB 置于 USB ext4**（setup_env.sh 自动建，swappiness=10）——
  Java/dex 阶段与 libLLVM 级链接的 OOM 保险。
- 并发红线：**`m -j8`**（16T CPU 但 14G RAM；-j16 必炸）；出现 OOM 再降 -j4。
- 8.1 **没有 jack-server**（那是 7.x 的 3GB 大坑），但 dx/dexpreopt 仍吃内存；
  eng 变体比 userdebug 多编测试件，首建用 eng 验证、产物用 userdebug 可省时间。
- **live 会话的 aufs cow 层就是内存**：apt 安装足迹 ~1.3 GB 直接占 RAM；
  不要在 live 系统里再装大件；ccache（50 GB 上限）指向 USB 盘。
- 构建前关掉桌面大户（firefox 等，实测 GUI 基线 ~1 GB 可接受）。

## 4. 构建顺序（先烟测后全量）

1. `mmm system/bt`（补丁后首验，30 min 量级，验证工具链+RAM）；
2. `m -j8` 全量（首建 4–8 h，`aosp-build.service` systemd 单元承载）；
3. 之后 `m snod` 增量重打 system.img（分钟级）。
- lunch 目标：设备为 32 位 armv7（B1 实测 vendor 全 ELF32）→ `aosp_arm-eng`；
  混合重打包按 `docs/INCREMENTAL_BUILD.md` / build.yml 既有设计走。
- out/ 预计 120–200 GB；解压确认后可删 101 GB tar 回收空间。
- r80 补丁系列（system_bt×4、hardware_libhardware×1、packages_apps_Bluetooth×1）
  在 CI 已对 fresh r80 克隆反复 git-am 验证，树就绪即套用。
- 8.1 原生自带：a2dp vendor codec 框架、LDAC source、external/aac
  （libFraunhoferAAC）→ F7 免移植；补丁只补 AAC sink / SBC-XQ / 双设备
  StreamHandler。

## 5. 长任务承载与会话保活（血的教训）

- **`nohup … &` 逃不过 systemd 会话 cgroup 清理**：bridge/SSH 会话一断，
  后代全灭（实测 wget 被杀、dmesg 无痕、日志 0 字节）。
  → 一律 `sudo systemd-run --unit=<名> --property=User=ubuntu /bin/bash -c '…'`
  挂成系统级瞬态单元；同名单元天然防重入，`systemctl is-active` 即状态。
- 防休眠三件套（nosleep.sh，幂等）：
  `systemctl mask sleep/suspend/hibernate/hybrid-sleep.target`；
  logind `HandleLidSwitch*=ignore` + `IdleAction=ignore`（合盖不断）；
  `usbcore.autosuspend=-1`（**USB 外置盘掉电=构建全灭**）+ `xset s off -dpms`。
- bridge 远程排查脚本时 `pkill -f` 的模式串会匹配到承载命令自身
  （bash -c 的 cmdline 含同一字符串）→ **自杀**。用 `[x]xx` 括号技巧，
  且确保同一条命令里其他地方（如脚本路径）不含该字面量。

## 6. USB 外置盘专项

- dmesg 无 reset/disconnect 记录；直写 348 MB/s（oflag=direct）。
- 随机 IO 弱于 NVMe：ninja 小文件阶段会慢，-j8 已兼顾；勿把 out/ 放 aufs。
- 分区根目录属 root：工作子目录先 `sudo chown ubuntu` 再作业。
- 机器另有两块 NVMe（Windows 系统盘）——**不碰**；一切只在指定 ext4 分区。
