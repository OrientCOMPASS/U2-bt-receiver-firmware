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

## Phase 1 probe results (run 40922183963, artifact `fw-probe-report`)

* **95 APK records** in app/, priv-app/, vendor/app/, vendor/priv-app/,
  preload/.  Baseline has NO telephony UI apps (no TeleService/Dialer/
  Contacts/Mms) -- the vendor already stripped them; `com.android.phone`
  is not in the REQUIRED boot-critical assertion for this reason.
* **`/system/preload/hotapps/` = 993.0 MiB of 15 third-party APKs**
  (Bilibili, iQIYI lite, QQ Music, NetEase Cloud Music, Xigua Video,
  Ximalaya, Tomato/Qimao novels, Kuaikan comics, Moon+ Reader Pro, Kugou
  lite, Zapya, Eudic, ...).  `/system/preinstall/` is empty.  The MTK
  preload service copies `preload/` APKs into /data/app on first boot --
  this is where the device's bloat actually lands.
* **userdata.img and cache.img ship empty** (lost+found only) -- nothing
  to debloat there; they pass through untouched.
* **dm-verity metadata EXISTS**: 46 MiB trailing area beyond the ext4 fs
  (3,095,396,352 - 3,047,079,936 B); full-area scan found the footer magic
  `0xb001b001` at offset 3,071,078,400 (right after the ~24 MiB hashtree).
  With the stock fstab `ro wait,verify` an edited system.img could never
  mount => the verity-off boot repack is MANDATORY, and the stale
  hashtree/footer tail is zeroed after the edit (nothing reads it once the
  verify flag is gone).
* Stock boot ramdisk (64 entries): `fstab.mt6735` 0640, `verity_key`
  present, `init.mt6735.rc`, `enableswap.sh`, zygote64_32, default.prop
  `ro.secure=1 ro.debuggable=0` (user build), `bs.fake.ram=1.5
  bs.fake.rom=8`.  Kernel md5 `4128dc54373f68008afd1eb6ed25c105` matches
  the reference.  Boot carries a 1320 B MTK signature tail.
* No `/system/etc/install-recovery.sh` => the `recovery-from-boot.p`
  leftover cannot rewrite our repacked boot on first start.
* `/system/data/` holds a single `recovery_rootcheck` blob (110 KiB) --
  left untouched (not an app).

## Curation decision (remove-list v0)

**Removed (4 entries, ~1.01 GiB):** `preload/hotapps` (15 APKs, 993 MiB),
`app/BsHotApps` (com.android.hotapps promo store, 13 MiB -- not an AOSP
package despite the name), `vendor/priv-app/Baidu_Location`
(com.baidu.map.location, 2.4 MiB -- no references in etc/permissions or
preferred-apps; GPS/AGPS unaffected), `priv-app/ValidationTools`
(com.teksun.factorytest ODM factory tool, 1.4 MiB).

**Kept:** every AOSP platform app (incl. WebView, LatinIME, Gallery2,
DeskClock, Exchange2, PicoTts), every MediaTek BSP app (incl. MiraVision
60 MiB, EngineerMode, Camera, MTKLogger, BtTool, YGPS...), FMRadio,
WfoService.  Rationale: first-party platform/BSP components are not
"third-party preloads"; MiraVision/EngineerMode/Camera are attractive
future-debloat candidates for the BT-receiver product but out of scope
for THIS boot experiment -- one variable class at a time.

## Results

* phase 1 probe: run 40922183963 SUCCESS, artifact `fw-probe-report`
  (REPORT.md also lands in the run summary).
* phase 2 build: see release `firmware-debloat-*` (zip + VERIFICATION.md)
  and the `fw-debloat-report` artifact of the fw-debloat run.
