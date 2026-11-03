# docs/03 — 传输与刷入

三段链路：**沙箱（构建）→ Windows（`C:\u2\`，adb 宿主）→ U2 设备**。
沙箱与 Windows 之间只有一条"命令桥"，没有文件通道，所以大件走临时文件床。

## 1. 沙箱 → Windows

```bash
# 沙箱侧：上传，打印 URL + md5
python3 tools/dev/uguu.py up bld/out/bluetooth.default.so
#   → https://uguu.se/xxxxxxxx/bluetooth.default.so
#   → md5 = df19dc6ace21e90aed41c357daccdc58
```

```powershell
# Windows 侧（通过桥接执行；--ssl-no-revoke 必带）
curl.exe --ssl-no-revoke -o C:\u2\bt_new.so "<URL>"
Get-FileHash C:\u2\bt_new.so -Algorithm MD5   # 必须与沙箱 md5 一致
```

**uguu.se 按内容识别文件类型**（不看扩展名）：

| 内容 | 结果 |
|---|---|
| ELF（`.so`） | ✅ 放行 |
| `tar`（`application/x-tar`） | ✅ 放行 |
| 纯文本 / base64 存成 `.txt` | ✅ 放行 |
| **zip / apk（PK 魔数）** | ❌ `415 Filetype not allowed`（改扩展名也没用） |

→ 需要打包时用 `tar -cf`（**不要** zip）；小文件可 base64 成 `.txt`。

Windows → 沙箱同理：Windows 侧 `tar.exe -cf` → `curl.exe --ssl-no-revoke -F "files[]=@..." https://uguu.se/upload.php` → 沙箱 `python3 tools/dev/uguu.py down <url> <out>`。

## 2. Windows → 设备

```powershell
adb push C:\u2\bt_new.so /data/local/tmp/bt_new.so
adb shell md5sum /data/local/tmp/bt_new.so      # 第三段校验
# 写入门控用的目标 md5（flash_bt_so.sh 会比对）
adb shell "echo df19dc6ace21e90aed41c357daccdc58 > /data/local/tmp/bt_want.txt"
```

## 3. 原子刷入（`scripts/device/flash_bt_so.sh`）

**为什么必须原子**：`/system/lib/hw/bluetooth.default.so` 在 BT 进程里是**已映射**的；
直接 `cp` 覆盖会让运行中的进程 **SIGBUS**。

配方（脚本已内建，通过 root executor 跑）：

```sh
mount -o rw,remount /system
cp /data/local/tmp/bt_new.so /system/lib/hw/.bt_swap.tmp
chmod 644 .bt_swap.tmp ; chown 0:0 .bt_swap.tmp ; restorecon .bt_swap.tmp
mv .bt_swap.tmp /system/lib/hw/bluetooth.default.so     # ← 原子安装
sync
mount -o ro,remount /system
kill $(pidof com.android.bluetooth)                     # zygote 自动重启 BT 进程
md5sum /system/lib/hw/bluetooth.default.so              # 复核
```

**门控**（脚本自动做，别绕过）：

1. `/data/local/tmp/bt_stock_backup.so` 必须存在（原厂备份）——否则拒绝刷入。
2. `bt_new.so` 的 md5 必须等于 `bt_want.txt`——否则判定传输损坏，拒绝刷入。
3. `bt_want.txt` 等于当前已装 md5 → **SKIP**（禁止重复刷）。

执行方式：

```bash
tools/dev/root_run.sh scripts/device/flash_bt_so.sh 120
```

## 4. 刷入后验证

```bash
tools/dev/root_run.sh scripts/device/p0_verify_bt.sh 90
```

检查项：

- `dumpsys bluetooth_manager` → **STATE_ON**
- 适配器地址 / 名称正常（`00:00:46:03:35:01` / `U2`）
- `ls /data/tombstones/` → **零新增**
- `logcat -b crash` 无 BT 相关崩溃
- `settings get global bluetooth_on` = 1

若适配器起不来：先 `adb reboot`（**不要**反复 killall，可能不回 STATE_ON）。

## 5. 回滚

```sh
# 通过 root executor（单变量原则：只回滚 .so）
mount -o rw,remount /system
cp /data/local/tmp/bt_stock_backup.so /system/lib/hw/.bt_swap.tmp
chmod 644 /system/lib/hw/.bt_swap.tmp ; chown 0:0 /system/lib/hw/.bt_swap.tmp
restorecon /system/lib/hw/.bt_swap.tmp
mv /system/lib/hw/.bt_swap.tmp /system/lib/hw/bluetooth.default.so
sync ; mount -o ro,remount /system
md5sum /system/lib/hw/bluetooth.default.so   # 应为 e0dd373e4632316a0348e6ec0a288347
```

## 6. Windows PowerShell 注意事项

- 路径含反斜杠 / PnP InstanceId → 用 bash **quoted heredoc**（`cat <<'PSEOF'`）构造，再整体传给 `rc.sh`。
- `>` 重定向写出的是 **UTF-16**；读设备 UTF-8 文件用 `[IO.File]::ReadAllText($p,[Text.Encoding]::UTF8)`。
- 单条命令输出 **64KB 上限**；大文件用 `Select-Object -Skip N -First M` 分段。
- 桥接命令里**绝不能**跑 `adb kill-server` / `adb start-server`（会卡死远端代理，见 docs/04 A1）。

## 7. 配对缓存（改 caps 后必做）

Windows 蓝牙栈会**缓存编解码偏好**：改了 BT caps / COD 之后，必须

1. 在 Windows 上**删除并重新配对**设备；
2. 重连配方：`Stop-Service bthserv; Start-Service bthserv` + `Disable-PnpDevice`/`Enable-PnpDevice` + `sleep 90`；
3. 实测常需 **2–3 轮**才生效。

Android 手机侧同理：忘记设备 → 重新配对。
