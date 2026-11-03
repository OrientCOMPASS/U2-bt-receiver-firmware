#!/system/bin/sh
# p1e_capture_keys.sh — 抓有线耳机线控的原始 input 事件（P1e 映射表的唯一依据）
#
# 前置：把线控插进 U2 的耳机口。脚本启动后有 ~28 秒窗口，请依次做：
#   单击(播放键) → 停顿 → 双击 → 停顿 → 长按 2 秒 → 停顿 → 音量+ → 音量-
# 每一步之间留半秒，方便按时序切分。
#
# 已知事实（p1e_probe_input.sh 实测）：
#   /dev/input/event0 = "ACCDET"（MTK 配件检测驱动）
#   能力位只有 4 个 KEY：114 VOLUMEDOWN / 115 VOLUMEUP / 164 PLAYPAUSE / 582 VOICECOMMAND
#   —— 没有 NEXTSONG/PREVIOUSSONG/HEADSETHOOK，所以"切歌"必须由时序推断，
#      本脚本抓的就是这个时序。
#   /sys/class/switch/h2w/state：0=无配件 1=带麦耳机 2=无麦耳机
#
# 规矩：禁用管道；后台进程 + sleep + kill；输出尽早 chmod 666。

GE=/data/local/tmp/p1e_ge.txt

exec >"$O" 2>&1
chmod 666 "$O" 2>/dev/null
echo "=== P1e 线控按键抓取  $(date) ==="

echo "--- 0. 耳机插入状态（h2w：0 无 / 1 带麦 / 2 无麦）---"
cat /sys/class/switch/h2w/state 2>/dev/null
echo "(若为 0，说明没检测到配件，下面大概率抓不到按键)"

echo "--- 1. 设备确认 ---"
getevent -p /dev/input/event0 2>&1
ls -l /dev/input/event0

rm -f "$GE"
echo "--- 2. 开始抓取 28 秒：请按 单击 / 双击 / 长按 / 音量+ / 音量- ---"
getevent -lt /dev/input/event0 >"$GE" 2>&1 &
GPID=$!
echo "getevent pid=$GPID"

i=0
while [ $i -lt 14 ]; do
  sleep 2
  i=$((i+1))
  cat /sys/class/switch/h2w/state > /data/local/tmp/p1e_h2w.txt 2>/dev/null
done

kill $GPID 2>/dev/null
sleep 1
kill -9 $GPID 2>/dev/null
chmod 666 "$GE" 2>/dev/null

echo "--- 3. 抓取结束，事件流（带时间戳与标签）---"
wc -l "$GE" 2>/dev/null
head -n 300 "$GE"

echo "--- 4. h2w 最终状态 ---"
cat /data/local/tmp/p1e_h2w.txt 2>/dev/null
cat /sys/class/switch/h2w/state 2>/dev/null

echo "=== end ==="
chmod 666 "$O" 2>/dev/null
