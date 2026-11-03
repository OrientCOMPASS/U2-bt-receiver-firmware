#!/system/bin/sh
# p1a_log_capture.sh — 打开日志可见性后重启 BT，抓取 u2_sink_policy 的实际行为
#
# 背景（本机实测，重要）：`getprop log.tag` = **E** → 全机日志最低优先级被压到 ERROR，
# 所以我们的 LOG_INFO/LOG_WARN 一律被 liblog 过滤掉（只有 `E bt_btif` 那类能看到）。
# 对策：给我们的 tag 单独设阈值 `setprop log.tag.<TAG> V`（只影响本 tag，不动全机设置）。
#
# 步骤：设 prop → logcat -c → kill BT 进程（zygote 自动重启）→ 抓状态/maps/日志/tombstone

echo "=== p1a log capture ===" >> $O

echo "--- 0. 现状 ---" >> $O
getprop log.tag >> $O 2>&1
echo "uptime:" >> $O
cat /proc/uptime >> $O 2>&1

echo "--- 1. 设 per-tag 日志阈值 ---" >> $O
setprop log.tag.u2_sink_policy V
setprop log.tag.bt_btif V
setprop log.tag.bt_bta V
setprop log.tag.bt_bta_av V
setprop log.tag.bt_appl V
setprop log.tag.bt_stack V
setprop log.tag.bt_avdt V
getprop log.tag.u2_sink_policy >> $O 2>&1

echo "--- 2. 清 log ---" >> $O
logcat -c >> $O 2>&1

echo "--- 3. 重启 BT 进程 ---" >> $O
PID=`pidof com.android.bluetooth`
echo "old_pid=$PID" >> $O
kill $PID >> $O 2>&1
echo "kill_rc=$?" >> $O
sleep 20

echo "--- 4. 状态 ---" >> $O
settings get global bluetooth_on >> $O 2>&1
dumpsys bluetooth_manager > /data/local/tmp/.p1c_dump 2>&1
grep -m 10 -e "state:" -e "enabled:" -e "address:" -e "name:" -e "time since" /data/local/tmp/.p1c_dump >> $O 2>&1

echo "--- 5. 新 pid 与映射的 .so（必须无 '(deleted)'）---" >> $O
NPID=`pidof com.android.bluetooth`
echo "new_pid=$NPID" >> $O
cat /proc/$NPID/maps > /data/local/tmp/.p1c_maps 2>&1
grep -e "bluetooth.default" /data/local/tmp/.p1c_maps >> $O 2>&1
md5sum /system/lib/hw/bluetooth.default.so >> $O 2>&1

echo "--- 6. u2_sink_policy 日志 ---" >> $O
logcat -d -s u2_sink_policy:V > /data/local/tmp/.p1c_u2 2>&1
head -n 80 /data/local/tmp/.p1c_u2 >> $O 2>&1

echo "--- 7. av/a2dp 原生线索 ---" >> $O
logcat -d > /data/local/tmp/.p1c_all 2>&1
grep -m 80 -e "u2_sink" -e "btif_av" -e "bta_av" -e "AVDT" -e "a2dp" -e "A2DP" /data/local/tmp/.p1c_all >> $O 2>&1

echo "--- 8. tombstones ---" >> $O
ls -l /data/tombstones >> $O 2>&1

echo "=== end ===" >> $O
chmod 666 $O
