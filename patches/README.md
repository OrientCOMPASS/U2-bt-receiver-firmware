# AOSP source patches — KeenPlus U2 (AOSP 8.1 / `android-8.1.0_r80`)

These are **real `git format-patch` series** generated against, and verified to
apply cleanly to, the exact AOSP tag `android-8.1.0_r80`.  They are organised by
AOSP git repository.  Everything that is *not* an AOSP source change (device
tree, product config, ramdisk, trimming) lives under `device/keenplus/u2/` and
`vendor/keenplus/u2/` and needs no patching.

CI re-clones each repository at `android-8.1.0_r80` and runs `git am` on every
series, so a green build guarantees these still apply to the pristine tag.

## Layout

```
patches/
├── hardware_libhardware/     # applied to  platform/hardware/libhardware
│   └── 0001-bt_av-Add-BTAV_A2DP_CODEC_INDEX_SINK_AAC.patch
├── system_bt/                # applied to  platform/system/bt
│   ├── 0001-a2dp-aac-Add-A2DP-AAC-sink-codec-support.patch
│   ├── 0002-a2dp-aac-Backport-the-AAC-sink-decoder-from-pie.patch
│   ├── 0003-btif-a2dp_sink-Dispatch-SBC-AAC-decoding-by-codec-type.patch
│   ├── 0004-a2dp-sbc-Raise-the-sink-bitpool-ceiling-for-SBC-XQ.patch
│   └── 0005-a2dp-vendor-Handle-SINK_AAC-in-the-vendor-codec-swit.patch
├── prebuilts_sdk/            # applied to  platform/prebuilts/sdk
│   └── 0001-jack-Run-jack-admin-with-the-prebuilt-JDK8.patch
└── packages_apps_Bluetooth/  # applied to  platform/packages/apps/Bluetooth
    ├── 0001-a2dpsink-Bind-each-StreamHandler-to-its-own-BluetoothDevice.patch
    └── MULTIDEVICE.md        # design guide for the full F6 rewrite
```

`prebuilts_sdk` is verified in CI in *lite* mode: the repository is multiple
gigabytes, so `scripts/apply-patches.sh --verify` fetches only the files the
series touches (googlesource `?format=TEXT`) into a mini repo and `git am`s
them there — the same pristine-context guarantee, without the clone.

`0001-jack-...` fixes `setup-jack-server` on hosts whose system OpenJDK is
8u261+ (TLS policy that the Ubuntu 16.04 curl/GnuTLS client cannot handshake
with): the rule runs `jack-admin` with the tree's own `prebuilts/jdk/jdk8`
(8u152-android, the official 8.1 toolchain).  Found by the on-hardware smoke
build; A/B-verified live (8u292 server: handshake fail; 8u152 server: HTTP
response over mutual-TLS).

## How to apply

From the root of an initialised AOSP 8.1 tree (`repo init -b android-8.1.0_r80`
+ `repo sync`), or use `scripts/apply-patches.sh` which does the checkout for
you:

```bash
# hardware/libhardware
( cd hardware/libhardware && git am /path/to/patches/hardware_libhardware/*.patch )

# system/bt   (the four patches are an ordered series)
( cd system/bt && git am /path/to/patches/system_bt/*.patch )

# packages/apps/Bluetooth
( cd packages/apps/Bluetooth && git am /path/to/patches/packages_apps_Bluetooth/0001-*.patch )
```

`git am` preserves authorship and commit messages.  To drop a change:
`git rebase -i` or `git revert <sha>`.  Every patch is independent enough to
revert on its own (see the mapping below for the intended build order).

## Mapping to the plan's patch set (docs/DEVELOPMENT_PLAN.md §23)

| Plan patch | Where it lives | Notes |
|---|---|---|
| `01-boot-chain` | `device/keenplus/u2/rootdir/` (`fstab.mt6735`, `init.zygote32.rc`, `enableswap.sh`) | Not an AOSP source patch — device-tree files. verity/forceencrypt removed, zygote32-only, zram enabled (D5, §14.3). |
| `02-bluetooth-sink` | `device/keenplus/u2/bdroid_buildcfg.h` + `overlay/packages/apps/Bluetooth/res/values/config.xml` | Device-tree config; enables A2DP Sink + AVRCP CT, disables unused profiles (§16.1–16.2). |
| `03-aac-sink` | `patches/hardware_libhardware/0001` + `patches/system_bt/0001..0003` + `device/keenplus/u2/audio/a2dp_audio_policy_configuration.xml` | AAC sink decoder backport + codec registration + btif dispatch + 44100\|48000 audio policy (§16.4). |
| `04-multidevice-sink` | `patches/packages_apps_Bluetooth/0001` + `MULTIDEVICE.md` | Device-binding prerequisite shipped; full per-device rewrite specified in MULTIDEVICE.md (§16.6, F6). |
| `05-sbcxq` | `patches/system_bt/0004` | SBC-XQ dual-channel sink bitpool ceiling (§16.5). |
| `06-source-ldac-aptx` | `device/keenplus/u2/overlay/.../config.xml` (codec priorities) | LDAC/aptX source is already in AOSP 8.1; enabled via priority overlay. aptX needs `libaptX_encoder.so`/`libaptXHD_encoder.so` added to the build (§16.7). |
| `07-trim-system` | `device/keenplus/u2/u2.mk` + `device.mk` | Whitelist-based product config; no telephony/camera/NFC/print/MTK bloat (§15). |
| `08-product` | `device/keenplus/u2/` (Launcher, logo, power) | Productisation (§17). |

## What CI proves vs. what needs hardware

* **CI proves:** every patch applies cleanly to `android-8.1.0_r80`
  (`git am`), the new decoder translation unit passes a host `g++
  -fsyntax-only` check, braces/parens are balanced, and the device-tree scripts
  and configs are well-formed.
* **CI cannot prove:** that the patched stack *compiles* in a full AOSP build or
  that audio actually plays.  Those require a full AOSP 8.1 build (≥600 GB,
  ≥32 GB RAM, JDK 8) and the physical device — see plan sections 11–18 and the
  acceptance gates G0–G7.  The AAC payload framing and rx-queue pacing are the
  tuning points flagged as risks R8/R12/R13.
