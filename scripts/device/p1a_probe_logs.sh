#!/system/bin/sh
# p1a_probe_logs.sh — 定位"为什么看不到 u2_sink_policy 日志"
#   1) 确认 BT 进程映射的到底是哪一个 bluetooth.default.so（有无 "(deleted)"）
#   2) 确认 BT 进程的日志是否进 logcat（用通用 bt/bluetooth 关键字验证通道本身）
#   3) 再针对性找 u2_sink

PID=`pidof com.android.bluetooth`
echo "=== p1a probe logs ===" >> $O
echo "pid=$PID" >> $O

echo "--- 1. mapped .so ---" >> $O
cat /proc/$PID/maps > /data/local/tmp/.p1_maps 2>&1
grep -e bluetooth /data/local/tmp/.p1_maps >> $O 2>&1

echo "--- 2. logcat tail 60（看通道是否通）---" >> $O
logcat -d -t 60 > /data/local/tmp/.p1_lc 2>&1
cat /data/local/tmp/.p1_lc >> $O 2>&1

echo "--- 3. main log 行数 + bt 关键字 ---" >> $O
logcat -d > /data/local/tmp/.p1_full 2>&1
wc -l /data/local/tmp/.p1_full >> $O 2>&1
grep -m 40 -i -e "u2_sink" /data/local/tmp/.p1_full >> $O 2>&1
echo "--- 3b. 通用 bt 关键字（验证 BT 进程日志能否进 logcat）---" >> $O
grep -m 40 -e "bt_btif" -e "bt_stack" -e "bt_bta" -e "BluetoothAdapter" -e "bt_vendor" /data/local/tmp/.p1_full >> $O 2>&1

echo "--- 4. 是否有 log.tag 过滤属性 ---" >> $O
getprop log.tag >> $O 2>&1
getprop log.tag.global >> $O 2>&1
getprop persist.log.tag >> $O 2>&1

echo "--- 5. BT 进程启动时间 / uptime ---" >> $O
cat /proc/uptime >> $O 2>&1
ls -ld /proc/$PID >> $O 2>&1

echo "=== end ===" >> $O
chmod 666 $O
