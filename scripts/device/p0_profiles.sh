#!/system/bin/sh
DM=/data/local/tmp/.dm3.txt
echo "=== profiles ===" > $O
dumpsys bluetooth_manager > $DM 2>&1
grep -n -e "Profile:" $DM >> $O 2>&1
echo "--- count ---" >> $O
grep -c -e "Profile:" $DM >> $O 2>&1
echo "--- sink-ish keywords ---" >> $O
grep -i -n -e "sink" -e "hfclient" -e "HfpClient" -e "AvrcpController" -e "a2dpsink" $DM >> $O 2>&1
echo "--- dumpsys bluetooth (adapter svc) ---" >> $O
dumpsys bluetooth > /data/local/tmp/.dm4.txt 2>&1
head -n 30 /data/local/tmp/.dm4.txt >> $O 2>&1
echo "--- pm list bt packages ---" >> $O
pm list packages -f > /data/local/tmp/.pkgs.txt 2>&1
grep -i -e blue /data/local/tmp/.pkgs.txt >> $O 2>&1
chmod 666 $DM
chmod 666 $O
