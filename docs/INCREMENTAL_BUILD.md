# Feasibility: "compile only our changes and drop them onto a prebuilt AOSP 8.1"

The idea: instead of a full ~100-190 GB AOSP build, compile *only the modules our
patches touch*, then merge those artifacts into an already-built AOSP 8.1
`system.img` -- a "manual incremental build".  This document records the
investigation and the verdict, with the evidence.

## TL;DR

- **Partly yes, but not the way that avoids the big build.**  Incremental rebuild
  of just our modules is real and fast (`m <modules> snod`, ~10-20 min) -- but it
  only works **on top of an existing full build** (the source tree + a populated
  `out/`).  It cuts *per-iteration time*, not the *first-build* disk/compute.
- **It cannot replace the first full build**, for two hard reasons (evidence
  below): (1) there is **no prebuilt bootable AOSP 8.1 base** for this
  non-Treble MT6735M device, and (2) our three modules **cannot be compiled
  outside the full tree + toolchain** because they link platform-internal
  libraries and hidden framework APIs.

We therefore wired the *valid* form of the idea into the pipeline:
`scripts/build.sh --incremental` (rebuild only the BT modules + `snod`) and a
`build_mode` input on `build.yml`, intended to run on a **self-hosted runner that
persists the tree + `out/`** between runs.

> **2026-10-28 experiment update — reason (2) was HALF wrong.**  We built a
> standalone compile environment (NDK r25c clang + blobless/sparse header
> checkouts of 11 AOSP repos, ~90 MB of sources) and compiled the **entire
> patched r80 `system/bt` stack + external/aac + external/libldac for
> ARM/bionic API 26: 451/451 TUs green in ~10 min on a 2-core/1 GB sandbox**,
> and it links into ELF artifacts.  See `scripts/bt_standalone_build.sh` and
> `.github/workflows/bt-compile.yml` (runs on every patch change on a stock
> runner).  What remains true: platform deps are *shared* libraries, so the
> resulting `.so` still needs a **matching 8.1 system image at runtime** —
> standalone compilation gives compile/link **validation**, not a drop-in module
> for a foreign base.  That insight unlocked the deployable variant of this
> whole idea: port the patches to the **7.0 baseline's own stack** and swap the
> modules into the official firmware — plan in `docs/NOUGAT_BT_SWAP.md`.

## What our patches actually compile into

Inspecting the real `android-8.1.0_r80` build files (`system/bt`, `packages/apps/
Bluetooth`) shows our changes land in exactly three installable modules:

| Module | Built from | Our patched files | Key dependencies |
|---|---|---|---|
| `bluetooth.default` (HAL .so) | `system/bt/main/Android.bp` (+ `stack/`, `btif/`, `bta/`) | `a2dp/a2dp_aac.cc`, `a2dp/a2dp_aac_decoder.cc` (new), `a2dp/a2dp_sbc.cc`, `btif/src/btif_a2dp_sink.cc` | **static** `libFraunhoferAAC` (external/aac), `libprotobuf-cpp-lite` (external/protobuf); **shared** `libaudioclient`, `libhardware`, `libchrome`, `libcutils`, `libutils`, `liblog`, `libbase` |
| `libbluetooth_jni` (.so) | `packages/apps/Bluetooth/jni/Android.bp` | (consumes `system/bt/types`, `bt_av.h`) | **`libandroid_runtime`** (framework native runtime), `libchrome`, `libnativehelper`, `libcutils`, `libutils`, `liblog`, `libhardware`; static `libbluetooth-types` |
| `Bluetooth` (APK) | `packages/apps/Bluetooth/Android.mk` | `A2dpSinkStreamHandler.java`, `A2dpSinkStateMachine.java` | **`framework.jar` with hidden/@hide APIs**, `com.android.vcard`, `bluetooth.mapsapi`, `sap-api-java-static`, `services.net`; aapt2 resources; platform signing |

`hardware/libhardware`'s `bt_av.h` (the new `BTAV_A2DP_CODEC_INDEX_SINK_AAC`
enum) is a **header** -- it has no artifact of its own; it changes how the three
modules above compile.

## Why the three modules cannot be compiled "standalone"

> **Correction (2026-10-28):** the claim below holds for producing **deployable**
> modules and for the **APK**, but the native stack CAN be compiled (and
> stub-linked) outside the tree: all its platform dependencies are *shared*
> libraries, so the compiler only needs their **headers** (cheap sparse
> checkouts) and the linker may leave those symbols undefined — exactly like the
> real `-shared` link does.  Proven by `scripts/bt_standalone_build.sh`
> (451/451 TUs, incl. all patched files).  The APK still genuinely needs the
> hidden-API `framework.jar`, and a *runnable* module still needs a matching
> system image.

Every non-trivial dependency above is a **platform-internal** library or the
**hidden-API framework**:

- `libFraunhoferAAC`, `libprotobuf-cpp-lite`, `libchrome`, `libaudioclient`,
  `libandroid_runtime`, `libnativehelper`, `libbase` are built **by the AOSP build
  system** from `external/`, `frameworks/`, `system/`.  They are **not** in the
  NDK (which exposes only the public stable ABI) and are not shipped as standalone
  prebuilts.
- `Bluetooth.apk` calls `@hide` framework methods, so it must compile against a
  platform `framework.jar` (the "system" SDK), not the public `android.jar`.
- To build any of these with Soong/Make you need the **whole tree parsed** (the
  build graph spans every `Android.bp`/`Android.mk`) plus the **prebuilt
  toolchain** (`prebuilts/clang`, JDK 8) plus, for linking, the matching platform
  libraries from `out/`.

Hand-rolling a compile outside the build system would mean reconstructing the
exact include paths, ~a dozen platform link libraries, compile flags, and the
target ABI -- and even then the `.so` must match the base image's library ABI
exactly or it will crash at load.  That is more fragile and more work than running
the build system, so it is not a real shortcut.

**Conclusion:** "compile our part separately" still requires the full tree +
toolchain + `out/`.  There is no few-GB standalone compile.

## Why there is no prebuilt AOSP 8.1 base to drop them onto

- **Generic 8.1 images are Treble/emulator builds.**  A GSI or `aosp_arm` image
  targets Treble-compliant vendor interfaces (or the emulator).  Our device is
  **non-Treble** (`BOARD_VNDK_VERSION` empty) with MTK HALs
  (`hwcomposer.mt6735`, `gralloc.mt6735m`, `audio.primary.mt6735m`, ...), an MTK
  init/fstab, and the MT6735M kernel.  A generic 8.1 `system.img` will not boot
  on it.  (AOSP itself notes GSI stopped carrying workarounds for "Android 8.0/8.1
  devices that are not fully Treblized".)
- **The baseline is Android 7.0.**  Injecting 8.1 `Bluetooth.apk` /
  `libbluetooth_jni.so` / `bluetooth.default.so` into a 7.0 `system.img` fails on
  framework + platform-library ABI/API differences (8.1 modules expect 8.1
  `libaudioclient`, `libandroid_runtime`, `framework.jar`, ...).
- **No one has published a bootable "KeenPlus U2 AOSP 8.1" base** -- that image is
  exactly what this project produces.  So the base itself requires one full build.

## What *is* feasible and useful: incremental on a persisted full build

Once a full build exists (tree + populated `out/`), changing only the Bluetooth
patches is cheap:

```bash
source build/envsetup.sh && lunch u2-userdebug
m -j"$(nproc)" bluetooth.default libbluetooth_jni Bluetooth snod
# -> out/target/product/u2/system.img is regenerated from the staged system dir
```

`snod` ("Systemimage, NO Dependencies") repacks `system.img` from
`out/target/product/u2/system/` without recompiling the rest of the platform, so a
BT-only revision is ~10-20 minutes instead of hours.  `boot.img`/`recovery.img`
are unchanged and reused.  This is the "manual incremental build" the idea
describes -- and it is wired into the repo:

- `scripts/build.sh --incremental` runs exactly the module list above + `snod`
  (it refuses to run unless a prior full build's `out/.../system` exists).
- `build.yml` takes a `build_mode` input (`full` | `incremental`).

**The catch:** incremental needs the tree + `out/` to already exist and persist.
GitHub Actions cache is capped (~10 GB/entry) and cannot hold a 100 GB+ `out/`,
and larger runners are ephemeral.  So the practical model is a **self-hosted
runner with a persistent disk** that keeps the AOSP tree + `out/`:

1. **First run:** `build_mode: full` -- one full build (~1-3 h), persists `out/`.
2. **Later runs:** `build_mode: incremental` -- rebuild only BT + `snod` (~10-20
   min), then `repack_firmware.py` + `publish_release.py`.

This does not lower the *disk floor* (the tree + `out/` still occupy ~100-190 GB
on that runner), but it makes every firmware revision after the first fast and
cheap -- which is the real, achievable win from the incremental idea.

## Verdict

| Sub-idea | Feasible? | Why |
|---|---|---|
| Compile our native modules with **no** AOSP tree (NDK/standalone) — as **validation** | **Yes** (proven 2026-10-28) | Headers-only deps + `-shared` link tolerating undefined platform symbols; `scripts/bt_standalone_build.sh`, 451/451 TUs, runs on a stock CI runner |
| Produce **deployable** 8.1 modules with no AOSP tree | **No** | Runtime still needs matching `libchrome`/`libcutils`/`libaudioclient`/… from the same 8.1 build |
| Compile the **APK** with no AOSP tree | **No** | hidden-API `framework.jar` (workaround for 7.0: extract from baseline — see `NOUGAT_BT_SWAP.md` R3) |
| Drop them onto a **prebuilt generic 8.1** system | **No** | Generic/GSI 8.1 is Treble/emulator; won't boot non-Treble MT6735M |
| Drop **8.1** modules onto the **7.0 baseline** system | **No** | 7.0 vs 8.1 ABI/API mismatch |
| Port patches to **7.0** and swap into the **official firmware** | **Yes — active plan** | Same standalone-compile method against 7.0 sources, link against device-extracted libs; `docs/NOUGAT_BT_SWAP.md` |
| Incremental rebuild **on a persisted full build** (`m … snod`) | **Yes** | Standard AOSP flow; wired as `build.sh --incremental` |
| Avoid the **first** full build entirely | **No** (for an 8.1 base) | A bootable U2 8.1 base can only come from one full build |

So: keep the hybrid-repack pipeline, and use incremental builds to make
*revisions* cheap on a self-hosted runner -- but budget for one full build (big
disk) to create the base.  On-hardware flash/boot verification still needs a
physical U2.
