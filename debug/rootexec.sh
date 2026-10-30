#!/system/bin/sh
# rootexec.sh -- run AS ROOT by init (debug boot v5) at post-fs-data, every boot.
# Job: swap in the sink BT stack (idempotent), keep root-level diagnostics fresh.
: > /data/local/tmp/rootexec.log 2>/dev/null
exec >>/data/local/tmp/rootexec.log 2>&1
echo "=== rootexec $(date) ==="
id
NEW=/data/local/tmp/bt_new.so
TGT=/system/lib/hw/bluetooth.default.so
BAK=/data/local/tmp/bt_stock_backup.so
WANT=c561b6128e9293bde3280f7aca3d5b17
if [ -f "$NEW" ]; then
  echo "payload md5: $(md5sum $NEW)"
  GOT=$(md5sum $TGT | cut -d' ' -f1)
  echo "target md5 before: $GOT"
  if [ "$GOT" != "$WANT" ]; then
    if [ ! -f "$BAK" ]; then cp $TGT $BAK; echo "backup md5: $(md5sum $BAK | cut -d' ' -f1)"; fi
    if mount -o rw,remount /system; then echo "remount rw OK"; else echo "remount rw FAILED"; fi
    if cp $NEW $TGT; then echo "copy OK"; else echo "copy FAILED"; fi
    chmod 644 $TGT
    chown 0:0 $TGT 2>/dev/null
    restorecon $TGT 2>/dev/null
    sync
    echo "target md5 after: $(md5sum $TGT | cut -d' ' -f1)"
    mount -o ro,remount /system 2>/dev/null
  else
    echo "already swapped -- skipping copy"
  fi
else
  echo "no payload at $NEW -- nothing to do"
fi
chmod 666 /data/local/tmp/rootexec.log $BAK 2>/dev/null
( while true; do
    dmesg > /data/local/tmp/root_dmesg.txt 2>&1
    logcat -b all -d -v threadtime > /data/local/tmp/root_logcat.txt 2>&1
    ps > /data/local/tmp/root_ps.txt 2>&1
    getprop > /data/local/tmp/root_props.txt 2>&1
    chmod 666 /data/local/tmp/root_*.txt 2>/dev/null
    sleep 20
  done ) </dev/null >/dev/null 2>&1 &
echo "rootexec done"
