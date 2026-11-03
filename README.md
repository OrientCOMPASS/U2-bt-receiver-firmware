# U2 Bluetooth A2DP **SINK** Receiver Firmware

把一台 **U2 手机（MT6735 / Android 7.0 / SDK 24）** 改造成蓝牙音频**接收器（扬声器）**：
交付物是 `/system/lib/hw/bluetooth.default.so`（**只改这一个文件**，不换 APK）。

从 **干净的官方固件 + 干净的 AOSP 源码** 从零构建；不使用任何历史 Release 里的
蓝牙栈代码或二进制，不引入任何闭源 blob。

---

## 状态

| 阶段 | 主题 | 状态 |
|---|---|---|
| **P0** | 干净 AOSP `bluetooth.default.so` 可构建 / 可原子刷入 / BT 起得来 | ✅ **达成**（Release `p0-baseline-build-flash-20261104`） |
| **P1a** | `-DUSE_AUDIO_TRACK` + native 主动启用 A2DP SINK service | ✅ **已刷入验证**（STATE_ON，零 tombstone） |
| **P1b** | 扬声器身份 COD 0x240414 + 常驻可发现（HCI 层实证） | ✅ **已刷入验证** |
| P1c/P1d | SBC 48k 收敛 + 真机出声 + AVRCP 播放/暂停/音量 | ⏳ 进行中 |
| P2 | AAC（fdk-aac，LATM） | ⏳ |
| P3 | LDAC（libldac 2.0.72 全源码静态链接） | ⏳ |
| P4 | 一拖二（2 台手机、单路活跃流、切换 ≤1s、24h） | ⏳ |
| P5 | 72h 老化（零 tombstone / 零 EBADF / 无可闻断音） | ⏳ |
| P6 | 工程化收尾（Release + 文档 + 回滚） | ⏳ |

**全局硬约束：48kHz 全链路**（codec 协商 → 解码输出 → AudioTrack 采样率），44.1k 仅作回退。

---

## 目录

```
src/                     vendored AOSP platform/system/bt @ android-7.0.0_r36（此后所有改动属于本项目）
external/                vendored 第三方（tinyxml2；P2 加 fdk-aac，P3 加 libldac）
bld/                     构建配置（plan.json / UPSTREAM.json / env.json / bdroid_buildcfg / shim_include）
bld/out/                 构建产物（不入库）
tools/build/             setup_env.sh（环境重建）· mk2plan.py（Android.mk→编译计划）
                         build_bt.py（NDK 独立构建）· audit_symbols.py（刷入前符号审计）
tools/dev/               root_run.sh（root 通道封装）· uguu.py（文件传输）
scripts/device/          flash_bt_so.sh（原子刷入）· p0_verify_bt.sh · p0_probe2.sh · p0_profiles.sh
docs/                    00 设备事实 · 01 root 通道 · 02 构建系统 · 03 传输与刷入
                         04 坑册 · 05 架构决策与路线图
WORKLOG.md               工程日志（每日更新，新坑即时入册）
```

## 快速开始（全新会话）

```bash
bash tools/build/setup_env.sh --all          # 重建 /tmp 里的 NDK + AOSP 头树 + host protoc

python3 tools/build/mk2plan.py --src src --external external \
        --aosp /tmp/build --buildcfg bld/bdroid_buildcfg --out bld/plan.json
python3 tools/build/build_bt.py --jobs 2     # 312 obj，产物 bld/out/bluetooth.default.so
python3 tools/build/audit_symbols.py bld/out/bluetooth.default.so \
        --devlibs ../devlibs --ndk /tmp/ndk/android-ndk-r21e   # 必须 0 未解析强符号

tools/dev/root_run.sh scripts/device/flash_bt_so.sh 120        # 原子刷入（md5 门控 + 原厂备份门控）
tools/dev/root_run.sh scripts/device/p0_verify_bt.sh 90        # STATE_ON + 零 tombstone
```

详见 `docs/02-build-system.md`、`docs/03-flash-and-transfer.md`。

## 铁律（工程纪律）

1. 源码基线固定 **`android-7.0.0_r36`**（tag 显式指定，不跟 HEAD）。
2. **一次 build-flash-test 循环只改一个变量**；刷入后立刻记录 md5 + 行为。
3. `.so` 刷入**必须原子**（`cp → .bt_swap.tmp` → `mv` → `sync`）；直接 cp 覆盖被映射的文件会 SIGBUS。
4. 刷入前必须过 **符号审计**（所有 UND 都能在设备真实 `.so` 里解析）。
5. 不改与厂商二进制共享的结构体布局。
6. media 热路径零新增分配；新结构体单独分配 + canary。
7. 内存异常（EBADF / 野指针 / 静默存活）先上 **malloc-debug 取证**，**不猜改、不"自愈"掩盖**。
8. 每阶段 commit + Release（`pN-topic-date`）+ WORKLOG；**push / release 前先征得用户确认**。
9. 凭据只存在于工作区根的 `CREDENTIALS.txt`，**绝不进仓库 / commit / log / Release**。

## License

见 [LICENSE](LICENSE)。AOSP 部分为 Apache-2.0；LDAC 部分遵循 open-vela/external_libldac 的授权条款。
