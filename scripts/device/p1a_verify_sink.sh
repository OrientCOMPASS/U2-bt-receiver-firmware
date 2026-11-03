#!/system/bin/sh
# p1a_verify_sink.sh — P1a 验证：USE_AUDIO_TRACK + native 主动启用 A2DP SINK
#
# 检查项：
#   1. 在机 .so md5 == 本轮构建
#   2. BT 是否 STATE_ON、进程是否存活
#   3. /data/tombstones 是否零新增
#   4. 我们自己的策略层日志（tag=u2_sink_policy，走 osi/liblog，不受 bt_stack.conf TRC 影响）
#   5. crash buffer
#   6. AV/sink 相关的原生日志线索
# 规矩：不用管道；grep 用多个 -e；大输出用 -m 限流。

echo "=== P1a verify: A2DP SINK enable ===" >> $O

echo "--- 1. installed .so ---" >> $O
md5sum /system/lib/hw/bluetooth.default.so >> $O 2>&1
ls -laZ /system/lib/hw/bluetooth.default.so >> $O 2>&1

echo "--- 2. bt state ---" >> $O
settings get global bluetooth_on >> $O 2>&1
dumpsys bluetooth_manager > /data/local/tmp/.p1a_dump 2>&1
grep -m 40 -e "enabled:" -e "state:" -e "State:" -e "name:" -e "address:" -e "Profile" /data/local/tmp/.p1a_dump >> $O 2>&1
echo "--- 2b. profile 列表（看 sink 是否出现在 Java 侧；预期仍只有 7 个 source 侧）---" >> $O
grep -m 60 -e "Gatt" -e "Headset" -e "A2dp" -e "Hid" -e "Health" -e "Pan" -e "Map" -e "Sink" -e "Avrcp" /data/local/tmp/.p1a_dump >> $O 2>&1

echo "--- 3. bt process ---" >> $O
ps > /data/local/tmp/.p1a_ps 2>&1
grep -m 10 -i blue /data/local/tmp/.p1a_ps >> $O 2>&1

echo "--- 4. tombstones ---" >> $O
ls -l /data/tombstones >> $O 2>&1

echo "--- 5. u2_sink_policy logs ---" >> $O
logcat -d -s u2_sink_policy:V > /data/local/tmp/.p1a_log 2>&1
head -n 120 /data/local/tmp/.p1a_log >> $O 2>&1

echo "--- 6. crash buffer (tail) ---" >> $O
logcat -d -b crash > /data/local/tmp/.p1a_crash 2>&1
tail -n 40 /data/local/tmp/.p1a_crash >> $O 2>&1

echo "--- 7. av/sink 原生线索（main log）---" >> $O
logcat -d > /data/local/tmp/.p1a_main 2>&1
grep -m 120 -e "u2_sink" -e "btif_av" -e "A2DP SINK" -e "btif_init_ok" -e "AvEnable" -e "AVDT" -e "bta_av" /data/local/tmp/.p1a_main >> $O 2>&1

echo "--- 8. dmesg 尾部（看有无 SIGBUS/segfault）---" >> $O
dmesg > /data/local/tmp/.p1a_dmesg 2>&1
grep -m 30 -e "bluetooth" -e "sigbus" -e "segfault" -e "SIGSEGV" -e "traps" /data/local/tmp/.p1a_dmesg >> $O 2>&1

echo "=== end ===" >> $O
chmod 666 $O
