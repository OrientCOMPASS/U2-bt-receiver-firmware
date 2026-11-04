#!/system/bin/sh
# p1d2_phone_prep.sh — 手机（nubia/Android）侧绝对音量验证的**现场准备**
#
# 目的：让这次手机测试的证据是**干净可判定**的：
#   1) 放开日志阈值（必须在刺激之前，否则 BTIF_TRACE_*/APPL_TRACE_* 一条都不落）
#   2) 扩大并清空 logcat 环形缓冲（音量测试日志量大，256KB 默认缓冲会被冲掉）
#   3) **轮转 snoop**：旧文件（含 Windows 侧 P1d2 取证）挪到 /data/local/tmp，
#      重启 BT 后生成全新 snoop —— 这样 SET_ABSOLUTE_VOLUME / ACCEPTED / CHANGED
#      的计数只统计本次手机测试，不会和上一轮混在一起
#   4) 记录基线（在机 .so md5、bonded 设备、scan mode），便于事后核对
#
# 规矩（任务书 + 实测）：脚本内**禁用管道**（守护可能挂死）；grep 用多个 -e；
# 大输出先重定向到临时文件再 grep；尽早 chmod 666 $O。
#
# 用法： tools/dev/root_run.sh scripts/device/p1d2_phone_prep.sh 60

S=/data/misc/bluedroid/btsnoop_hci.log
PREV=/data/local/tmp/btsnoop_prev_p1d2.bin
T=/data/local/tmp/.p1d2_prep

exec >>"$O" 2>&1
chmod 666 "$O" 2>/dev/null
echo "=== p1d2 phone prep  $(date) ==="

echo "--- 1. 放开日志阈值（刺激之前）---"
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

echo "--- 2. logcat 缓冲扩容 + 清空 ---"
logcat -G 16M > $T 2>&1
cat $T
logcat -g > $T 2>&1
cat $T
logcat -c > $T 2>&1
echo "logcat_c_rc=$?"

echo "--- 3. 基线：在机 .so / BT 状态 / bonded ---"
md5sum /system/lib/hw/bluetooth.default.so
echo "want=`cat /data/local/tmp/bt_want.txt`"
echo "init.svc.bluetooth=`getprop init.svc.bluetooth`"
echo "bluetooth_on=`settings get global bluetooth_on`"
echo "bt_pid=`pidof com.android.bluetooth`"
dumpsys bluetooth_manager > $T 2>&1
grep -m 30 -e "state:" -e "Bonded" -e "bonded" -e "mDevice" -e "scan mode" -e "ScanMode" -e "name:" $T

echo "--- 4. 轮转 snoop（旧文件挪走，重启 BT 生成新文件）---"
ls -l $S
mv $S $PREV
echo "mv_rc=$?"
chmod 666 $PREV 2>/dev/null
ls -l $PREV

PID=`pidof com.android.bluetooth`
echo "old_pid=$PID"
kill $PID
echo "kill_rc=$?"
sleep 22
NPID=`pidof com.android.bluetooth`
echo "new_pid=$NPID"
echo "bluetooth_on=`settings get global bluetooth_on`"
echo "init.svc.bluetooth=`getprop init.svc.bluetooth`"

echo "--- 5. 新 snoop 是否已生成 + 放开权限 ---"
ls -l /data/misc/bluedroid
chmod 666 $S 2>/dev/null
echo "chmod_rc=$?"
ls -l $S
md5sum $S 2>/dev/null

echo "--- 6. BT 重启后日志阈值仍在（属性是全局的，不随进程丢）---"
echo "u2_sink_policy=`getprop log.tag.u2_sink_policy`"
logcat -d -s u2_sink_policy:V > $T 2>&1
echo "u2 行数（应为启动自检若干行）："
wc -l < $T
tail -n 15 $T

echo "--- 7. tombstones（必须为空/无新增）---"
ls -l /data/tombstones

echo "=== end ==="
chmod 666 "$O" 2>/dev/null
