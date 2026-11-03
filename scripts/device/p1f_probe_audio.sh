#!/system/bin/sh
# p1f_probe_audio.sh — 为 P1e(线控→AVRCP CT) 与 P1f(HFP SCO 音频通路) 取事实
#
# 上一轮 p1e_probe_input.sh 已发现：
#   /dev/input/event0 = "ACCDET"（MTK 配件检测），KEY 0072/0073/00a4/0246
#   声卡存在 VOIP_Call_BT_Capture / VOIP_Call_BT_Playback / Voice_MD1_BT_Playback 等 BT SCO DAI
# 本轮补：input devices 全表、audio_policy.conf 里的 bt_sco 通路、accdet sysfs、
#         以及 h2w state 的语义（0=无 1=带麦耳机 2=耳机）
#
# 通过 tools/dev/root_run.sh 以 root 运行；禁用管道，只用重定向。$O 由 root_run.sh 注入。

exec >"$O" 2>&1
chmod 666 "$O" 2>/dev/null

echo "===== P1f 音频通路探测  $(date) ====="

echo
echo "----- 1. /proc/bus/input/devices -----"
cat /proc/bus/input/devices

echo
echo "----- 2. /vendor/etc/audio_policy.conf 全文 -----"
cat /vendor/etc/audio_policy.conf

echo
echo "----- 3. accdet / headset sysfs -----"
ls -l /sys/devices/platform/ACCDET 2>/dev/null
for f in /sys/devices/platform/ACCDET/driver/wired_status \
         /sys/devices/platform/ACCDET/wired_status \
         /sys/devices/platform/mt-pmic-accdet/wired_status; do
  if [ -e "$f" ]; then
    echo "[$f]"
    cat "$f"
  fi
done
ls /sys/class/switch/h2w 2>/dev/null
cat /sys/class/switch/h2w/state 2>/dev/null

echo
echo "----- 4. audio HAL 相关 prop / mixer 控件名（含 bt/sco 关键字的先不筛，全量列控件名） -----"
tinymix 2>/dev/null
echo "-- tinymix exit=$? --"

echo
echo "----- 5. 当前 audio HAL 的 so 与配置 -----"
ls -l /vendor/lib/hw/audio.primary.mt6735m.so /system/lib/hw/audio.a2dp.default.so 2>/dev/null
getprop ro.hardware
getprop ro.board.platform

echo
echo "----- 6. BT 侧：SCO/PCM 相关 prop -----"
getprop persist.bt.sco.codec
getprop ro.bt.sco.codec
getprop persist.service.bdroid.sco
getprop bluetooth.hfp.client.enabled
getprop persist.bluetooth.disableinbandringing

echo
echo "----- 7. 当前进程与已装 .so 校验（确认 P1c 仍在机） -----"
md5sum /system/lib/hw/bluetooth.default.so
cat /data/local/tmp/bt_want.txt 2>/dev/null
echo "-- bt service --"
getprop init.svc.bluetooth
getprop ro.boot.hardware
echo "===== 探测结束 ====="
chmod 666 "$O" 2>/dev/null
