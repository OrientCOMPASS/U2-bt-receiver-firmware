#!/system/bin/sh
# p1e_probe_input.sh — 摸清 U2 的输入设备（耳机线控按键）与音频采集能力
#
# 目的（对应 docs/05 §3.9(B) P1e 第一步、§3.10 P1f 音频层判定）：
#   1) 找出线控/耳机按键挂在哪个 /dev/input/eventX，支持哪些 KEY_*
#      → P1e 需要把这些按键映射成 AVRCP passthrough（CT 方向，控制手机播放）
#   2) 看耳机插入检测（h2w switch）有没有节点 → 判断"插了线控"这件事能不能被感知
#   3) 顺手采集声卡/PCM 设备清单 → P1f 真麦克风上行是否有可用采集设备
#
# 通过 tools/dev/root_run.sh 以 root 运行；本脚本禁用管道（|），只用重定向。
# $O 由 root_run.sh 注入为唯一输出文件名。

exec >"$O" 2>&1
chmod 666 "$O" 2>/dev/null

echo "===== P1e/P1f 输入与音频采集探测  $(date) ====="

echo
echo "----- 1. /proc/bus/input/devices (Name / Handlers=eventX / capabilities) -----"
cat /proc/bus/input/devices

echo
echo "----- 2. ls -l /dev/input -----"
ls -l /dev/input

echo
echo "----- 3. getevent -p (每个输入设备的能力位，含 KEY_*) -----"
getevent -p

echo
echo "----- 4. 耳机插入检测 switch -----"
for f in /sys/class/switch/h2w/state /sys/class/switch/h2w/name \
         /sys/devices/virtual/switch/h2w/state \
         /sys/class/extcon/extcon*/state /sys/class/extcon/extcon*/name; do
  if [ -e "$f" ]; then
    echo "[$f]"
    cat "$f"
  fi
done
ls /sys/class/switch 2>/dev/null
ls /sys/class/extcon 2>/dev/null

echo
echo "----- 5. 声卡 / PCM 设备清单（P1f 麦克风上行） -----"
cat /proc/asound/cards
echo "-- pcm --"
cat /proc/asound/pcm
echo "-- /dev/snd --"
ls -l /dev/snd

echo
echo "----- 6. 与音频输入相关的 prop -----"
getprop ro.audio.silent
getprop ro.hardware.audio.primary
getprop persist.service.bdroid.soc.alc
getprop ro.mediatek.platform
getprop ro.board.platform

echo
echo "----- 7. AudioRecord 可见性快速判定：audio_policy 配置里的 input -----"
for f in /vendor/etc/audio_policy.conf /system/etc/audio_policy.conf \
         /vendor/etc/audio/audio_policy_configuration.xml \
         /system/etc/audio/audio_policy_configuration.xml; do
  if [ -e "$f" ]; then
    echo "[FOUND] $f"
    ls -l "$f"
  fi
done

echo
echo "===== 探测结束 ====="
chmod 666 "$O" 2>/dev/null
