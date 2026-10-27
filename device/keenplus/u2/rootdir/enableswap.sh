#!/system/bin/sh
# enableswap.sh -- KeenPlus U2 zram setup (plan section 14.3).
#
# The baseline shipped an enableswap.sh whose body was
#   echo 0 > /sys/block/zram0/disksize
# i.e. it set the size to ZERO, so zram was effectively disabled.  This version
# actually enables a 768 MiB lz4-compressed swap device, which is the single
# biggest lever for keeping a 1.5 GB device responsive under A2DP sink load
# (serves R4).  768 MiB (not 1 GiB) is the benefit/overhead balance point: the
# zram metadata + compression pool themselves consume RAM.

ZRAM=/sys/block/zram0

# Reset any prior state so disksize is writable.
echo 1 > ${ZRAM}/reset 2>/dev/null

# Prefer lz4 (faster and lower CPU than lzo on the 4xA53); fall back silently if
# the kernel was not built with CONFIG_ZRAM_LZ4_COMPRESS.
echo lz4 > ${ZRAM}/comp_algorithm 2>/dev/null

# 768 MiB
echo 805306368 > ${ZRAM}/disksize

# Match the number of compression streams to the CPU count (4 on MT6735M).
echo 4 > ${ZRAM}/max_comp_streams 2>/dev/null

/system/bin/mkswap /dev/block/zram0
/system/bin/swapon -p 32767 /dev/block/zram0
