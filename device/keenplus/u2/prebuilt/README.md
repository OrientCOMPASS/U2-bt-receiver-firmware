# prebuilt/ -- binary build inputs extracted from the official baseline

## kernel

The stock MTK kernel exactly as shipped in `boot.img` of the baseline firmware
`B04_35_1.5_8_3inch_M304_ST7701SN_20251016` (release `base_firmware`).
Extracted 2026-10-28 by range-fetching `boot.img` (8,754,472 B) from the
baseline zip and slicing the header's kernel payload.

* **Layout** (matches `../BoardConfig.mk` "DTB appended to Image.gz, header
  `dt_size=0`"): gzip Image (magic `1f8b08`; 6,346,120 B compressed ->
  15,569,376 B raw) + appended DTB (63,240 B; `d00dfeed` at tail offset 0,
  `totalsize` field agrees). Total = `kernel_size` 6,409,360 B.
* **Boot header (v0, page_size 2048)** verified field-by-field against
  BoardConfig: `kernel_addr 0x40080000`, `ramdisk_addr 0x44000000`,
  `second_addr 0x40f00000`, `tags_addr 0x4e000000`, `dt_size 0`,
  name field carries MTK's `bootopt=64S3,32N2,64N2`.
* **Version string**: `Linux version 3.18.35 (ts01@blueshark02) (gcc version
  4.9 ...) #1 SMP PREEMPT` -- 3.18 is an officially supported kernel level
  for Android 8.1 (non-Treble).
* **md5**: `4128dc54373f68008afd1eb6ed25c105`

The u2 build consumes this via `TARGET_PREBUILT_KERNEL`; legacy `mkbootimg`
repacks it with the 8.1 ramdisk using the BoardConfig offsets (header v0),
so the resulting `boot.img` stays byte-layout-compatible with the stock
MTK loader/LK.

This file is a *build input* (like a HAL blob), not a generated artifact --
it is intentionally committed.
