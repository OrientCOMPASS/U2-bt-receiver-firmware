# KeenPlus U2 -- AOSP 8.1 Bluetooth-receiver firmware

Self-developed **Android 8.1 (Oreo)** firmware for the **KeenPlus U2_6735_1.5+8G**
media player, rebuilt as a lean **Bluetooth audio receiver** (A2DP Sink) on the
MediaTek **MT6735M** SoC (quad Cortex-A53, **1.5 GiB** RAM, **8 GiB** storage).

The stock device shipped Android 7.0 with a heavy MTK skin, telephony, camera and
~1 GiB of preloaded bloat.  This project strips it to a fast, single-purpose
audio sink + local player that boots AOSP 8.1, connects to one or two phones
over Bluetooth, and plays their audio (SBC / AAC / SBC-XQ receive; LDAC
transmit), within a tight RAM/latency budget.

[![CI](https://github.com/OrientCOMPASS/U2-bt-receiver-firmware/actions/workflows/ci.yml/badge.svg)](https://github.com/OrientCOMPASS/U2-bt-receiver-firmware/actions/workflows/ci.yml)

> **Status / scope.**  This repository contains the *firmware engineering*: the
> device tree, resource overlays, SEPolicy, kernel-config contract, the AOSP
> source **patches** that add the missing Bluetooth codecs, and the tooling to
> apply / verify / build / **repack into an SP_Flash_Tool package** / publish a
> release / measure.  It does **not** commit a prebuilt image, the AOSP tree, or
> the proprietary blobs (extracted at build time).  A full AOSP 8.1 build needs
> ~100-190 GB disk and >= 32 GB RAM, so it cannot run on a standard GitHub runner
> (14 GB); the real build + release is therefore a **gated workflow**
> ([`build.yml`](.github/workflows/build.yml)) that runs on a self-hosted or larger
> runner.  What CI proves on *every* push, with no big machine, is the patch
> series **and** the whole packaging path against the *real* device layout (see
> [`docs/FIRMWARE_BUILD.md`](docs/FIRMWARE_BUILD.md)).  Hardware metrics
> (R3/R8/R11-R15) and final flashability need a physical U2 -- see
> [`scripts/measure.sh`](scripts/measure.sh).

---

## Target hardware

| | |
|---|---|
| Device | KeenPlus U2_6735_1.5+8G |
| SoC | MediaTek MT6735M (4x Cortex-A53, 32-bit userspace) |
| RAM / storage | 1.5 GiB / 8 GiB |
| Display | 3" ST7701SN DSI panel |
| BT/Wi-Fi/FM | MT6627 combo (BT 4.0, Wi-Fi 2.4G b/g/n, FM) |
| Baseline | Android 7.0 (MTK), reused kernel **Linux 3.18.35** + appended DTB |

## Feature & resource targets

The full specification is [`docs/DEVELOPMENT_PLAN.md`](docs/DEVELOPMENT_PLAN.md).
Headline acceptance criteria:

- **F1-F5** Bootable AOSP 8.1; A2DP **Sink** with SBC, AAC and SBC-XQ receive.
- **F6** Dual-device (one-to-two) connection + switching.
- **F7** A2DP **Source** with LDAC transmit.  **F8** AVRCP controller.
- **F9** Local playback.  **F10** Wi-Fi + FM.  **F11** OTA / reflash / rollback.
- **R1** `/system` <= 620 MiB, **R2** `/data` >= 2.4 GiB, **R3** idle RAM <= 380 MiB,
  **R5** bluetooth PSS <= 70 MiB, **R8** boot <= 30 s, **R9** <= 22 resident
  processes, **R11** BT connect-to-sound <= 1.5 s, **R12** A/V latency <= 180 ms,
  **R13** <= 2 glitches / 30 min, **R14** CPU <= 35 % while receiving.

**Non-goals:** no GMS, no telephony/SIM/SMS, no Treble, no Android 9+, no custom
kernel build, no camera, no multi-stream mixing, no LDAC *receive*.

---

## Repository layout

```
.
├── docs/
│   ├── DEVELOPMENT_PLAN.md       full specification (features, resources, decisions)
│   └── FIRMWARE_BUILD.md         build -> repack -> SP_Flash_Tool package -> release
├── device/keenplus/u2/           AOSP device tree (product + board config)
│   ├── BoardConfig.mk            partition sizes, kernel args, non-Treble, verity off
│   ├── device.mk / u2.mk         package whitelist, copy-files, board properties
│   ├── AndroidProducts.mk        lunch target: u2-userdebug
│   ├── bdroid_buildcfg.h         compile-time Bluetooth stack switches
│   ├── overlay/…/config.xml      Bluetooth profile on/off (A2DP sink + AVRCP ctrl)
│   ├── audio/…configuration.xml  A2DP input accepts 44100|48000 (AAC sink)
│   ├── bluetooth/                bt_stack.conf, bt_did.conf
│   ├── rootdir/                  fstab.mt6735, init.zygote32.rc, enableswap.sh
│   ├── sepolicy/                 baseline .te + *_contexts for the custom helper
│   ├── proprietary-files.txt     curated blob list (what to extract)
│   └── extract-files.sh          pulls blobs from the baseline system image
├── kernel/                       reused-kernel contract (config fragment + auditor)
├── patches/                      AOSP source patches (see patches/README.md)
│   ├── hardware_libhardware/     BTAV_A2DP_CODEC_INDEX_SINK_AAC enum
│   ├── system_bt/                AAC sink codec + decoder backport, SBC-XQ bitpool
│   └── packages_apps_Bluetooth/  per-device StreamHandler binding (+ MULTIDEVICE.md)
├── vendor/keenplus/u2/           generated prebuilt makefiles (blobs gitignored)
├── scripts/                      apply/build/measure/logo/repack/publish + tests
└── .github/workflows/
    ├── ci.yml                    every push: lint, unit tests, patch + package verify
    └── build.yml                 gated: AOSP build -> repack -> publish release
```

---

## Quick start

### 1. Verify the patches apply to pristine AOSP 8.1 (what CI runs)

```bash
scripts/apply-patches.sh --verify
```

Fresh shallow-clones `hardware/libhardware`, `system/bt` and
`packages/apps/Bluetooth` at tag `android-8.1.0_r80` and `git am`s each series.
Non-zero exit if any patch fails.

### 2. Apply the patches to a real tree

```bash
repo init -u https://android.googlesource.com/platform/manifest -b android-8.1.0_r80
repo sync -j8
scripts/apply-patches.sh --aosp-root "$PWD"
```

### 3. Extract the proprietary blobs (from the baseline system image)

```bash
device/keenplus/u2/extract-files.sh /path/to/baseline/system.img
```

Writes 32-bit-checked blobs into `vendor/keenplus/u2/proprietary/`.  The prebuilt
module makefiles are **generated** -- regenerate after editing the blob list:

```bash
python3 scripts/gen-vendor-mk.py         # writes Android.mk + blobs.mk
python3 scripts/gen-vendor-mk.py --check # CI gate: fail if stale
```

### 4. Build (full AOSP host required -- not CI)

```bash
source build/envsetup.sh
lunch u2-userdebug
scripts/build.sh            # wrapper; see scripts/build.sh --help
```

### 5. Measure on hardware

```bash
scripts/measure.sh          # collects R1-R15 over adb from a booted device
```

---

## Firmware build & release (SP_Flash_Tool)

The flashable package is a **hybrid repack**: the MTK-private partitions
(`preloader`, `lk`, `logo`, `secro`, `trustzone`) and the **official scatter** are
reused from the baseline firmware (range-fetched, ~1 MB -- never the 2.46 GiB
stock `system`), while `system.img`, `boot.img` and `recovery.img` come from this
repo's AOSP 8.1 build.  The result is validated against the official scatter and
zipped so SP_Flash_Tool can load it directly.

```bash
# after an AOSP build has produced out/target/product/u2/{system,boot,recovery}.img
python3 scripts/repack_firmware.py --baseline-url "<official zip url>" \
        --build-out out/target/product/u2 --out U2_AOSP81.zip --pkg-name U2_AOSP81
python3 scripts/publish_release.py --tag u2-aosp81-<ver> --files U2_AOSP81.zip
```

The AOSP **compile** is the only step that needs a big machine, so it lives in the
gated [`build.yml`](.github/workflows/build.yml) (`workflow_dispatch`, runs on a
self-hosted / larger runner) and publishes the Release automatically.  Full
details, the real partition table, and SP_Flash_Tool flashing steps are in
[`docs/FIRMWARE_BUILD.md`](docs/FIRMWARE_BUILD.md).

---

## Continuous integration

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs two jobs on every
push / PR to `main`:

- **Lint & unit tests** (offline) -- `scripts/validate_repo.py` (XML/YAML
  well-formed, `bash -n`, Python compile, patch/SEPolicy/fstab format, CRLF,
  conflict markers), `gen-vendor-mk.py --check` (no drift), the Python unit-test
  suite (`scripts/tests/`, 89 tests), `shellcheck`, `xmllint`, and CLI smokes.
- **Verify patches & package firmware** (network) -- `apply-patches.sh --verify`
  (all series `git am` onto fresh `android-8.1.0_r80` clones) **plus** a
  `repack_firmware.py --dry-run` that range-fetches the *real* baseline scatter +
  MTK-private partitions, substitutes placeholder built images, and validates the
  package with `mtk_scatter.py`.  This proves the SP_Flash_Tool packaging path on
  every push without needing a build host.

[`.github/workflows/build.yml`](.github/workflows/build.yml) is manual-only and
does the real AOSP build -> repack -> Release publish on a capable runner.

Run the offline checks locally with:

```bash
python3 scripts/validate_repo.py
python3 -m unittest discover -s scripts/tests -p 'test_*.py'
```

---

## Key engineering decisions

Recorded in full in the plan; the load-bearing ones:

- **D2 -- 32-bit-only userspace.**  Drops `lib64`/`vendor/lib64` (~165 MiB) and the
  second zygote, saving 60-100 MiB RAM (serves R1/R3).
- **D3 -- reuse the 3.18.35 kernel + appended DTB.**  No custom kernel build; the
  baseline DTB already carries the panel/i2s/msdc/regulator nodes.  Only config
  additions when AOSP 8.1 needs them ([`kernel/`](kernel/)).
- **D5 -- dm-verity + FDE off.**  The 7.0 FDE structures are incompatible with 8.1,
  so `/data` must be wiped on flash; verity is disabled for a self-built image.
- **D6 -- AOSP Bluetooth stack, not MTK's.**  Enables the A2DP **sink** AAC codec
  and SBC-XQ via source patches; the MTK `MtkBluetooth.apk` is replaced by the
  AOSP `Bluetooth` app driven by a resource overlay.

The AAC **sink** codec does not exist in AOSP 8.1 (only source-side AAC), so it is
backported from Android 9 with the oreo<->pie interface adaptation; SBC-XQ is
enabled by raising the sink bitpool ceiling.  Details and the per-patch mapping to
plan sections are in [`patches/README.md`](patches/README.md).  Full multi-device
(F6) architecture is specified in
[`patches/packages_apps_Bluetooth/MULTIDEVICE.md`](patches/packages_apps_Bluetooth/MULTIDEVICE.md).

## License

See [`LICENSE`](LICENSE).  Proprietary MediaTek blobs remain under their original
licensing and are not redistributed here.
