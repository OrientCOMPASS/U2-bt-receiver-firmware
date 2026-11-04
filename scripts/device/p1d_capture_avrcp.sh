#!/system/bin/sh
# p1d_capture_avrcp.sh — P1c(48k) 验收 + P1d(AVRCP 绝对音量根因) 一次性取证
#
# 触发时机（用户操作）：
#   1) Android 与 Windows **都**"忘记设备 → 重新配对"（改了 caps，偏好有缓存，见 docs/04 F2）
#   2) 连上 A2DP、放一段音乐（听到声音）
#   3) 在 **Windows** 上把音量滑块拖几下（这是 P1d 的关键刺激）
#   然后跑本脚本，把 $O 与 /data/local/tmp/btsnoop_hci.log 取回沙箱解析
#
# 两个要回答的问题：
#   Q1 (P1c) 协商到的采样率是不是 48000？—— 看 audio_flinger 的 Output thread sampleRate
#            + logcat 里 btif_media 的 track 创建/ sample_rate + snoop 的 AVDTP SET_CONFIG
#   Q2 (P1d) Windows 的绝对音量命令死在哪一层？三选一（互斥）：
#        (a) "Neither CTRL, nor TG is up, drop meta commands"  → bt_rc_callbacks 与
#            bt_rc_ctrl_callbacks 都为 NULL，btif_rc.c:1113 直接丢弃（当前判断）
#        (b) "btif_rc_upstreams_evt pdu: REGISTER_NOTIFICATION" → Java AvrcpTarget 在，
#            但它不实现 VOLUME_CHANGED 的 TG 语义
#        (c) "Abs Volume Cmd Recvd" / "handle_avk_rc_metamsg_cmd" → 走 AVK 路径，
#            说明 bt_rc_ctrl_callbacks 非 NULL（则需重新评估）
#        (d) 一条都没有 → 对端根本没发（GetCapabilities 阶段就放弃了），需回看 snoop
#
# 规矩：脚本内禁用管道；grep 用多个 -e；大输出用 -m / head 限流；尽早 chmod 666。

S=/data/misc/bluedroid/btsnoop_hci.log
D=/data/local/tmp/btsnoop_hci.log

exec >>"$O" 2>&1
chmod 666 "$O" 2>/dev/null
echo "=== p1d capture  $(date) ==="

echo "--- 1. 放开日志阈值（本机 log.tag=E）---"
setprop log.tag.u2_sink_policy V
setprop log.tag.bt_btif V
setprop log.tag.bt_bta V
setprop log.tag.bt_bta_av V
setprop log.tag.bt_appl V
setprop log.tag.bt_stack V
setprop log.tag.btif_av V
setprop log.tag.btif_media_task V
getprop log.tag.bt_btif

echo "--- 2. 在机 .so 校验（应为 P1c: bd7bcef2c0239b75dd917f78226baff0）---"
md5sum /system/lib/hw/bluetooth.default.so
cat /data/local/tmp/bt_want.txt
echo
getprop init.svc.bluetooth
settings get global bluetooth_on

echo "--- 3. BT 状态 + profile 列表（看 AvrcpTarget/AvrcpController 在不在）---"
dumpsys bluetooth_manager > /data/local/tmp/.p1d_dump 2>&1
grep -m 20 -e "state:" -e "enabled:" -e "address:" -e "name:" /data/local/tmp/.p1d_dump
echo "-- profiles --"
grep -m 40 -i -e "profile" -e "avrcp" -e "a2dp" /data/local/tmp/.p1d_dump

echo "--- 4. 已连接设备 ---"
grep -m 40 -e "Bond" -e "bond" -e "Connected" -e "connected" /data/local/tmp/.p1d_dump

echo "--- 5. Q1 证据：AudioFlinger 输出线程采样率（48000 = P1c 成功）---"
dumpsys media.audio_flinger > /data/local/tmp/.p1d_af 2>&1
grep -m 60 -e "Output thread" -e "sampleRate" -e "sample rate" -e "format" -e "AudioTrack" -e "Client" /data/local/tmp/.p1d_af

echo "--- 6. Q1 证据：我们的 native 解码/AudioTrack 日志 ---"
logcat -d > /data/local/tmp/.p1d_all 2>&1
grep -m 60 -e "audio_config_cb" -e "sample_rate" -e "samp_freq" -e "Track creation" -e "BtifAvrcpAudioTrack" -e "btif_a2dp" /data/local/tmp/.p1d_all

echo "--- 7. Q1 证据：AVDTP/AV 连接与 start ---"
grep -m 80 -e "bta_av" -e "AVDT" -e "avdt" -e "SET_CONFIG" -e "setconfig" -e "bta_av_co" /data/local/tmp/.p1d_all

echo "--- 8. Q2 证据：AVRCP 命令落点（四种情形见脚本头注释）---"
grep -m 120 -e "Neither CTRL" -e "drop meta commands" -e "drop passthrough" -e "handle_avk_rc_metamsg_cmd" -e "handle_rc_metamsg_cmd" -e "btif_rc_upstreams_evt" -e "u2_sink_rc_metamsg_cmd" -e "SetAbsoluteVolume" -e "Abs Volume" -e "Register notification" -e "register_notification" -e "RegisterNotification" -e "VOLUME_CHANGED" -e "interim" -e "BTA_AV_META_MSG_EVT" -e "AVRCP" -e "avrcp" -e "btif_rc" -e "u2_sink_policy" -e "set gain" -e "SetAudioTrackGain" -e "bta_av_rc_opened" -e "rc_features" /data/local/tmp/.p1d_all

echo "--- 9. Java AVRCP 服务是否存在（决定 bt_rc_callbacks 是否为 NULL）---"
pm list packages -f > /data/local/tmp/.p1d_pkgs 2>&1
grep -m 10 -i -e "Bluetooth" /data/local/tmp/.p1d_pkgs
echo "-- 运行中的蓝牙相关进程 --"
ps -A > /data/local/tmp/.p1d_ps 2>&1
grep -m 10 -i -e "bluetooth" /data/local/tmp/.p1d_ps

echo "--- 10. u2 策略日志 ---"
logcat -d -s u2_sink_policy:V > /data/local/tmp/.p1d_u2 2>&1
head -n 80 /data/local/tmp/.p1d_u2

echo "--- 11. tombstones / crash ---"
ls -l /data/tombstones
logcat -d -b crash > /data/local/tmp/.p1d_crash 2>&1
tail -n 20 /data/local/tmp/.p1d_crash

echo "--- 12. snoop 拷出（本地用 tools/dev/parse_snoop.py 看 AVDTP SET_CONFIG / AVCTP）---"
ls -l $S
cp $S $D
echo "cp_rc=$?"
chmod 666 $D
ls -l $D
md5sum $D

echo "--- 13. bt_config.conf（重配对后应出现新的远端条目与 A2DP/AVRCP 位）---"
cp /data/misc/bluedroid/bt_config.conf /data/local/tmp/.p1d_btcfg
chmod 666 /data/local/tmp/.p1d_btcfg
head -n 50 /data/local/tmp/.p1d_btcfg

echo "=== end ==="
chmod 666 "$O" 2>/dev/null
