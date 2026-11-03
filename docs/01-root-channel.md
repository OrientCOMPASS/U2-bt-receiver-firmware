# docs/01 — Root 通道（唯一可用的提权方式）

## 1. 为什么需要它

- `adb root` **无效**：会打印 "restarting adbd as root"、`service.adb.root=1`，但 `id` 仍是 `uid=2000(shell)`（user/release-keys 固件）。
- `su` 不存在；Bootloader 锁（`ro.boot.flash.locked=1`），不能刷 recovery/boot。
- 但设备上有 **MTK 工程 root executor**：一个 property 触发的守护，把 `/data/local/tmp/rootexec.sh` 以 uid=0 执行。

## 2. 协议（实测，勿改顺序）

```
1. adb push <script> /data/local/tmp/rootexec.sh
2. adb shell chmod 777 /data/local/tmp/rootexec.sh
3. adb shell setprop repo.exec 0        # 复位
4. sleep 2
5. adb shell setprop repo.exec 1        # 触发执行
6. 等脚本自己把结果写到约定的输出文件，然后 adb pull
```

执行身份：`uid=0(root) gid=0(root) context=u:r:shell:s0`，且 **shell 域是 permissive**（SELinux 不拦）。

守护会在每次触发时自动重建这些文件（**正常现象**，不是残留）：
`root_dmesg`、`root_logcat`、`root_ps`、`root_props`、`snoop_ls`、`rootexec.log`（都在 `/data/local/tmp/`）。

## 3. 硬约束（踩过坑，务必遵守）

| 约束 | 原因 / 后果 |
|---|---|
| **脚本内禁止管道 `\|`** | 守护会挂死；恢复方式：`adb shell setprop ctl.stop repoexec`（有时需 `adb reboot`） |
| **长参数行会被静默截断** | 不要传超长字符串；用文件传参 |
| 输出文件尽早 `chmod 666 $O` | root 创建的文件默认 600，shell 读不到（有时 chmod 也不生效 → 换文件名重跑） |
| 不要用 `\| head` 限流 | 同管道问题；用 `head -n N file >> $O` |
| `grep` 不支持 `\|` 交替 | toybox grep；用多个 `-e` |
| 设备 `sh` **没有 awk** | 用 `sed`/`cut`/`read` |
| 每次跑用**唯一输出文件名** | 系统时钟不可信，无法靠 mtime 判断新旧 |

## 4. 工具：`tools/dev/root_run.sh`（已实测可用）

```bash
# 在沙箱里（能访问 adb 的那台机器）：
tools/dev/root_run.sh scripts/device/p0_verify_bt.sh 60
```

它自动完成：读脚本 → 注入唯一 `$O`（输出路径）→ quoted heredoc 写到设备 → chmod 777 → `repo.exec` 0/1 触发 → 轮询等待 → `adb pull` → 打印内容（>56KB 自动截断，避免撞 64KB 桥接上限）。

### 脚本模板

```sh
#!/system/bin/sh
# 注意：不用管道；结果一律 >> $O
echo "=== my probe ===" >> $O
id >> $O 2>&1
ls -l /data/tombstones >> $O 2>&1
dumpsys bluetooth_manager > /data/local/tmp/.probe_tmp 2>&1
grep -e "State" -e "enabled" /data/local/tmp/.probe_tmp >> $O 2>&1
chmod 666 $O
```

## 5. 若 root 通道失效

1. `setprop ctl.stop repoexec` → 再试一轮 0/1。
2. 检查 `/data/local/tmp/rootexec.sh` 是否存在且 777、是否被 `repo.exec` 之外的属性名（`ro.` 前缀不可写）。
3. 仍不行：`adb reboot`，重启后重新 push + 触发。
4. 记录到 docs/04（新坑即时入册）。
