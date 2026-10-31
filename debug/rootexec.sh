#!/system/bin/sh
# rootexec.sh v17b -- system APK swap + BT .so swap + streaming boot logcat.
: > /data/local/tmp/rootexec.log 2>/dev/null
exec >>/data/local/tmp/rootexec.log 2>&1
echo "=== rootexec v17b $(date) boot_completed=$(getprop sys.boot_completed) ==="
id
T=/data/local/tmp
# --- 1) BT stack .so swap (idempotent) ---
NEW=$T/bt_new.so
TGT=/system/lib/hw/bluetooth.default.so
BAK=$T/bt_stock_backup.so
WANT=$(cat $T/bt_want.txt 2>/dev/null | tr -d '\r\n ')
[ -z "$WANT" ] && WANT=5187ca6f4c69eacc3442ae9ec505bd83
if [ -f "$NEW" ]; then
  GOT=$(md5sum $TGT | cut -d' ' -f1)
  if [ "$GOT" != "$WANT" ]; then
    [ -f "$BAK" ] || cp $TGT $BAK
    mount -o rw,remount /system
    cp $NEW $TGT && chmod 644 $TGT && chown 0:0 $TGT 2>/dev/null && restorecon $TGT 2>/dev/null && sync
    echo "so swap done: $(md5sum $TGT)"
    mount -o ro,remount /system 2>/dev/null
    # v14: reload HAL - kill bt proc (zygote restarts it, snoop truncates fresh)
    BTPID=$(pidof com.android.bluetooth)
    echo "v16: kill bt $BTPID after so swap" >> $T/snoop_v11.txt
    kill $BTPID 2>/dev/null
    sleep 20
    { echo "v14: new pid $(pidof com.android.bluetooth), tgt=$(md5sum $TGT | cut -d' ' -f1)"
    } >> $T/snoop_v11.txt
  else
    echo "so already swapped"
  fi
else
  echo "no so payload"
fi
# --- 2) system Bluetooth.apk swap (idempotent, md5-gated) ---
APK_NEW=$T/bt_new_apk.apk
APK_TGT=/system/app/Bluetooth/Bluetooth.apk
APK_BAK=$T/Bluetooth_stock_backup.apk
APK_WANT=54d2b2bb6d2a4822c7cdabd5473ab5ce
if [ -f "$APK_NEW" ]; then
  GOT=$(md5sum $APK_TGT | cut -d' ' -f1)
  echo "apk target md5 before: $GOT"
  if [ "$GOT" != "$APK_WANT" ]; then
    if [ ! -f "$APK_BAK" ]; then cp $APK_TGT $APK_BAK; echo "apk backup: $(md5sum $APK_BAK)"; fi
    mount -o rw,remount /system
    rm -rf /system/app/Bluetooth/oat
    echo "oat removed: $(ls /system/app/Bluetooth/)"
    cp $APK_NEW $APK_TGT && chmod 644 $APK_TGT && chown 0:0 $APK_TGT 2>/dev/null && restorecon $APK_TGT 2>/dev/null && sync
    echo "apk swap done: $(md5sum $APK_TGT)"
    ls -laZ $APK_TGT
    mount -o ro,remount /system 2>/dev/null
  else
    echo "apk already swapped"
  fi
else
  echo "no apk payload"
fi
# --- 2b) enable HCI snoop logging (fluoride N keys in bt_stack.conf) ---
CONF=/etc/bluetooth/bt_stack.conf
if [ -f "$CONF" ] && ! grep -q "BtSnoopLogOutput=true" "$CONF"; then
  mount -o rw,remount /system
  sed -i "s/BtSnoopLogOutput=false/BtSnoopLogOutput=true/" "$CONF"
  sed -i "s|BtSnoopFileName=.*|BtSnoopFileName=/data/misc/bluetooth/logs/btsnoop_hci.log|" "$CONF"
  mount -o ro,remount /system 2>/dev/null
  echo "conf patched: $(grep -E BtSnoop $CONF | tr '\n' ' ')"
else
  echo "conf already patched or missing"
fi
cp /etc/bluetooth/bt_stack.conf $T/conf_copy.txt 2>/dev/null
ls -lZ /etc/bluetooth/bt_stack.conf >> $T/conf_copy.txt 2>/dev/null
chmod 666 $T/conf_copy.txt 2>/dev/null
mkdir -p /data/misc/bluetooth/logs 2>/dev/null
chown 1002:1002 /data/misc/bluetooth/logs 2>/dev/null
chmod 2770 /data/misc/bluetooth/logs 2>/dev/null
# --- 2c) v11 snoop path fix ---
# Root cause of EACCES: parent /data/misc/bluetooth is system:system 0770;
# bluetooth uid cannot even traverse it (DAC, SELinux is Permissive anyway).
# Stock snoop dir /data/misc/bluedroid (bluetooth:net_bt_stack) is writable.
if ! grep -q "bluedroid/btsnoop" "$CONF" 2>/dev/null; then
  mount -o rw,remount /system 2>/dev/null
  sed -i "s#^BtSnoopFileName=.*#BtSnoopFileName=/data/misc/bluedroid/btsnoop_hci.log#" "$CONF"
  sync
  mount -o ro,remount /system 2>/dev/null
  chmod 0771 /data/misc/bluetooth 2>/dev/null
  echo "v12: conf -> bluedroid path"
  BTPID=$(pidof com.android.bluetooth)
  echo "v11: killing bt pid $BTPID to reload conf"
  kill $BTPID 2>/dev/null
  sleep 15
fi
{ echo "== v11 verify =="
  grep BtSnoop "$CONF"
  echo "bt pid: $(pidof com.android.bluetooth)"
  ls -laZ /data/misc/bluedroid/ 2>/dev/null
  logcat -d 2>/dev/null | grep -iE "bt_snoop" | tail -n 5
} >> $T/snoop_v11.txt 2>&1
chmod 666 $T/snoop_v11.txt 2>/dev/null
# --- 2d) v13: copy live snoop file out (root-only readable) ---
if [ -f /data/misc/bluedroid/btsnoop_hci.log ]; then
  cp /data/misc/bluedroid/btsnoop_hci.log $T/snoop.log
  chmod 666 $T/snoop.log
  { echo "== snoop copy =="; ls -l $T/snoop.log; md5sum $T/snoop.log 2>/dev/null; } >> $T/snoop_v11.txt
fi
# --- 2f) v17b: raise AV/btif trace levels (conf ships TRC_*=2 = WARNING) ---
setprop log.tag D
if ! grep -q "^TRC_BTIF=4" "$CONF" 2>/dev/null; then
  mount -o rw,remount /system 2>/dev/null
  sed -i "s/^TRC_BTIF=.*/TRC_BTIF=4/" "$CONF"
  sed -i "s/^TRC_BTAPP=.*/TRC_BTAPP=4/" "$CONF"
  sed -i "s/^TRC_AVDT=.*/TRC_AVDT=4/" "$CONF"
  sed -i "s/^TRC_AVCT=.*/TRC_AVCT=4/" "$CONF"
  sed -i "s/^TRC_A2D=.*/TRC_A2D=4/" "$CONF"
  sync
  mount -o ro,remount /system 2>/dev/null
  echo "v17b: trace levels raised: $(grep -E '^TRC_(BTIF|BTAPP|AVDT|A2D)=' $CONF | tr '\n' ' ')"
  BTPID=$(pidof com.android.bluetooth)
  echo "v17b: kill bt $BTPID to reload conf"
  kill $BTPID 2>/dev/null
  sleep 15
else
  echo "v17b: trace levels already raised or conf missing"
fi
cp $CONF $T/conf_copy.txt 2>/dev/null
chmod 666 $T/conf_copy.txt 2>/dev/null
# --- 2g) v17b: generic root command channel (one-shot, file-driven) ---
if [ -f $T/cmd.txt ]; then
  echo "v17b: running cmd.txt"
  sh $T/cmd.txt > $T/cmd_out.txt 2>&1
  chmod 666 $T/cmd_out.txt
  rm $T/cmd.txt
  echo "v17b: cmd done"
fi
# --- 3) phase-specific captures ---
BC=$(getprop sys.boot_completed)
if [ -z "$BC" ] || [ "$BC" = "0" ]; then
  cp /data/system/packages.xml $T/root_packages_pre.xml 2>&1
  ls -laR /data/app > $T/root_dataapp_pre.txt 2>&1
  ( n=0; while [ $n -lt 2 ]; do
      logcat -v threadtime -b all >> $T/root_stream.txt 2>&1 &
      LP=$!
      sleep 75
      kill $LP 2>/dev/null
      n=$((n+1))
    done ) </dev/null >/dev/null 2>&1 &
  ( i=0; while [ $i -lt 20 ]; do
      logcat -d -t 400 > $T/root_bootlog_$i.txt 2>&1
      chmod 666 $T/root_bootlog_$i.txt 2>/dev/null
      sleep 10
      i=$((i+1))
    done ) </dev/null >/dev/null 2>&1 &
  echo "early phase armed v10"
else
  cp /data/system/packages.xml $T/root_packages_post.xml 2>&1
  ls -laZ /data/misc/bluetooth/ > $T/snoop_ls.txt 2>&1
  ls -laZ /data/misc/bluetooth/logs/ >> $T/snoop_ls.txt 2>&1
  ls -la /sdcard/mtklog/btlog/ >> $T/snoop_ls.txt 2>&1
  chmod 666 $T/snoop_ls.txt 2>/dev/null
  echo "late phase captured"
fi
( while true; do
    dmesg > $T/root_dmesg.txt 2>&1
    logcat -b all -d -v threadtime > $T/root_logcat.txt 2>&1
    ps > $T/root_ps.txt 2>&1
    getprop > $T/root_props.txt 2>&1
    chmod 666 $T/root_*.txt $T/root_*.xml $T/rootexec.log 2>/dev/null
    sleep 20
  done ) </dev/null >/dev/null 2>&1 &
echo "rootexec v17b done"
