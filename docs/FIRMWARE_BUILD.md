# Firmware build, repack & release (SP_Flash_Tool package)

This document describes how the KeenPlus U2 AOSP 8.1 firmware is turned into a
**SP_Flash_Tool-flashable package** and published to a GitHub Release, and it is
explicit about what can and cannot be done in CI.

## Goal

Produce a zip that SP_Flash_Tool can load directly (scatter + every downloadable
partition image), built from this repo's AOSP 8.1 tree + patches, that replaces
the official Android 7.0 firmware on a KeenPlus U2_6735_1.5+8G.

## The compute reality (read this first)

A full AOSP 8.1 build needs **~100-190 GB of disk** (source ~40-70 GB shallow +
`prebuilts/` toolchain + `out/` ~30-80 GB), **>= 32 GB RAM**, and many cores for
hours.  AOSP's own guidance is "400 GB free (250 checkout + 150 build)".

- A **standard GitHub-hosted runner is 14 GB disk / 4 vCPU** -> it physically
  cannot build AOSP.  "Trimmed" source does not help: the few-GB MTK BSP repos
  (e.g. `Iscle/OrangePi_4G-IOT_Android_8.1_BSP`, 2.16 GB) are small *because*
  they omit `prebuilts/`, `system/`, `vendor/` and `toolchain/` -- exactly the
  compiler and platform you need, so they cannot build.
- Therefore the real build runs on a **self-hosted runner** (>= 300 GB disk,
  >= 32 GB RAM, Linux, JDK 8) or a **GitHub larger runner** (paid, big NVMe).

The build workflow ([`.github/workflows/build.yml`](../.github/workflows/build.yml))
is `workflow_dispatch`-only and `runs-on` the runner label you supply, so it never
runs (and never fails) on a standard runner by accident.

> **Faster iteration.** After the first full build, `build_mode: incremental`
> (or `scripts/build.sh --incremental`) rebuilds only the Bluetooth modules and
> regenerates `system.img` via `snod` (~10-20 min) on a self-hosted runner that
> persists the tree + `out/`.  It cannot replace the first full build -- see
> [`INCREMENTAL_BUILD.md`](INCREMENTAL_BUILD.md) for the evidence-based analysis
> of why "compile only our parts and drop them on a prebuilt 8.1" does not work.

What CI *can* prove on every push, without a big machine, is the **entire
packaging path against the real device layout** -- see "What CI verifies" below.

## Hybrid repack (the only viable MTK approach)

An MT6735M SP_Flash_Tool package = a **scatter** file + every `is_download: true`
partition image + (optionally) the download agent.  Most bootloader partitions are
**MTK-private and cannot be built from AOSP** (`preloader`, `lk`, `tee`, `logo`,
`secro`).  So the package is assembled by *reuse + rebuild*:

| Source | Partitions |
|---|---|
| **Reused from the official baseline** (range-fetched, ~1 MB total) | `preloader`, `lk`, `logo`, `secro`, `trustzone` (tee1+tee2), empty `cache`, empty `userdata`, and the **official scatter** |
| **Built from this repo** (AOSP 8.1 + patches) | `system.img`, `boot.img`, `recovery.img` |

The official `MT6735M_Android_scatter.txt` is reused **verbatim** -- we do not
modify the device's partition layout, we only supply images under the exact
`file_name`s it references.  This is what makes the result flashable on a stock
U2 with SP_Flash_Tool.

### Official partition layout (from the baseline scatter)

Downloadable partitions (what SP_Flash_Tool writes):

| Partition | file_name | Size | Region | Source |
|---|---|---|---|---|
| preloader | preloader_tek6735m_65_d_n.bin | 0.25 MiB | EMMC_BOOT_1 | reuse |
| lk | lk.bin | 0.34 MiB | EMMC_USER | reuse |
| boot | boot.img | 16 MiB | EMMC_USER | **build** |
| recovery | recovery.img | 16 MiB | EMMC_USER | **build** |
| logo | logo.bin | 0.55 MiB | EMMC_USER | reuse (or `make_logo.py`) |
| secro | secro.img | 0.13 MiB | EMMC_USER | reuse |
| tee1 / tee2 | trustzone.bin | 0.16 MiB | EMMC_USER | reuse |
| system | system.img | 2952 MiB | EMMC_USER | **build** |
| cache | cache.img | 400 MiB | EMMC_USER | reuse (empty) |
| userdata | userdata.img | 3072 MiB | EMMC_USER | reuse (empty, resizes on first boot) |

Preserved / created on device (`is_download: false`, not in the package): `pgpt`,
`proinfo`, `nvram`, `protect1/2`, `para`, `expdb`, `seccfg`, `oemkeystore`,
`keystore`, `frp`, `nvdata`, `metadata`, `flashinfo`, `sgpt`.

Note the built `system.img` must be <= 2952 MiB (target R1 is <= 620 MiB, so it
fits with room to spare), and `userdata` = 3072 MiB satisfies R2 (>= 2.4 GiB).

## Pipeline

```
 scripts/build.sh --sync --make            # AOSP 8.1 -> out/target/product/u2/{system,boot,recovery}.img
        |                                  #   (needs a big runner; reuses baseline kernel + our ramdisk)
        v
 scripts/repack_firmware.py                # range-fetch scatter + MTK-private parts from the baseline,
   --baseline-url <zip> --build-out <dir>  #   substitute the built images, validate vs the scatter,
   --out U2_AOSP81_<tag>.zip               #   zip an SP_Flash_Tool package
        |
        v
 scripts/publish_release.py                # create/update the GitHub Release and upload the zip
   --tag <tag> --files <zip>
```

Supporting tools:
- [`scripts/mtk_fw_zip.py`](../scripts/mtk_fw_zip.py) -- read the baseline zip's
  central directory over HTTP **range requests** and extract single entries, so we
  fetch ~1 MB of private partitions instead of the 1.9 GiB archive.
- [`scripts/mtk_scatter.py`](../scripts/mtk_scatter.py) -- parse the scatter,
  list required image files, and **validate** a package (completeness + each image
  fits its partition).
- [`scripts/make_logo.py`](../scripts/make_logo.py) -- inspect/replace the `logo`
  partition if a custom boot logo is wanted.

## What CI verifies (every push, no big machine)

[`ci.yml`](../.github/workflows/ci.yml) job **"Verify patches & package firmware"**:
1. `apply-patches.sh --verify` -- all 6 patch series `git am` onto fresh
   `android-8.1.0_r80` clones.
2. `repack_firmware.py --dry-run --baseline-url <official zip>` -- range-fetches
   the **real** scatter + preloader/lk/logo/secro/trustzone/cache/userdata and
   substitutes **placeholder** boot/recovery/system, then assembles the package.
3. `mtk_scatter.py validate` -- the assembled package is complete and within
   partition sizes, with the scatter's canonical name preserved.

So the packaging/scatter/release logic is continuously proven against the actual
device layout.  The one thing CI cannot do is the AOSP **compile** (big runner)
and the on-hardware **boot/flash** check (needs a physical U2).

## Producing a real release

### Option A -- self-hosted / larger runner (recommended)

1. Add a self-hosted runner to the repo (Linux, >= 300 GB free disk, >= 32 GB RAM,
   JDK 8, `git curl python3 unzip readelf debugfs`), or enable a GitHub **larger
   runner** with a big NVMe disk.
2. In the GitHub UI: **Actions -> Build firmware -> Run workflow**, set
   `runner_label` to your runner's label, leave `baseline_url` as-is, choose
   `prerelease`, and run.
3. The workflow builds AOSP, repacks, validates, and publishes the zip to a
   Release tagged `u2-aosp81-<date>-<sha>`.

### Option B -- any big Linux box (manual)

```bash
git clone https://github.com/OrientCOMPASS/U2-bt-receiver-firmware && cd U2-*
# 1. build (provisions its own AOSP tree under --workdir)
bash scripts/build.sh --workdir /path/aosp --sync --make --jobs "$(nproc)" \
     --system-image /path/baseline/system.img
# 2. repack
python3 scripts/repack_firmware.py \
     --baseline-url "<official baseline zip url>" \
     --build-out /path/aosp/out/target/product/u2 \
     --out U2_AOSP81.zip --pkg-name U2_AOSP81
# 3. publish (needs a token with contents:write)
python3 scripts/publish_release.py --tag u2-aosp81-manual \
     --files U2_AOSP81.zip --token "$GH_TOKEN" --prerelease
```

## Flashing with SP_Flash_Tool

1. Unzip the release asset.
2. Open SP_Flash_Tool (v5.x for MT6735), **Scatter-loading File** -> select
   `MT6735M_Android_scatter.txt`.
3. Ensure the partitions are ticked (preloader, lk, boot, recovery, logo, secro,
   tee1, tee2, system, cache, userdata).
4. Click **Download** (SP_Flash_Tool's "Firmware Upgrade"/"Download" writes the
   ticked partitions).  The device must be **powered off**, then connect USB --
   the preloader handshake starts the download.
5. First boot wipes `/data` (the 7.0 FDE metadata is incompatible with 8.1) and
   `userdata` resizes to fill its 3 GiB partition.

## Caveats (honest)

- **Not hardware-verified here.**  Neither the sandbox nor CI has a U2.  The
  package is *structurally* correct (validated against the official scatter), but
  "boots and all features work" must be confirmed on a real device.
- **Recovery.**  If you build `recovery.img` from this tree it is an 8.1 recovery;
  the baseline recovery is 7.0.  `build.yml` uses the built one (correct for F11
  OTA/sideload).  The CI dry-run uses a placeholder.
- **Bootloader reuse.**  `preloader`/`lk`/`tee`/`secro` come from the official
  firmware and are tied to the device's secure-boot/eFuse state.  Reusing them
  verbatim is what keeps the package flashable; do not attempt to rebuild them
  from AOSP (impossible) or flash them to a different board.
- **logo.**  Reused by default; regenerate with `make_logo.py` only if you keep the
  exact panel resolution/RGB565 format.
