#!/system/bin/sh
# enable_snoop.sh — 打开 HCI snoop 落盘，用于在**不依赖第二台蓝牙设备**的情况下
# 验证我们下发的 HCI 参数（Write Class of Device / Write Scan Enable / AVDTP…）。
#
# 依据（源码）：
#   main/stack_config.c:28-30  配置键 BtSnoopFileName / BtSnoopLogOutput / BtSnoopSaveLog
#   main/stack_config.c:82     默认落盘路径 /data/misc/bluedroid/btsnoop_hci.log
#   hci/src/btsnoop.c:154-181  is_logging = api || stack_config->get_btsnoop_turned_on()
#   配置文件位置：/system/etc/bluetooth/bt_stack.conf（stack_config.c:47）
#
# 本脚本：备份 conf（一次）→ 把 BtSnoopLogOutput 改成 true → 重启 BT → 放开 snoop 文件权限。
# 之后用 tools/dev/parse_snoop.py 解析（本地解析，不在设备上跑）。
# 规矩：不用管道；grep 用多个 -e。

CONF=/system/etc/bluetooth/bt_stack.conf
BAK=/data/local/tmp/bt_stack.conf.stock
SNOOP=/data/misc/bluedroid/btsnoop_hci.log

echo "=== enable btsnoop ===" >> $O

echo "--- 0. conf 现状 ---" >> $O
ls -l $CONF >> $O 2>&1
grep -n -e "BtSnoop" -e "TraceConf" $CONF >> $O 2>&1

echo "--- 1. 备份（仅首次）---" >> $O
if [ ! -f $BAK ]; then
  cp $CONF $BAK >> $O 2>&1
  echo "backup_created=1" >> $O
else
  echo "backup_exists=1" >> $O
fi
md5sum $BAK >> $O 2>&1

echo "--- 2. 改配置 ---" >> $O
mount -o rw,remount /system >> $O 2>&1
echo "remount_rw_rc=$?" >> $O
sed -i "s/^BtSnoopLogOutput=.*/BtSnoopLogOutput=true/" $CONF >> $O 2>&1
echo "sed_on_rc=$?" >> $O
# ★实测坑：本机 conf 里 BtSnoopFileName 指向 /sdcard/mtklog/btlog/btsnoop_hci.log，
#   而 /sdcard/mtklog 目录**根本不存在** → btsnoop.c 的 open() 失败 → is_logging=false，
#   一个字节都不会写（现象：文件不存在，且 logcat 里有 "unable to open"）。
#   改成 AOSP 默认路径 /data/misc/bluedroid/（bluetooth uid 自己的目录，SELinux 放行）。
sed -i "s#^BtSnoopFileName=.*#BtSnoopFileName=/data/misc/bluedroid/btsnoop_hci.log#" $CONF >> $O 2>&1
echo "sed_path_rc=$?" >> $O
grep -n -e "BtSnoop" $CONF >> $O 2>&1
chmod 644 $CONF >> $O 2>&1
chown 0:0 $CONF >> $O 2>&1
restorecon $CONF >> $O 2>&1
ls -lZ $CONF >> $O 2>&1
mount -o ro,remount /system >> $O 2>&1
echo "remount_ro_rc=$?" >> $O

echo "--- 3. 重启 BT 让配置生效 ---" >> $O
PID=`pidof com.android.bluetooth`
echo "old_pid=$PID" >> $O
kill $PID >> $O 2>&1
echo "kill_rc=$?" >> $O
sleep 18
NPID=`pidof com.android.bluetooth`
echo "new_pid=$NPID" >> $O
settings get global bluetooth_on >> $O 2>&1

echo "--- 4. snoop 文件 ---" >> $O
ls -l /data/misc/bluedroid >> $O 2>&1
chmod 666 $SNOOP >> $O 2>&1
ls -l $SNOOP >> $O 2>&1
md5sum $SNOOP >> $O 2>&1

echo "--- 5. 我们的策略日志 ---" >> $O
setprop log.tag.u2_sink_policy V
logcat -d -s u2_sink_policy:V > /data/local/tmp/.sn_u2 2>&1
head -n 40 /data/local/tmp/.sn_u2 >> $O 2>&1

echo "--- 6. tombstones ---" >> $O
ls -l /data/tombstones >> $O 2>&1

echo "=== end ===" >> $O
chmod 666 $O
