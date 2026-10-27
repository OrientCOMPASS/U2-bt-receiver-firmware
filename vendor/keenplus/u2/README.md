# vendor/keenplus/u2 -- extracted proprietary blobs

This directory holds the KeenPlus U2 **proprietary blobs** pulled from the
baseline Android 7.0 `system` image, plus the **generated** prebuilt makefiles
that turn them into AOSP modules.

The blobs themselves are **not committed** (licensing + ~150 MiB of binaries);
they are produced at build time.  Only the makefiles and this README are tracked.

## Layout

```
vendor/keenplus/u2/
  Android.mk        GENERATED -- prebuilt module definitions (BUILD_PREBUILT)
  blobs.mk          GENERATED -- PRODUCT_PACKAGES list, included by device.mk
  README.md         this file
  proprietary/      GITIGNORED -- extract-files.sh writes the blobs here
    vendor/lib/...      32-bit HALs and libraries
    vendor/bin/...      native executables (wmt_loader, mnld, ...)
    etc/firmware/...    MT6627 BT/Wi-Fi/FM firmware
    ...
```

## How the blobs get here

1. Obtain the baseline `system.img` (sparse or raw) from the Android 7.0 flash
   package.
2. Run the extractor, which drives `scripts/mtk_sparse2raw.py` + `debugfs`
   (no root needed) and 32-bit-checks every ELF with `readelf`:

   ```
   device/keenplus/u2/extract-files.sh <system.img>
   ```

   It reads `device/keenplus/u2/proprietary-files.txt` and writes each entry
   under `vendor/keenplus/u2/proprietary/`, aborting if any blob is 64-bit-only
   (decision D2: this is a 32-bit-only port).

## How the makefiles stay in sync

`Android.mk` and `blobs.mk` are **generated** by `scripts/gen-vendor-mk.py`
from `proprietary-files.txt` -- never edit them by hand.  To regenerate after
changing the blob list:

```
python3 scripts/gen-vendor-mk.py          # write both files
python3 scripts/gen-vendor-mk.py --check  # CI gate: fail if they are stale
```

`device/keenplus/u2/device.mk` does `include vendor/keenplus/u2/blobs.mk`, so
every module defined in `Android.mk` is added to `PRODUCT_PACKAGES` and gets
installed.

## What is NOT handled automatically

`gen-vendor-mk.py` only creates prebuilt **modules** for `.so` libraries and
`bin/` executables.  Firmware and config **data** (`etc/firmware/*`,
`etc/wifi/*`, `vendor/etc/audio_param/`, `vendor/etc/.tp`, ...) are listed as
comments at the bottom of `blobs.mk` and must be installed with explicit
`PRODUCT_COPY_FILES` entries in `device.mk` (the MT6627 firmware already is).
Directories like `audio_param/` and `.tp/` are copied wholesale by
`extract-files.sh`; add a `PRODUCT_COPY_FILES` line per file, or a small
`Android.mk` `include $(BUILD_PREBUILT)` for the directory, at full-build time.

## Install-path caveats (full-build only)

The generated `Android.mk` uses sensible defaults -- `LOCAL_VENDOR_MODULE := true`
for `vendor/` blobs, `LOCAL_MODULE_RELATIVE_PATH := hw` for `hw/` HALs,
`LOCAL_MULTILIB := 32`, and no `LOCAL_SHARED_LIBRARIES` (BUILD_PREBUILT resolves
`DT_NEEDED` at load time).  These cannot be compile-verified in CI (this repo
does not build AOSP -- see `patches/README.md`).  If a real build reports an
unresolved symbol or a wrong install path, adjust the affected module and
re-run the generator; verify dependencies with `readelf -d proprietary/<path>`.
