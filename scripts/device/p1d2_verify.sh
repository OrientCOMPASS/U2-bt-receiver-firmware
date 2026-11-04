#!/system/bin/sh
# p1d2_verify.sh — P1d2（AVRCP 绝对音量：第一支截获）验收取证
#
# 触发时机：Windows 侧跑完 scripts/windows/p1d2_vol_test.ps1（播放 + 音量键）之后。
#
# 要回答的三个问题（都在本机日志/snoop 里，不靠人耳）：
#   Q1 我们是否**就地应答**了 SetAbsoluteVolume？
#      期望：`u2_sink_rc_take_metamsg_cmd: SetAbsoluteVolume abs_vol=NN/127`
#           + `handle_rc_metamsg_cmd: pdu=0x50 consumed by U2 sink AVRCP TG`
#      反例（P1d 的行为）：`btif_rc_upstreams_evt` → `Sending error notification`（REJECTED）
#   Q2 是否回了 RegisterNotification(VOLUME_CHANGED) 的 INTERIM？
#      期望：`VOLUME_CHANGED registered, interim abs_vol=NN/127`
#   Q3 增益是否真的落到 AudioTrack？
#      期望：`u2_sink_policy: SetAbsoluteVolume: abs_vol=NN/127 (第 N 次)`
#           → `rc gain: abs_vol=NN/127 → AudioTrack gain=0.xxx`
#           → `btif_a2dp_set_audio_track_gain ... (posted to media task)`
#           → media task 侧 `BtifAvrcpSetAudioTrackGain ... set gain 0.xxx`
#      若只有前三条、没有最后一条 → audio_track 当时为 NULL（未开流），
#      应由 `rc gain: ... (track_created)` 在开流后重放。
#
# 日志 tag 说明（BTIF_TRACE_* 用文件顶部的 LOG_TAG）：
#   btif_rc.c            → bt_btif_avrc
#   btif_media_task.c    → bt_appl（APPL_TRACE_*）
#   btif_avrcp_audio_track.cpp → btif_avrcp_audio_track
#   u2_sink_policy.c     → u2_sink_policy（LOG_INFO/LOG_WARN）
# 本机 log.tag 默认 E，必须先 setprop 放开，否则一条都看不到。
#
# 规矩：脚本内禁用管道；grep 用多个 -e；尽早 chmod 666；大输出用 -m 限流。

S=/data/misc/bluedroid/btsnoop_hci.log
D=/data/local/tmp/btsnoop_hci.log

exec >>"$O" 2>&1
chmod 666 "$O" 2>/dev/null
echo "=== p1d2 verify  $(date) ==="

echo "--- 1. 放开日志阈值 ---"
setprop log.tag.u2_sink_policy V
setprop log.tag.bt_btif_avrc V
setprop log.tag.bt_btif V
setprop log.tag.bt_appl V
setprop log.tag.bt_stack V
setprop log.tag.bt_bta_av V
setprop log.tag.btif_avrcp_audio_track V
setprop log.tag.btif_media_task V
getprop log.tag.bt_btif_avrc

echo "--- 2. 在机 .so 校验（P1d2 期望 ced7ee9dd15428c9c3ce4c8f6dba030b）---"
md5sum /system/lib/hw/bluetooth.default.so
cat /data/local/tmp/bt_want.txt
echo
getprop init.svc.bluetooth
settings get global bluetooth_on
pidof com.android.bluetooth

echo "--- 3. A2DP 连接 / 开流 / AudioTrack 创建 ---"
logcat -d > /data/local/tmp/.p1d2_all 2>&1
grep -m 80 -e "connection_state=" -e "audio_state=" -e "audio_config" -e "Create Track" -e "sample_rate" -e "samp_freq" -e "audio focus" /data/local/tmp/.p1d2_all

echo "--- 4. ★Q1/Q2 绝对音量截获证据 ---"
grep -m 200 -e "u2_sink_rc_take_metamsg_cmd" -e "consumed by U2 sink AVRCP TG" -e "SetAbsoluteVolume" -e "RegisterNotification" -e "VOLUME_CHANGED registered" -e "no Java RC callbacks" /data/local/tmp/.p1d2_all

echo "--- 5. ★Q3 增益落地链路（策略层 → media task → AudioTrack）---"
grep -m 200 -e "rc gain" -e "set gain to" -e "SetAudioTrackGain" -e "posted to media task" -e "on_track_created" -e "track_created" /data/local/tmp/.p1d2_all

echo "--- 6. 反面证据：TG 侧不应再 reject 音量命令 ---"
grep -m 80 -e "Sending error notification" -e "btif_rc_upstreams_evt" -e "Neither CTRL" -e "drop meta commands" /data/local/tmp/.p1d2_all

echo "--- 7. 策略层完整日志（含 set/replay/clamp 计数与 canary）---"
logcat -d -s u2_sink_policy:V > /data/local/tmp/.p1d2_u2 2>&1
tail -n 80 /data/local/tmp/.p1d2_u2

echo "--- 8. canary / 越界写自检（有则说明踩了内存）---"
grep -m 20 -e "canary" -e "越界" /data/local/tmp/.p1d2_all

echo "--- 9. tombstones / crash buffer ---"
ls -l /data/tombstones
logcat -d -b crash > /data/local/tmp/.p1d2_crash 2>&1
tail -n 25 /data/local/tmp/.p1d2_crash

echo "--- 10. snoop 拷出（回沙箱用 tools/dev/parse_snoop.py --avrcp --vol-only 解析）---"
ls -l $S
cp $S $D
echo "cp_rc=$?"
chmod 666 $D
ls -l $D
md5sum $D

echo "=== end ==="
chmod 666 "$O" 2>/dev/null
