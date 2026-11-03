#!/system/bin/sh
# p0_probe2.sh — 采集 profile 集合 / COD / 扫描可见性 / 日志与 snoop 配置
DM=/data/local/tmp/.btdump2.txt
LC=/data/local/tmp/.lc2.txt

echo "=== P0 probe2: profiles / COD / logging ===" > $O

echo "--- bluetooth_manager: Profile 列表 ---" >> $O
dumpsys bluetooth_manager > $DM 2>&1
grep -n "^Profile:\|^  enabled:\|mEnabledProfiles\|Profile:" $DM >> $O 2>&1

echo "--- adapter 基本信息 ---" >> $O
head -n 12 $DM >> $O 2>&1

echo "--- scan mode / discoverable ---" >> $O
grep -n -i "scanmode\|scan_mode\|discoverable\|mDiscoverable\|class of device\|cod" $DM >> $O 2>&1
settings get global bluetooth_on >> $O 2>&1
getprop persist.bluetooth.disableabsvolvalue >> $O 2>&1

echo "--- bt_stack.conf (完整) ---" >> $O
cat /system/etc/bluetooth/bt_stack.conf >> $O 2>&1
echo "--- bt_stack.conf.debug ---" >> $O
cat /system/etc/bluetooth/bt_stack.conf.debug >> $O 2>&1

echo "--- logcat buffer 情况 ---" >> $O
logcat -g >> $O 2>&1
getprop ro.logd.size >> $O 2>&1
getprop persist.log.tag >> $O 2>&1
getprop log.tag >> $O 2>&1

echo "--- 当前 logcat（main+system+crash）---" >> $O
logcat -d -b main -b system -b crash -v brief > $LC 2>&1
wc -l $LC >> $O 2>&1
grep -i -m 60 -n "bt_\|blue\|FluorideClover\|BTIF\|vendor" $LC >> $O 2>&1

echo "--- 进程 ---" >> $O
ps > /data/local/tmp/.ps2 2>&1
grep -i blue /data/local/tmp/.ps2 >> $O 2>&1

echo "--- APK 里的 profile 开关（config 资源）---" >> $O
ls -la /system/app/Bluetooth/ >> $O 2>&1
ls -la /system/app/Bluetooth/oat/arm/ >> $O 2>&1
ls -la /system/etc/permissions/ >> $O 2>&1
grep -l -i bluetooth /system/etc/permissions/*.xml >> $O 2>&1

chmod 666 $DM
chmod 666 $LC
chmod 666 $O
echo "=== end ===" >> $O
