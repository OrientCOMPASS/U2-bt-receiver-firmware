# p1d2_vol_test.ps1 — Windows 侧刺激脚本：让 Windows 对 U2 下发 AVRCP SetAbsoluteVolume
#
# 编码要求（踩过的坑，务必保持）：本文件必须存成 UTF-8 **with BOM** + CRLF。
#   PowerShell 5.1 只有在看到 BOM 时才按 UTF-8 解码；否则按系统 ANSI（中文机 = GBK）。
#   而 GBK 的尾字节范围含 0x40-0x7E：中文标点（例如 U+FF09 "）" = EF BC 89）的末字节
#   0x89 是合法 GBK 首字节，会把紧随其后的 ASCII 引号一起吃掉，于是报
#   "字符串缺少终止符" 并级联出一堆 Missing closing '}'。仓库里用 python 生成时
#   请写 encoding='utf-8-sig', newline='\r\n'。
#
# 为什么需要它：P1d2 修的是"Windows 拖音量滑块 → U2 音量跟着变"。验收不能靠人耳，
# 要拿 snoop（应看到 tx RSP ACCEPTED 而不是 REJECTED）与设备侧 logcat
# （应看到 SetAbsoluteVolume → rc gain → AudioTrack setVolume）。本脚本把"拖滑块"
# 变成可重复的自动化：
#   1) 循环播放一段系统 wav —— 把 A2DP 流拉起来，设备侧才会创建 AudioTrack；
#   2) 注入 VK_VOLUME_DOWN / VK_VOLUME_UP —— Windows 会把它作用在默认播放端点上；
#      若默认端点是"扬声器 (U2)"且该端点支持绝对音量（我们的 GetCapabilities 已广告
#      EVENT_VOLUME_CHANGED = 0x0d），Windows 就会发 SetAbsoluteVolume。
#
# 前提：U2 必须是 Windows 的默认播放设备（设置 → 系统 → 声音 → 输出）。
#   若不是，音量键会作用到别的端点，snoop 里将没有任何 AVCTP 音量帧。
#
# 用法（在 Windows 上）：
#   powershell -ExecutionPolicy Bypass -File C:\u2\p1d2_vol_test.ps1
#   powershell -ExecutionPolicy Bypass -File C:\u2\p1d2_vol_test.ps1 -PlaySeconds 60 -Down 12 -Up 12
#
# 本脚本只注入按键与播放声音：不改注册表、不动配对、不重启服务。

param(
    [int]$PlaySeconds = 45,
    [int]$Down = 8,
    [int]$Up = 8
)

$ErrorActionPreference = 'Continue'
"=== p1d2 windows volume stimulus  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ==="

# --- 0. 环境自检：U2 是否连着、音频端点在不在 -------------------------------
"--- 0. bluetooth / audio endpoint check ---"
Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue |
    Where-Object { $_.FriendlyName -match 'U2' } |
    Select-Object Status, FriendlyName | Format-Table -AutoSize | Out-String -Width 200
Get-PnpDevice -Class AudioEndpoint -ErrorAction SilentlyContinue |
    Where-Object { $_.FriendlyName -match 'U2' } |
    Select-Object Status, FriendlyName | Format-Table -AutoSize | Out-String -Width 200

# --- 1. 注入按键用：user32!keybd_event -------------------------------------
# 刻意不用 here-string（@' ... '@）：对行尾/编码最敏感。单引号普通字符串里的
# 双引号无需转义。
$u2_sig = '[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, System.UIntPtr dwExtraInfo);'
Add-Type -Namespace U2 -Name Key -MemberDefinition $u2_sig

function Send-VolKey([byte]$vk, [int]$n, [string]$name) {
    "--- sending $name x$n ---"
    for ($i = 0; $i -lt $n; $i++) {
        [U2.Key]::keybd_event($vk, 0, 0, [UIntPtr]::Zero)          # key down
        Start-Sleep -Milliseconds 50
        [U2.Key]::keybd_event($vk, 0, 2, [UIntPtr]::Zero)          # KEYEVENTF_KEYUP
        Start-Sleep -Milliseconds 420   # 间隔要够长，否则 Windows 会合并音量事件
    }
}

# --- 2. 起播：SoundPlayer 优先，失败退回 WMPlayer.OCX -----------------------
$wav = 'C:\Windows\Media\Windows Notify System Generic.wav'
if (-not (Test-Path $wav)) {
    $cand = Get-ChildItem 'C:\Windows\Media\*.wav' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cand) { $wav = $cand.FullName }
}
"wav=$wav"

$sp = $null
$wmp = $null
try {
    $sp = New-Object System.Media.SoundPlayer $wav
    $sp.PlayLooping()
    "playback: System.Media.SoundPlayer (looping)"
} catch {
    "SoundPlayer failed: $($_.Exception.Message) -> fallback to WMPlayer.OCX"
    try {
        $wmp = New-Object -ComObject WMPlayer.OCX
        $wmp.URL = $wav
        $wmp.settings.setMode('loop', $true)
        $wmp.controls.play()
        "playback: WMPlayer.OCX (looping)"
    } catch {
        "WMPlayer failed too: $($_.Exception.Message)"
        "no audio stream; protocol-level check still valid (snoop should show ACCEPTED)"
    }
}

Start-Sleep -Seconds 10   # 给 A2DP 建流 + 设备侧 AudioTrack 创建留时间

# --- 3. 音量刺激：先降到底（abs_vol 应到 0，设备侧必须全静音），再升回 -------
Send-VolKey 0xAE $Down 'VK_VOLUME_DOWN'
Start-Sleep -Seconds 3
Send-VolKey 0xAF $Up 'VK_VOLUME_UP'
Start-Sleep -Seconds 4

# --- 4. 收尾 ----------------------------------------------------------------
if ($sp) { $sp.Stop() }
if ($wmp) {
    $wmp.controls.stop()
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($wmp)
}
"playback stopped"
"=== end $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ==="
