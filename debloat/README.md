# Debloat experiment — editing the official 7.0 baseline directly

> 2026-10-29.  Fresh start after the AOSP-8.1-from-source route (v0-v4) was
> abandoned.  This directory holds the experiment's charter and its curated
> removal list; the pipelines are `.github/workflows/fw-probe.yml` (phase 1,
> read-only inventory) and `.github/workflows/fw-debloat.yml` (phase 2,
> surgery + repack + validation + release).

## Question under test

Can the **official** KeenPlus U2 Android 7.0 firmware
(`B04_35_1.5_8_3inch_M304_ST7701SN_20251016.zip`, release `base_firmware`)
be edited in place — preinstalled third-party apps deleted from
`system.img` — and still boot normally?

## Baseline facts this design rests on

| fact | value | source |
|---|---|---|
| platform | MT6735M, Android **7.0** NRD90M `user/release-keys`, MTK `alps-mp-n0.mp1-V1.19` tree | `build.prop` (fw-probe re-dumps it every run) |
| system.img | MTK-variant sparse (magic `0xED26FF3A`, 4096 B blocks) -> raw **3,095,396,352 B** (= 2952 MiB, exactly the scatter partition size); ext4 4096 B blocks | `scripts/mtk_sparse2raw.py`, probe |
| dm-verity | stock boot ramdisk `fstab.mt6735` mounts `/system` `ro wait,verify` — **verity is ON** in the stock boot chain; the raw image carries a hashtree tail beyond the ext4 fs | probe (`system_verity.json`, `boot_fstab.txt`) |
| boot signing | stock `boot.img` ends in an MTK ASN.1/CMS tail signed with the **AOSP testkey** | previous route's analysis; re-checked every run (kernel md5 `4128dc54373f68008afd1eb6ed25c105`) |
| tooling | `mtk_fw_zip.py` (HTTP-range zip access), `mtk_bootimg.py disable-verity` (fstab edit + deterministic cpio repack + CMS re-sign, 5/5 unit tests), `repack_firmware.py` (official scatter + private parts passthrough), `mtk_scatter.py validate`, `publish_release.py` | `scripts/`, all unit-tested |

**Consequence:** an edited `system.img` can never match the stock verity
hashtree, so the experiment must ship a `boot.img` whose fstab no longer
carries the `verify` flag.  `mtk_bootimg.py disable-verity` makes exactly
that one change (plus a regenerated testkey signature tail); the kernel and
every other ramdisk byte are diff-asserted identical to stock.

## Pipeline

```
fw-probe (read-only)                     fw-debloat (surgery)
------------------------                 -------------------------
stream baseline zip (range HTTP)         stream baseline zip
scatter + zip inventory                  boot.img  -> disable-verity repack
boot/recovery ramdisk facts                (kernel byte-identical, testkey)
system: sparse->raw, verity scan,        recovery.img -> passthrough
  loop-mount ro, full app inventory      system.img:  sparse->raw, mount rw,
  (AXML package/version per APK),          remove debloat/remove-list.txt dirs,
  build.prop, etc/permissions dumps        zero-fill free space, e2fsck,
userdata/cache: preload APK scan           zero stale verity tail, img2simg
artifact: fw-probe-report                repack_firmware.py + official scatter
                                         validate: scatter, boot kernel md5,
                                           fstab verify gone, recovery md5,
                                           final image round-trip + app-set
                                           algebra, boot-critical packages
                                         release: firmware-debloat-<date>
                                           (zip + VERIFICATION.md, prerelease)
```

## What CI can and cannot prove

CI proves *structural* bootability: valid ext4 (e2fsck clean), intact boot
chain (kernel md5, ramdisk identical except the fstab flag), boot-critical
packages present, scatter-valid package, deterministic removal algebra.
CI **cannot** flash hardware — the definitive boot test is on-device
(SP Flash Tool, "Download Only"; rescue = reflash the official baseline).

## Removal-list policy

* keep: AOSP platform apps, MTK/BSP apps, anything boot-critical
  (asserted in CI), anything ambiguous until proven junk;
* remove: third-party preloads (adware, stores, social/media bundles,
  vendor extras unrelated to the BT-receiver product);
* every entry must be a top-level dir under `app/ priv-app/ vendor/app/
  vendor/priv-app/` — `scripts/debloat_system.py` rejects everything else
  before deleting anything, and reports dangling `etc/permissions`
  references (left in place; PMS ignores unknown packages).

## Results

* phase 1 probe: see the `fw-probe-report` artifact of the latest fw-probe
  run (REPORT.md appends to the run summary).
* phase 2 build: release `firmware-debloat-*` (zip + VERIFICATION.md).
