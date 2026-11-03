#!/system/bin/sh
# p0_probe.sh — P0 设备事实采集 + 原厂件备份 + root 通道自检
# 规矩：不用管道；输出全部追加到 $O；最后 chmod 666
# 注意：$O 由 tools/dev/root_run.sh 注入（每次运行唯一文件名），脚本内不要再赋值

echo "=== P0 PROBE (root executor) ===" > $O
echo "--- id ---" >> $O
id >> $O 2>&1
echo "--- uname ---" >> $O
uname -a >> $O 2>&1
echo "--- fingerprint ---" >> $O
getprop ro.build.fingerprint >> $O 2>&1
getprop ro.build.display.id >> $O 2>&1
getprop ro.bootimage.build.fingerprint >> $O 2>&1

echo "--- repoexec service definition (root 可读 /init.rc) ---" >> $O
grep -n -B2 -A14 repoexec /init.rc >> $O 2>&1
grep -n -B2 -A14 repoexec /multi_init.rc >> $O 2>&1
grep -rn repo /property_contexts >> $O 2>&1
ls -la /root >> $O 2>&1
ls -la /sbin >> $O 2>&1

echo "--- mounts ---" >> $O
mount >> $O 2>&1
echo "--- verity ---" >> $O
getprop partition.system.verified >> $O 2>&1
getprop ro.boot.veritymode >> $O 2>&1
getprop ro.boot.flash.locked >> $O 2>&1

echo "--- stock bt md5 ---" >> $O
ls -la /system/lib/hw/bluetooth.default.so >> $O 2>&1
md5sum /system/lib/hw/bluetooth.default.so >> $O 2>&1
ls -la /system/app/Bluetooth/ >> $O 2>&1
md5sum /system/app/Bluetooth/Bluetooth.apk >> $O 2>&1

echo "--- backup stock to /data/local/tmp ---" >> $O
cp /system/lib/hw/bluetooth.default.so /data/local/tmp/bt_stock_backup.so >> $O 2>&1
chmod 644 /data/local/tmp/bt_stock_backup.so >> $O 2>&1
md5sum /data/local/tmp/bt_stock_backup.so >> $O 2>&1
cp /system/app/Bluetooth/Bluetooth.apk /data/local/tmp/Bluetooth_stock.apk >> $O 2>&1
chmod 644 /data/local/tmp/Bluetooth_stock.apk >> $O 2>&1
md5sum /data/local/tmp/Bluetooth_stock.apk >> $O 2>&1

echo "--- /system rw remount test ---" >> $O
mount -o rw,remount /system >> $O 2>&1
echo "remount_rc=$?" >> $O
touch /system/lib/hw/.wtest >> $O 2>&1
echo "touch_rc=$?" >> $O
ls -la /system/lib/hw/.wtest >> $O 2>&1
rm /system/lib/hw/.wtest >> $O 2>&1
echo "rm_rc=$?" >> $O
mount -o ro,remount /system >> $O 2>&1
echo "remount_ro_rc=$?" >> $O

echo "--- bt config ---" >> $O
ls -la /system/etc/bluetooth >> $O 2>&1
cat /system/etc/bluetooth/bt_stack.conf >> $O 2>&1
cat /system/etc/bluetooth/bt_did.conf >> $O 2>&1
cat /vendor/etc/bluetooth/bt_did.conf >> $O 2>&1

echo "--- bt data dirs ---" >> $O
ls -la /data/misc/bluetooth >> $O 2>&1
ls -la /data/misc/bluedroid >> $O 2>&1
ls -la /data/misc/bluetooth/logs >> $O 2>&1

echo "--- vendor bt libs ---" >> $O
ls -la /vendor/lib/libbt-vendor.so >> $O 2>&1
ls -la /vendor/lib/libbluetooth_mtk.so >> $O 2>&1

echo "--- cleanup probe leftovers ---" >> $O
rm /data/local/tmp/marker_rootexec.sh.txt >> $O 2>&1
rm /data/local/tmp/rootexec.sh.bak >> $O 2>&1
ls -la /data/local/tmp >> $O 2>&1

echo "=== END ===" >> $O
chmod 666 $O
