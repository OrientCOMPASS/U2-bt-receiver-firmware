#!/system/bin/sh
# rootexec.sh v8 -- system APK swap + BT .so swap + streaming boot logcat.
: > /data/local/tmp/rootexec.log 2>/dev/null
exec >>/data/local/tmp/rootexec.log 2>&1
echo "=== rootexec v8 $(date) boot_completed=$(getprop sys.boot_completed) ==="
id
T=/data/local/tmp
# --- 1) BT stack .so swap (idempotent) ---
NEW=$T/bt_new.so
TGT=/system/lib/hw/bluetooth.default.so
BAK=$T/bt_stock_backup.so
WANT=7432a20a00215988f140f06e9350d1e8
if [ -f "$NEW" ]; then
  GOT=$(md5sum $TGT | cut -d' ' -f1)
  if [ "$GOT" != "$WANT" ]; then
    [ -f "$BAK" ] || cp $TGT $BAK
    mount -o rw,remount /system
    cp $NEW $TGT && chmod 644 $TGT && chown 0:0 $TGT 2>/dev/null && restorecon $TGT 2>/dev/null && sync
    echo "so swap done: $(md5sum $TGT)"
    mount -o ro,remount /system 2>/dev/null
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
  echo "early phase armed"
else
  cp /data/system/packages.xml $T/root_packages_post.xml 2>&1
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
echo "rootexec v8 done"
