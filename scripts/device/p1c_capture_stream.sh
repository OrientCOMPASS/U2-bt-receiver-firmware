#!/system/bin/sh
# p1c_capture_stream.sh — 真机出声测试后的取证：A2DP 连接状态 + 协商采样率 + AVDTP/SDP 轨迹
#
# 用法：用户用手机完成"扫描 → 配对 → 勾选媒体音频 → 播放/暂停/调音量"之后跑本脚本，
#       然后在 Windows 侧 `adb pull /data/local/tmp/btsnoop_hci.log` 传回沙箱，
#       用 tools/dev/parse_snoop.py 解析 AVDTP SET_CONFIG。
# 规矩：不用管道；grep 用多个 -e；大输出用 -m / head 限流。

S=/data/misc/bluedroid/btsnoop_hci.log
D=/data/local/tmp/btsnoop_hci.log

echo "=== p1c capture (after phone streaming test) ===" >> $O

echo "--- 1. 日志阈值（本机 log.tag=E，必须先放开我们的 tag）---" >> $O
setprop log.tag.u2_sink_policy V
setprop log.tag.bt_btif V
setprop log.tag.bt_bta V
setprop log.tag.bt_bta_av V
setprop log.tag.bt_appl V
setprop log.tag.bt_avdt V
setprop log.tag.bt_stack V
getprop log.tag.u2_sink_policy >> $O 2>&1

echo "--- 2. BT 状态 ---" >> $O
settings get global bluetooth_on >> $O 2>&1
dumpsys bluetooth_manager > /data/local/tmp/.p1c_dump 2>&1
grep -m 30 -e "state:" -e "enabled:" -e "address:" -e "name:" -e "time since" -e "ScanMode" -e "scan mode" /data/local/tmp/.p1c_dump >> $O 2>&1

echo "--- 3. 已配对/已连接设备 ---" >> $O
grep -m 40 -e "Bond" -e "bond" -e "Connected" -e "connected" -e "Device" /data/local/tmp/.p1c_dump >> $O 2>&1

echo "--- 4. 我们的策略日志（连接/音频/配置回调）---" >> $O
logcat -d -s u2_sink_policy:V > /data/local/tmp/.p1c_u2 2>&1
head -n 120 /data/local/tmp/.p1c_u2 >> $O 2>&1

echo "--- 5. AV / AVDT / media 相关（协商与解码线索）---" >> $O
logcat -d > /data/local/tmp/.p1c_all 2>&1
grep -m 150 -e "bta_av" -e "AVDT" -e "avdt" -e "btif_media" -e "A2dpSink" -e "A2DP" -e "AudioTrack" -e "audio_track" -e "Track creation" -e "samp_freq" -e "sample_rate" /data/local/tmp/.p1c_all >> $O 2>&1

echo "--- 6. 音频输出侧（AudioFlinger 是否真的建了 track）---" >> $O
dumpsys media.audio_flinger > /data/local/tmp/.p1c_af 2>&1
grep -m 40 -e "Output thread" -e "sample rate" -e "Sample rate" -e "format" -e "AudioTrack" -e "standby" /data/local/tmp/.p1c_af >> $O 2>&1

echo "--- 7. tombstones / crash ---" >> $O
ls -l /data/tombstones >> $O 2>&1
logcat -d -b crash > /data/local/tmp/.p1c_crash 2>&1
tail -n 30 /data/local/tmp/.p1c_crash >> $O 2>&1

echo "--- 8. snoop 拷出（供本地解析 AVDTP SET_CONFIG）---" >> $O
ls -l $S >> $O 2>&1
cp $S $D >> $O 2>&1
echo "cp_rc=$?" >> $O
chmod 666 $D >> $O 2>&1
ls -l $D >> $O 2>&1
md5sum $D >> $O 2>&1

echo "--- 9. bt_config.conf（配对后应出现远端设备条目）---" >> $O
cp /data/misc/bluedroid/bt_config.conf /data/local/tmp/.p1c_btcfg >> $O 2>&1
chmod 666 /data/local/tmp/.p1c_btcfg >> $O 2>&1
head -n 60 /data/local/tmp/.p1c_btcfg >> $O 2>&1

echo "=== end ===" >> $O
chmod 666 $O
