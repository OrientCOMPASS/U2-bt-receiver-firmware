#!/usr/bin/env bash
# root_run.sh — 通过 root executor 通道在 U2 设备上以 root 身份执行脚本
#
# 实测协议（本机 debug boot，init banner = repo-rootexec-v6-propexec）：
#   1) 脚本推到 /data/local/tmp/rootexec.sh   ← 注意：不是 cmd.txt！
#   2) chmod 777（必须可执行，否则 init execve 失败 → status 127）
#   3) setprop repo.exec 0 → sleep 2 → setprop repo.exec 1
#   4) init 以 root(uid=0, context=u:r:shell:s0 permissive) 启动 service 'repoexec'
#   5) 脚本自己负责把输出写到 $O（本工具注入的唯一文件名）并 chmod 666
#
# 规矩（任务书 + 实测）：
#   - 脚本内禁用管道（|），只用重定向；管道可能让守护挂死（恢复：setprop ctl.stop repoexec）
#   - 长参数行会被静默截断 → 命令拆短行
#   - 输出文件尽早 chmod 666（root 600 就重跑或换文件名）
#   - 输出超 64KB 会被桥接截断 → 这里做本地头尾裁剪
#
# 用法:
#   tools/dev/root_run.sh <script.sh> [wait_sec]
# 环境变量:
#   RC          远控封装脚本路径（默认 <repo>/../../rc.sh）
#   RC_TIMEOUT  单次 bridge 命令超时（默认 240）
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WS="$(cd "$HERE/../../.." && pwd)"
RC="${RC:-$WS/rc.sh}"
SCRIPT="${1:?用法: root_run.sh <script.sh> [wait_sec]}"
WAIT="${2:-10}"

[ -f "$SCRIPT" ] || { echo "root_run: 找不到脚本 $SCRIPT" >&2; exit 2; }
[ -x "$RC" ] || { echo "root_run: 找不到 rc.sh ($RC)" >&2; exit 2; }

STAMP="$$_$(date +%s)"
REMOTE_OUT="/data/local/tmp/root_out_${STAMP}.txt"

# 注入输出路径：保留 shebang 在第一行，第二行插入 O=<唯一输出文件>
INJECT="$(mktemp)"
head -1 "$SCRIPT" > "$INJECT"
echo "O=$REMOTE_OUT" >> "$INJECT"
tail -n +2 "$SCRIPT" >> "$INJECT"
B64="$(base64 -w0 "$INJECT")"
rm -f "$INJECT"

# PowerShell 模板（quoted heredoc：不做 bash 展开，占位符随后替换）
read -r -d '' PSTPL <<'PSEOF' || true
[IO.File]::WriteAllBytes('C:\u2\rootexec.sh',[Convert]::FromBase64String('__B64__'))
adb push C:\u2\rootexec.sh /data/local/tmp/rootexec.sh
adb shell chmod 777 /data/local/tmp/rootexec.sh
adb shell rm __REMOTE_OUT__
adb shell setprop repo.exec 0
Start-Sleep -Seconds 2
adb shell setprop repo.exec 1
Start-Sleep -Seconds __WAIT__
adb shell getprop init.svc.repoexec
adb pull __REMOTE_OUT__ C:\u2\__OUTNAME__
if (Test-Path C:\u2\__OUTNAME__) {
  "----- __OUTNAME__ -----"
  $t = [IO.File]::ReadAllText('C:\u2\__OUTNAME__',[Text.Encoding]::UTF8)
  if ($t.Length -gt 56000) {
    "OUTPUT TRUNCATED by root_run: head 40000 + tail 12000 of " + $t.Length + " chars"
    $t.Substring(0,40000)
    "...[SNIP]..."
    $t.Substring($t.Length-12000)
  } else { $t }
} else {
  "NO_OUTPUT_FILE (executor 未运行或脚本失败)"
  adb shell ls -la /data/local/tmp
  adb shell dmesg > C:\u2\dm.txt
  Select-String -Path C:\u2\dm.txt -Pattern "repoexec" | Select-Object -Last 4
}
PSEOF

PS="${PSTPL//__B64__/$B64}"
PS="${PS//__REMOTE_OUT__/$REMOTE_OUT}"
PS="${PS//__OUTNAME__/root_out_${STAMP}.txt}"
PS="${PS//__WAIT__/$WAIT}"

RC_TIMEOUT="${RC_TIMEOUT:-240}" "$RC" "$PS"
