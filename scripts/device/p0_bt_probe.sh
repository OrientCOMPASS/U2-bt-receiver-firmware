#!/system/bin/sh
# p0_bt_probe.sh — 打开蓝牙并采集 profile / SDP / 扫描可见性事实
echo "=== enable BT ===" > $O
svc bluetooth enable >> $O 2>&1
echo "svc_rc=$?" >> $O
sleep 4
settings get global bluetooth_on >> $O 2>&1
echo "--- ps ---" >> $O
ps > /data/local/tmp/ps_all.txt 2>&1
grep -i blue /data/local/tmp/ps_all.txt >> $O 2>&1
echo "--- dumpsys bluetooth_manager ---" >> $O
dumpsys bluetooth_manager > /data/local/tmp/btdump.txt 2>&1
echo "dump_bytes:" >> $O
wc -c /data/local/tmp/btdump.txt >> $O 2>&1
head -n 90 /data/local/tmp/btdump.txt >> $O 2>&1
echo "--- grep profiles ---" >> $O
grep -i -n "profile\|Sink\|A2dp\|Avrcp\|Headset\|Hfp" /data/local/tmp/btdump.txt >> $O 2>&1
echo "--- adapter props ---" >> $O
settings get global bluetooth_on >> $O 2>&1
getprop persist.bluetooth.disableabsvolvalue >> $O 2>&1
getprop bluetooth.profile >> $O 2>&1
echo "--- snoop cfg ---" >> $O
ls -la /sdcard/mtklog/btlog >> $O 2>&1
echo "--- bond list ---" >> $O
cat /data/misc/bluedroid/bt_config.conf >> $O 2>&1
chmod 666 /data/local/tmp/btdump.txt
chmod 666 $O
