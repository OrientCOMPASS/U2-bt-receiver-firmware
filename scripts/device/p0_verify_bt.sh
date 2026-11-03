#!/system/bin/sh
# p0_verify_bt.sh — 刷入后验证：蓝牙能否起来、我们的 .so 是否被正确加载
# 只用重定向，不用管道；大输出先落文件再 grep。

LC=/data/local/tmp/.lc.txt
DM=/data/local/tmp/.btdump.txt
PS=/data/local/tmp/.ps.txt

echo "=== P0 verify BT (source-equivalent build) ===" > $O

echo "--- installed lib ---" >> $O
md5sum /system/lib/hw/bluetooth.default.so >> $O 2>&1
ls -laZ /system/lib/hw/bluetooth.default.so >> $O 2>&1

echo "--- clear log, enable BT ---" >> $O
logcat -c >> $O 2>&1
settings get global bluetooth_on >> $O 2>&1
service call bluetooth_manager 6 >> $O 2>&1
echo "svc_call_rc=$?" >> $O
sleep 8

echo "--- adapter state ---" >> $O
settings get global bluetooth_on >> $O 2>&1
dumpsys bluetooth_manager > $DM 2>&1
wc -c $DM >> $O 2>&1
head -n 45 $DM >> $O 2>&1

echo "--- process ---" >> $O
ps > $PS 2>&1
grep -i blue $PS >> $O 2>&1

echo "--- tombstones ---" >> $O
ls -la /data/tombstones >> $O 2>&1

echo "--- logcat: stack / vendor / errors ---" >> $O
logcat -d > $LC 2>&1
wc -l $LC >> $O 2>&1
grep -i -m 40 -n "FATAL\|tombstone\|SIGSEGV\|SIGBUS\|abort\|cannot load\|dlopen\|CANNOT LINK" $LC >> $O 2>&1
echo "--- bt tags ---" >> $O
grep -i -m 60 -n "bt_vendor\|libbt-vendor\|bt_hw\|bt_bte\|bt_btif\|bt_stack\|bt_main\|BTIF\|bluetoothd\|AdapterService\|vendor_open\|MTKBT\|bt_mtk" $LC >> $O 2>&1
echo "--- first 60 bt-ish lines ---" >> $O
grep -i -m 80 -n "blue\|bt_" $LC >> $O 2>&1

chmod 666 $LC
chmod 666 $DM
chmod 666 $O
echo "=== end ===" >> $O
