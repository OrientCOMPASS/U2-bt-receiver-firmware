#!/system/bin/sh
# flash_bt_so.sh — 原子替换 /system/lib/hw/bluetooth.default.so（md5 门控，禁止重复刷）
#
# 配方（照抄任务书，实测有效）：
#   mount -o rw,remount /system
#   cp bt_new.so /system/lib/hw/.bt_swap.tmp ; chmod 644 ; chown 0:0 ; restorecon
#   mv .bt_swap.tmp bluetooth.default.so ; sync          ← 原子安装：直接 cp 覆盖
#                                                          被映射的文件会让运行中进程 SIGBUS
#   mount -o ro,remount /system
#   kill $(pidof com.android.bluetooth)                  ← zygote 自动重启 BT 进程
#   md5sum 复核
#
# 门控：/data/local/tmp/bt_want.txt 里写目标 md5；若与当前已装的一致 → SKIP。
# 首次操作前必须已有 /data/local/tmp/bt_stock_backup.so（原厂备份）。
# 规矩：脚本内不用管道；长命令行拆短。

NEW=/data/local/tmp/bt_new.so
DST=/system/lib/hw/bluetooth.default.so
BAK=/data/local/tmp/bt_stock_backup.so
TMP=/system/lib/hw/.bt_swap.tmp
WANTF=/data/local/tmp/bt_want.txt
CURF=/data/local/tmp/.cur_md5

echo "=== flash bluetooth.default.so ===" > $O

echo "--- 前置检查 ---" >> $O
ls -la $NEW >> $O 2>&1
ls -la $BAK >> $O 2>&1
ls -la $DST >> $O 2>&1

echo "--- 备份校验（必须存在且与原厂一致）---" >> $O
md5sum $BAK > /data/local/tmp/.bak_md5 2>&1
cat /data/local/tmp/.bak_md5 >> $O 2>&1
md5sum $DST > $CURF 2>&1
cat $CURF >> $O 2>&1
md5sum $NEW >> $O 2>&1
cat $WANTF >> $O 2>&1

WANTMD5=`cut -d" " -f1 $WANTF`
CURMD5=`cut -d" " -f1 $CURF`
NEWMd5=`md5sum $NEW > /data/local/tmp/.new_md5 ; cut -d" " -f1 /data/local/tmp/.new_md5`
echo "want=$WANTMD5" >> $O
echo "cur =$CURMD5" >> $O
echo "new =$NEWMd5" >> $O

if [ -z "$WANTMD5" ]; then
  echo "ABORT: bt_want.txt 为空" >> $O
elif [ "$WANTMD5" != "$NEWMd5" ]; then
  echo "ABORT: bt_new.so 与 bt_want.txt 不一致（传输损坏？）" >> $O
elif [ "$WANTMD5" = "$CURMD5" ]; then
  echo "SKIP: 设备上已是目标版本，不重复刷" >> $O
else
  if [ ! -f $BAK ]; then
    echo "ABORT: 缺少原厂备份 $BAK，拒绝刷入" >> $O
  else
    echo "--- 安装 ---" >> $O
    mount -o rw,remount /system >> $O 2>&1
    echo "remount_rw_rc=$?" >> $O
    cp $NEW $TMP >> $O 2>&1
    echo "cp_rc=$?" >> $O
    chmod 644 $TMP >> $O 2>&1
    chown 0:0 $TMP >> $O 2>&1
    restorecon $TMP >> $O 2>&1
    ls -laZ $TMP >> $O 2>&1
    mv $TMP $DST >> $O 2>&1
    echo "mv_rc=$?" >> $O
    sync >> $O 2>&1
    mount -o ro,remount /system >> $O 2>&1
    echo "remount_ro_rc=$?" >> $O
    echo "--- 复核 ---" >> $O
    md5sum $DST >> $O 2>&1
    ls -laZ $DST >> $O 2>&1
    echo "--- 重启 BT 进程 ---" >> $O
    pidof com.android.bluetooth >> $O 2>&1
    kill `pidof com.android.bluetooth` >> $O 2>&1
    echo "kill_rc=$?" >> $O
  fi
fi

sleep 3
ps > /data/local/tmp/.ps_after 2>&1
grep -i blue /data/local/tmp/.ps_after >> $O 2>&1
echo "=== end ===" >> $O
chmod 666 $O
