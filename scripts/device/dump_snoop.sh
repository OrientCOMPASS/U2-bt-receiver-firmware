#!/system/bin/sh
# dump_snoop.sh — 把 btsnoop 日志拷到 shell 可读的位置（/data/local/tmp）并放开权限
#
# 为什么需要：/data/misc/bluedroid 是 bluetooth:net_bt_stack 750，adb shell（uid 2000）
# 读不到；本机又没有 su，只能走 root executor。
# 之后在 Windows 侧 `adb pull /data/local/tmp/btsnoop_hci.log`，再传回沙箱用
# tools/dev/parse_snoop.py 解析。
# 规矩：不用管道。

S=/data/misc/bluedroid/btsnoop_hci.log
D=/data/local/tmp/btsnoop_hci.log

echo "=== dump snoop ===" >> $O

echo "--- 1. 目录与文件 ---" >> $O
ls -l /data/misc/bluedroid >> $O 2>&1
ls -l $S >> $O 2>&1

echo "--- 2. 拷贝到 /data/local/tmp ---" >> $O
cp $S $D >> $O 2>&1
echo "cp_rc=$?" >> $O
chmod 666 $D >> $O 2>&1
ls -l $D >> $O 2>&1
md5sum $D >> $O 2>&1

echo "--- 3. btsnoop 相关日志（看有没有 open 失败）---" >> $O
setprop log.tag.bt_stack V
logcat -d > /data/local/tmp/.sn_lc 2>&1
grep -m 40 -e "btsnoop" -e "unable to open" -e "Snoop" /data/local/tmp/.sn_lc >> $O 2>&1

echo "--- 4. BT 状态与 tombstone ---" >> $O
settings get global bluetooth_on >> $O 2>&1
ls -l /data/tombstones >> $O 2>&1

echo "=== end ===" >> $O
chmod 666 $O
