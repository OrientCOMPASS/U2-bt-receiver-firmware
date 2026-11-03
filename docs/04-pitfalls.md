# docs/04 — 坑册（实测，随时补充）

> 规则：每个坑都要写清 **现象 → 根因 → 规避做法**。新坑即时入册（铁律 7）。

## A. 桥接 / 远控链路

| # | 现象 | 根因 | 规避 |
|---|---|---|---|
| A1 | 握手成功、`ping` 超时，之后几分钟内所有命令都无响应 | 在桥接命令里执行了 `adb kill-server`（或 `adb root` 后紧接 kill-server），Windows 侧代理被卡死 | **绝不在桥接命令里跑 `adb kill-server`/`adb start-server`**；`adb root` 本机也无效（见 C1）。代理会在那条远端命令超时后自愈（实测 2–3 分钟），期间只能等 |
| A2 | 本地 bash 30s 后被杀，远端命令还在跑，下一轮又撞上 A1 | 沙箱 bash 工具默认超时 30s | 跑桥接命令时显式传 `timeout`（例如 420），并用 `RC_TIMEOUT` 控制远端超时 |
| A3 | 输出被截断，看不到中间内容 | 桥接单轮消息上限 64KB（头尾保留） | 大输出落设备/Windows 文件，再分段 `Get-Content -TotalCount N` / `Select-Object -Skip N -First M` |
| A4 | 设备侧 UTF-8 文件回显成乱码（`鍓嶇疆妫€鏌`） | PowerShell `Get-Content` 默认按本地代码页(GBK)解码；`>` 重定向还会写成 UTF-16 | 用 `[IO.File]::ReadAllText($p,[Text.Encoding]::UTF8)` 读；写文件用 `Set-Content -Encoding ascii` 或 `[IO.File]::WriteAllBytes` |
| A5 | 含反斜杠路径 / PnP InstanceId 的命令被转义毁掉 | printf/引号嵌套转义 | 一律用 bash quoted heredoc（`cat <<'PSEOF'`）构造 PowerShell 文本，再作为单个参数传给 `rc.sh`；需要变量替换时用占位符 + `${VAR//__X__/...}` |

## B. 文件传输（沙箱 ↔ Windows ↔ 设备）

| # | 现象 | 根因 | 规避 |
|---|---|---|---|
| B1 | uguu.se 返回 `415 Filetype not allowed` | **按内容识别**，zip/apk（PK 魔数）被拒，改扩展名（.so/.bin/.dat）也没用 | 用 **tar**（`tar.exe -cf`，mimetype application/x-tar 放行）；或 base64 成 `.txt`；ELF(.so)/纯文本直接放行 |
| B2 | Windows 侧下载失败/证书报错 | 老 PS 默认没开 TLS1.2；企业根证书吊销检查 | `curl.exe --ssl-no-revoke`；必要时 `[Net.ServicePointManager]::SecurityProtocol = Tls12` |
| B3 | 传输后文件损坏却继续刷入 | 没有端到端校验 | 三段都校验 md5：沙箱 `uguu.py up` 打印 md5 → Windows `Get-FileHash` 比对 → 设备 `md5sum` 比对（`scripts/device/flash_bt_so.sh` 已内建门控） |

## C. 设备侧

| # | 现象 | 根因 | 规避 |
|---|---|---|---|
| C1 | `adb root` 打印 "restarting adbd as root"，`service.adb.root=1`，但 `id` 仍是 `uid=2000(shell)` | user/release-keys 固件的 adbd 不真正提权 | 用 root executor 通道（docs/01-root-channel.md） |
| C2 | `setprop repo.exec 1` 后 `Service 'repoexec' exited with status 127` | executor 找的不是 `cmd.txt`；本机必须是 `/data/local/tmp/rootexec.sh` 且**可执行** | 用 `tools/dev/root_run.sh`（已内建 push + chmod 777 + 0→1 触发） |
| C3 | `grep "a\|b" file` 恒不命中 | **toybox grep 不支持 BRE 的 `\|` 交替** | 用多个 `-e`：`grep -e a -e b`；限制条数用 `-m N`（不能用 `| head`，见 C4） |
| C4 | root 脚本里用管道后守护挂死/无输出 | 任务书明确：脚本内禁用管道 | 只用重定向；需要 head/tail 就用 `head -n N file >> $O` |
| C5 | 输出文件读不到（root 600） | root 创建的默认权限 | 脚本结尾 `chmod 666 $O`；仍不行就换文件名重跑 |
| C6 | 时间戳乱序、`ls -lt` 不可信 | 设备系统时钟错乱（RTC/时区），日志里出现 2015-01-01、2026-xx 混杂 | 排序按 pid / 按日志顺序；`root_run.sh` 每次用唯一输出文件名，不靠 mtime 判断新旧 |
| C7 | `svc bluetooth enable` 只打印 usage | 本机 svc 不支持 bluetooth 子命令 | `service call bluetooth_manager 6`（IBluetoothManager.enable()）；关：`service call bluetooth_manager 8` |
| C8 | `kill $(pidof com.android.bluetooth)` 打印 kill usage、rc=1 | BT 未启动时 pidof 为空 | 先判空再 kill；或接受无害报错 |
| C9 | 连续 killall BT 进程后适配器不回 STATE_ON | 任务书已警告 | 需要干净重启时用 `adb reboot` |
| C10 | logcat 里几乎没有 bt_ 日志 | `/system/etc/bluetooth/bt_stack.conf` 里 `TraceConf=true` 且 `TRC_*=2`（只到 WARNING） | 调试时把 `TRC_*` 提到 6（`bt_stack.conf.debug` 就是全 6 的版本），并开 `BtSnoopLogOutput=true`（snoop 落 `/sdcard/mtklog/btlog/btsnoop_hci.log`） |
| C11 | `ls /sbin`、`cat /init.rc` 权限拒绝 | 750 root:root，shell 非 root | 用 root executor 读（root 可读 /init.rc） |

## D. 沙箱 / 工程

| # | 现象 | 根因 | 规避 |
|---|---|---|---|
| D1 | 仓库里的 `build/` 目录下一轮会话凭空消失 | 工作区快照**排除**名为 `build`/`out`/`dist`/`target`/`node_modules` 等的目录 | 构建配置目录改名 **`bld/`**；产物放 `bld/out/`（正好不进快照，也不污染仓库） |
| D2 | `/tmp` 里的 NDK、AOSP 树、protoc 全没了 | 每轮会话 `/tmp` 清空 | `tools/build/setup_env.sh --all` 重建；设备库快照持久化在 `<workspace>/devlibs/` |
| D3 | `.git/config` 丢失、remote 没了 | 快照排除 `.git/config` | 每轮用 PAT 从 `CREDENTIALS.txt` 重新加 remote（见 docs/03） |
| D4 | `git clone aosp-mirror/...` 报 `could not read Username` | 那些镜像仓库不存在（404 → git 转去要认证） | 一律用 `android.googlesource.com`；注意 `platform/external/fdk-aac` 在 googlesource 上也不存在，P2 需另找 fdk-aac 源 |
| D5 | 内存 ~1GB，编译卡死/OOM | 沙箱内存限制 | `--jobs 2`（参数写法是 `--jobs N`，不是 `-jN`）；构建目录放 `/tmp` |

## E. 构建 / ABI

| # | 现象 | 根因 | 规避 |
|---|---|---|---|
| E1 | 链接一堆 `std::__ndk1::*` 未定义 | NDK 的 libc++ 用 `__ndk1` inline namespace，设备 `/system/lib/libc++.so` 是 `std::__1` | `-nostdinc++` + AOSP `external/libcxx @ r36` 头 + 链接设备的 `libc++.so`（`-nostdlib++`） |
| E2 | `linux/binder.h: No such file` | NDK r21e sysroot 精简掉了它，而 `binder/Parcel.h` 需要 | `bld/shim_include/`（从 bionic r36 原样复制一个头，放 include 顺序最后） |
| E3 | protobuf 头 `use of dynamic_cast requires -frtti` | 我们全程 `-fno-rtti`（与原厂一致：0 个 `_ZTI*`） | 定义 `-DGOOGLE_PROTOBUF_NO_RTTI`（官方支持路径，MergeFrom 退化为反射实现） |
| E4 | `bluetooth.pb.cc` 找不到自己的 `.pb.h` | protoc 的 `--cpp_out` 根目录搞错，生成到多套了一层 `system/bt/` | `--proto_path=<top>` + `--cpp_out=bld/out/gen/proto`，include 根同时给 `.../gen/proto` 和 `.../gen/proto/system/bt` |
| E5 | `undefined reference to hw_get_module` | 原厂把 `hardware/libhardware/hardware.c` 静态编入（DT_NEEDED 里没有 libhardware.so） | 我们也静态编入同一个文件（`EXTRA_AOSP_SOURCES`），保持 DT_NEEDED 与原厂一致 |
| E6 | `-llibcutils`（多了 lib 前缀） | 设备库文件名与 `-l` 参数写法混淆 | `build_bt.py` 里统一 `_lname()` 去前缀/后缀 |
| E7 | 顶层 Android.mk 里的 `bluetooth_CFLAGS` 展开成空 | 顶层 mk 结尾会 `bluetooth_CFLAGS :=`（清空），真实 make 是先展开子目录再清空 | `mk2plan.py` 解析顶层文件时在 `include $(call all-subdir-makefiles)` 处**停住** |
| E8 | 编译期符号全过、设备上 `dlopen` 失败 | 引用了设备上不存在的符号（NDK 新版 libc / libchrome 版本差异） | `tools/build/audit_symbols.py` 递归解析 DT_NEEDED，刷入前必须 0 个未解析强符号 |
