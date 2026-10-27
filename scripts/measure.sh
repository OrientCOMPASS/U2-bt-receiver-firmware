#!/usr/bin/env bash
# measure.sh -- collect the R1-R15 resource metrics from a connected U2 over adb
# (plan section 18.4).  Run from a host with adb and the device authorised.
#
# Usage: measure.sh [--serial <adb-serial>] [--out <report.txt>] [--pcap <if>]
#
# It prints a labelled report to stdout (and to --out if given).  Nothing here
# mutates the device; it only reads dumpsys/proc/df.

set -uo pipefail

SERIAL=""
OUT=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --serial) SERIAL="$2"; shift 2 ;;
        --out)    OUT="$2"; shift 2 ;;
        -h|--help) sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

ADB=(adb)
[[ -n "${SERIAL}" ]] && ADB=(adb -s "${SERIAL}")

command -v adb >/dev/null 2>&1 || { echo "ERROR: adb not found" >&2; exit 1; }
"${ADB[@]}" get-state >/dev/null 2>&1 || { echo "ERROR: no authorised device" >&2; exit 1; }

sh() { "${ADB[@]}" shell "$@" 2>/dev/null; }

emit() {
    # tee to stdout and (optionally) the report file
    if [[ -n "${OUT}" ]]; then
        printf '%s\n' "$*" | tee -a "${OUT}"
    else
        printf '%s\n' "$*"
    fi
}

[[ -n "${OUT}" ]] && : > "${OUT}"

emit "================ KeenPlus U2 resource report ================"
emit "date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
emit "build: $(sh getprop ro.build.display.id)"
emit "fingerprint: $(sh getprop ro.build.fingerprint)"
emit "abi: $(sh getprop ro.product.cpu.abilist)  zygote: $(sh getprop ro.zygote)"
emit

emit "== R1 /system usage (target <= 620 MiB) =="
emit "$(sh df /system)"
emit "preload present? (should be absent): $(sh ls -d /system/preload 2>/dev/null || echo NO)"
emit

emit "== R2 /data free (target >= 2.4 GiB) =="
emit "$(sh df /data)"
emit

emit "== R3/R5/R6/R7 memory (dumpsys meminfo) =="
emit "$(sh dumpsys meminfo | head -40)"
emit "-- R5 com.android.bluetooth (target PSS <= 70 MiB) --"
emit "$(sh dumpsys meminfo com.android.bluetooth | head -25)"
emit "-- R6 system_server (target PSS <= 130 MiB) --"
emit "$(sh dumpsys meminfo system_server | head -25)"
emit "-- R7 systemui + surfaceflinger (target <= 90 MiB) --"
emit "$(sh dumpsys meminfo com.android.systemui | head -15)"
emit "$(sh dumpsys meminfo /system/bin/surfaceflinger | head -15)"
emit

emit "== R8 boot time (logcat -b events boot_progress) =="
emit "$(sh logcat -b events -d | grep -E 'boot_progress' | tail -20)"
emit

emit "== R9 process count (target <= 22) =="
emit "ps -A | wc -l = $(sh ps -A | wc -l)"
emit

emit "== R10 running native services (target <= 12) =="
emit "$(sh getprop | grep -c '\[init\.svc\..*\]: \[running\]')"
emit "$(sh getprop | grep '\[init\.svc\..*\]: \[running\]')"
emit

emit "== R14 CPU during BT playback (target <= 35% single-core-equiv) =="
emit "NOTE: start A2DP sink playback, then run: adb shell top -n 5 -d 2"
emit

emit "== audio path / underruns (R12/R13) =="
emit "$(sh dumpsys media.audio_flinger | grep -iA6 'output thread' | head -40)"
emit "underruns: $(sh dumpsys media.audio_flinger | grep -i underrun | head)"
emit

emit "== Bluetooth state (F2/F3/F4) =="
emit "$(sh dumpsys bluetooth_manager | grep -iE 'A2dpSink|codec|state|profile' | head -40)"
emit

emit "== zram / swap (should be ~768 MiB, lz4) =="
emit "$(sh cat /sys/block/zram0/disksize 2>/dev/null)"
emit "$(sh cat /sys/block/zram0/comp_algorithm 2>/dev/null)"
emit "$(sh cat /proc/swaps 2>/dev/null)"
emit

emit "================== end of report =================="
emit "For R11/R12/R13 latency+glitch, capture on the host while playing:"
emit "  sudo dumpcap -i bluetooth0 -w u2.pcap"
emit "  tshark -r u2.pcap -Y 'avdtp.signalid == 3' -V | grep -iA14 'Media Codec'"
