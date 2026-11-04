#!/system/bin/sh
# p1d2_logprops.sh — 只放开日志阈值（必须在**刺激之前**跑，否则 BTIF_TRACE_EVENT /
# APPL_TRACE_DEBUG 会被 log.tag 门掉，事后 logcat 里什么都看不到）。
exec >>"$O" 2>&1
chmod 666 "$O" 2>/dev/null
echo "=== logprops $(date) ==="
setprop log.tag.u2_sink_policy V
setprop log.tag.bt_btif_avrc V
setprop log.tag.bt_btif V
setprop log.tag.bt_appl V
setprop log.tag.bt_stack V
setprop log.tag.bt_bta_av V
setprop log.tag.btif_avrcp_audio_track V
setprop log.tag.btif_media_task V
echo "u2_sink_policy=`getprop log.tag.u2_sink_policy`"
echo "bt_btif_avrc=`getprop log.tag.bt_btif_avrc`"
echo "bt_appl=`getprop log.tag.bt_appl`"
echo "btif_avrcp_audio_track=`getprop log.tag.btif_avrcp_audio_track`"
logcat -c
echo "logcat cleared"
echo "=== end ==="
chmod 666 "$O" 2>/dev/null
