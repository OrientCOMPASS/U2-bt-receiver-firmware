#!/usr/bin/env bash
# bt_standalone_build.sh -- compile the patched AOSP 8.1 Bluetooth modules
# OUTSIDE a full AOSP tree ("poor man's sysroot").
#
# Why this exists
#   A full AOSP 8.1 build needs ~300 GB and many cores.  Our patches touch only
#   three modules (bluetooth.default / libbluetooth_jni / Bluetooth.apk).  This
#   script proves, on any 2-core/4 GB CI runner, that the patched sources
#   actually COMPILE for ARM/bionic, by assembling a minimal build environment:
#
#     * toolchain : Android NDK r25c (clang 14 "based on r450784" -- the same
#                   clang generation AOSP 8.1 shipped), target armv7a API 26
#     * headers   : blobless+sparse clones of the exact android-8.1.0_r80 trees
#                   the real build uses (libchrome, libcutils/libutils/liblog/
#                   libsystem/base, binder, libaudioclient/libmedia, system/
#                   audio, tinyxml2, googletest's gtest_prod.h, aac, ldac)
#     * sources   : system/bt (+ our patches applied with git am), external/aac
#                   and external/libldac -- with the SAME source lists as their
#                   Android.bp files (globs mirrored exactly)
#     * link libs : --link produces real ELF artifacts.  Platform-only shared
#                   libs (libchrome, libcutils, libaudioclient, ...) are left as
#                   undefined symbols, exactly like the real -shared link does;
#                   they resolve at runtime against the device's libraries.
#
# What this proves / does not prove
#   PROVES      : every patched TU compiles; module .so/.a artifacts link.
#   DOES NOT    : make the artifacts deployable onto a foreign base image --
#                 that still requires matching platform libs (see
#                 docs/INCREMENTAL_BUILD.md and docs/NOUGAT_BT_SWAP.md).
#
# Documented exclusions (mirroring what the real build does NOT put in the
# modules, or what needs build-time codegen):
#   * hci/src/hci_layer_android.cc -- needs hidl-gen-generated HIDL headers
#     (android.hardware.bluetooth@1.0); transport glue, untouched by patches.
#   * osi/src/metrics.cc -- needs protoc-generated bluetooth.pb.h; metrics
#     logging, untouched by patches.
#   * test/, tools/, vendor_libs/, service/, vnd/ subtrees -- not part of
#     bluetooth.default / audio.a2dp.default.
#   * aac arm//mips/ asm subdirs and non-globbed ldac sources -- the Android.bp
#     globs are top-level "lib*/src/*.cpp" and a 2-file ldac source list.
#
# Usage:
#   scripts/bt_standalone_build.sh --workdir DIR [options]
#
# Options:
#   --workdir DIR    where sources/NDK/objects live (cached between runs)
#   --tag TAG        AOSP tag to fetch (default android-8.1.0_r80)
#   --ndk-url URL    NDK zip (default r25c linux-x86_64 from dl.google.com)
#   --jobs N         parallel compiles (default: nproc)
#   --smoke          compile only the 14-TU smoke set (fast sanity check)
#   --link           after compiling, link bluetooth.default.so,
#                    audio.a2dp.default.so, libldacBT_*.so, libFraunhoferAAC.a
#   --skip-setup     reuse workdir contents as-is (no clone/download)
#   -h, --help       this text
#
# Exit codes: 0 all compiled (and linked); 1 any TU failed; 2 usage/setup error.
set -euo pipefail

TAG="android-8.1.0_r80"
TREE=""                       # 8.1 (default) | nougat  (auto from android-7.* tag)
NDK_URL="https://dl.google.com/android/repository/android-ndk-r25c-linux.zip"
WORKDIR=""
JOBS="$(nproc 2>/dev/null || echo 2)"
SMOKE=0
LINK=0
SKIP_SETUP=0

die() { echo "bt_standalone_build: $*" >&2; exit 2; }
usage() { sed -n '/^# Usage:/,/^# Exit codes:/p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --workdir)    WORKDIR="${2:-}"; shift 2 ;;
    --tag)        TAG="${2:-}"; shift 2 ;;
    --tree)       TREE="${2:-}"; shift 2 ;;
    --ndk-url)    NDK_URL="${2:-}"; shift 2 ;;
    --jobs)       JOBS="${2:-}"; shift 2 ;;
    --smoke)      SMOKE=1; shift ;;
    --link)       LINK=1; shift ;;
    --skip-setup) SKIP_SETUP=1; shift ;;
    -h|--help)    usage; exit 0 ;;
    *)            die "unknown option: $1 (see --help)" ;;
  esac
done

[ -n "$WORKDIR" ] || die "--workdir is required"
if [ -z "$TREE" ]; then
  case "$TAG" in
    android-7.*) TREE="nougat" ;;
    *)           TREE="8.1" ;;
  esac
fi
case "$TREE" in 8.1|nougat) ;; *) die "--tree must be 8.1 or nougat (got: $TREE)" ;; esac
echo "[cfg] tree=$TREE tag=$TAG"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
if [ "$TREE" = nougat ]; then
  PATCH_DIR="$REPO_ROOT/patches/nougat"
  BDCFG_DIR="$REPO_ROOT/device/keenplus/u2-n"
else
  PATCH_DIR="$REPO_ROOT/patches"
  BDCFG_DIR="$REPO_ROOT/device/keenplus/u2"
  [ -d "$PATCH_DIR/system_bt" ] || die "patches not found at $PATCH_DIR"
  [ -f "$BDCFG_DIR/bdroid_buildcfg.h" ] || die "bdroid_buildcfg.h not found in $BDCFG_DIR"
fi

SRC="$WORKDIR/src"
OBJ="$WORKDIR/obj"
OUTDIR="$WORKDIR/out"
LOGS="$WORKDIR/logs"
mkdir -p "$SRC" "$OBJ" "$OUTDIR" "$LOGS"

# ---------------------------------------------------------------------------
# Stage 1: NDK toolchain (selective extraction; ~1 GB on disk)
# ---------------------------------------------------------------------------
setup_ndk() {
  local zip="$WORKDIR/ndk.zip" prefix ndkroot
  # find any already-extracted NDK toolchain dir
  ndkroot="$(find "$WORKDIR" -maxdepth 5 -type d -path '*/toolchains/llvm/prebuilt/linux-x86_64' 2>/dev/null | head -1 || true)"
  if [ -n "$ndkroot" ] && [ -x "$ndkroot/bin/clang++" ] && [ -f "$ndkroot/lib64/libc++.so.1" ]; then
    NDK="$ndkroot"; echo "[ndk] cached: $NDK"; return 0
  fi
  echo "[ndk] downloading $NDK_URL (~530 MB)"
  curl -sS -L --retry 3 -o "$zip" "$NDK_URL" || die "NDK download failed"
  prefix="$(unzip -Z1 "$zip" | head -1 | cut -d/ -f1)"
  [ -n "$prefix" ] || die "cannot determine NDK top-level dir"
  local P="$prefix/toolchains/llvm/prebuilt/linux-x86_64"
  echo "[ndk] extracting toolchain (this keeps ~1 GB and drops the rest)"
  unzip -q -o -d "$WORKDIR" "$zip" \
    "$P/bin/*" "$P/lib64/clang/*" "$P/lib64/*.so*" \
    "$P/sysroot/usr/include/*" \
    "$P/sysroot/usr/lib/arm-linux-androideabi/*" || die "NDK extraction failed"
  rm -f "$zip"
  NDK="$WORKDIR/$P"
  [ -x "$NDK/bin/clang++" ] || die "clang++ missing after extraction"
  echo "[ndk] $("$NDK/bin/clang++" --version | head -1)"
}

# ---------------------------------------------------------------------------
# Stage 2: AOSP sources at $TAG (blobless + sparse where possible)
# ---------------------------------------------------------------------------
AOSP="https://android.googlesource.com/platform"

clone_full() { # name url
  if [ ! -d "$SRC/$1" ]; then
    echo "[src] clone $1 (full)"
    git clone -q --depth 1 --branch "$TAG" "$2" "$SRC/$1"
  fi
}
clone_sparse() { # name url dir...
  local n="$1" u="$2"; shift 2
  if [ ! -d "$SRC/$n" ]; then
    echo "[src] clone $n (sparse: $*)"
    git clone -q --depth 1 --branch "$TAG" --filter=blob:none --sparse "$u" "$SRC/$n"
    git -C "$SRC/$n" sparse-checkout set "$@"
  fi
}

setup_sources() {
  clone_full    bt        "$AOSP/system/bt"
  clone_sparse  libhardware "$AOSP/hardware/libhardware" include
  clone_full    tinyxml2  "$AOSP/external/tinyxml2"
  # external/libldac exists only from 8.0 on; LDAC on the nougat tree goes
  # through the (later) libldacdec+bco-shim route, see DEVELOPMENT_PLAN 16.8.
  [ "$TREE" = nougat ] || clone_full libldac "$AOSP/external/libldac"
  clone_full    aac       "$AOSP/external/aac"
  clone_sparse  core      "$AOSP/system/core" \
      libcutils/include libutils/include liblog/include libsystem/include base/include
  clone_sparse  native    "$AOSP/frameworks/native" \
      include libs/binder/include libs/ui/include libs/gui/include libs/nativewindow/include
  clone_sparse  av        "$AOSP/frameworks/av" \
      media/libaudioclient/include media/libmedia/include media/libaudiohal/include include
  clone_sparse  sysmedia  "$AOSP/system/media" audio/include audio_utils/include
  if [ ! -d "$SRC/gtest" ]; then
    # external/googletest carries NO android-7.* tags at all; we only need
    # gtest/gtest_prod.h (FRIEND_TEST macro) -- version-stable, so pin the
    # 8.1 tag when building the nougat tree.
    local gtag="$TAG"
    [ "$TREE" = nougat ] && gtag="android-8.1.0_r80"
    echo "[src] clone gtest (sparse: googletest/include @ $gtag)"
    git clone -q --depth 1 --branch "$gtag" --filter=blob:none --sparse \
      "$AOSP/external/googletest" "$SRC/gtest"
    git -C "$SRC/gtest" sparse-checkout set googletest/include
  fi
  if [ ! -d "$SRC/libchrome" ]; then
    echo "[src] clone libchrome (headers only)"
    git clone -q --depth 1 --branch "$TAG" --filter=blob:none --no-checkout \
      "$AOSP/external/libchrome" "$SRC/libchrome"
    git -C "$SRC/libchrome" sparse-checkout init --no-cone
    printf '*.h\n' > "$SRC/libchrome/.git/info/sparse-checkout"
    git -C "$SRC/libchrome" checkout -q -f HEAD
  fi
}

apply_patches() {
  if [ "$TREE" = nougat ]; then
    if ls "$PATCH_DIR/system_bt"/*.patch >/dev/null 2>&1; then
      git -C "$SRC/bt" config user.name  "OrientCOMPASS"
      git -C "$SRC/bt" config user.email "316423573+OrientCOMPASS@users.noreply.github.com"
      local p n=0
      for p in "$PATCH_DIR/system_bt"/*.patch; do
        git -C "$SRC/bt" am "$p" >/dev/null || die "git am failed: $p"
        n=$((n+1))
      done
      echo "[patch] bt(nougat): applied $n patch(es) from $PATCH_DIR/system_bt"
    else
      echo "[patch] bt(nougat): no series yet -- PRISTINE tree gate"
    fi
    return 0
  fi
  # Deterministic "already applied?" markers: files/symbols our series adds.
  if [ -f "$SRC/bt/stack/a2dp/a2dp_aac_decoder.cc" ]; then
    echo "[patch] bt: series already applied (a2dp_aac_decoder.cc present)"
  else
    echo "[patch] applying system_bt series"
    git -C "$SRC/bt" config user.name  >/dev/null 2>&1 || \
      git -C "$SRC/bt" config user.name  "OrientCOMPASS"
    git -C "$SRC/bt" config user.email >/dev/null 2>&1 || \
      git -C "$SRC/bt" config user.email "316423573+OrientCOMPASS@users.noreply.github.com"
    local p n=0
    for p in "$PATCH_DIR/system_bt"/*.patch; do
      git -C "$SRC/bt" am "$p" >/dev/null || die "git am failed: $p"
      n=$((n+1))
    done
    echo "[patch] bt: applied $n patch(es)"
  fi
  if grep -q 'BTAV_A2DP_CODEC_INDEX_SINK_AAC' "$SRC/libhardware/include/hardware/bt_av.h" 2>/dev/null; then
    echo "[patch] libhardware: series already applied"
  else
    echo "[patch] applying hardware_libhardware series"
    git -C "$SRC/libhardware" config user.name  >/dev/null 2>&1 || \
      git -C "$SRC/libhardware" config user.name  "OrientCOMPASS"
    git -C "$SRC/libhardware" config user.email >/dev/null 2>&1 || \
      git -C "$SRC/libhardware" config user.email "316423573+OrientCOMPASS@users.noreply.github.com"
    local p n=0
    for p in "$PATCH_DIR/hardware_libhardware"/*.patch; do
      git -C "$SRC/libhardware" am "$p" >/dev/null || die "git am failed: $p"
      n=$((n+1))
    done
    echo "[patch] libhardware: applied $n patch(es)"
  fi
}

# Sentinel headers/files that MUST exist for the compile environment to be
# complete.  Different git versions have shown subtle sparse-checkout
# differences (a top-level "include" cone once failed to materialize on the
# CI runners), so verify and self-heal instead of failing deep inside clang
# with a confusing "file not found".
SENTINELS_NOUGAT=(
  "bt/stack/a2dp/a2dp_api.c"
  "bt/stack/avdt/avdt_defs.h"
  "bt/btif/co/bta_av_co.c"
  "bt/main/bte_main.cc"
  "bt/gki/ulinux/gki_ulinux.c"
  "bt/embdrv/sbc/decoder/include/oi_codec_sbc.h"
  "libhardware/include/hardware/bluetooth.h"
  "libchrome/base/logging.h"
  "gtest/googletest/include/gtest/gtest_prod.h"
  "core/libcutils/include/cutils/properties.h"
  "core/libutils/include/utils/RefBase.h"
  "core/liblog/include/log/log.h"
  "native/libs/binder/include/binder/Parcel.h"
  "av/media/libaudioclient/include/media/AudioTrack.h"
  "sysmedia/audio/include/system/audio.h"
  "tinyxml2/tinyxml2.h"
  "aac/libAACdec/include/aacdecoder_lib.h"
)

SENTINELS=(
  "bt/stack/a2dp/a2dp_aac_decoder.cc"
  "libhardware/include/hardware/bt_av.h"
  "libchrome/base/logging.h"
  "gtest/googletest/include/gtest/gtest_prod.h"
  "core/libcutils/include/cutils/properties.h"
  "core/libutils/include/utils/RefBase.h"
  "core/liblog/include/log/log.h"
  "core/libsystem/include/system/graphics.h"
  "core/base/include/android-base/unique_fd.h"
  "native/libs/binder/include/binder/Parcel.h"
  "av/media/libaudioclient/include/media/AudioTrack.h"
  "av/include/media/AudioResamplerPublic.h"
  "sysmedia/audio/include/system/audio.h"
  "tinyxml2/tinyxml2.h"
  "aac/libAACdec/include/aacdecoder_lib.h"
  "libldac/inc/ldacBT.h"
)

verify_headers() {
  local s repo missing=0 arrname=SENTINELS
  [ "$TREE" = nougat ] && arrname=SENTINELS_NOUGAT
  local -n sentinels="$arrname"
  for s in "${sentinels[@]}"; do
    if [ ! -f "$SRC/$s" ]; then
      repo="${s%%/*}"
      echo "[verify] MISSING $s -- healing $repo with a full checkout"
      git -C "$SRC/$repo" sparse-checkout disable >/dev/null 2>&1 || true
      git -C "$SRC/$repo" checkout -f HEAD >/dev/null 2>&1 || true
      if [ ! -f "$SRC/$s" ]; then
        echo "[verify] still missing after heal:" >&2
        ls "$SRC/$repo" >&2 || true
        git -C "$SRC/$repo" sparse-checkout list >&2 || true
        die "environment verification failed for $s"
      fi
      missing=$((missing+1))
    fi
  done
  if [ "$TREE" = nougat ]; then
    echo "[verify] all ${#SENTINELS_NOUGAT[@]} nougat sentinels present ($missing healed)"
  elif grep -q 'BTAV_A2DP_CODEC_INDEX_SINK_AAC' "$SRC/libhardware/include/hardware/bt_av.h"; then
    echo "[verify] all ${#SENTINELS[@]} sentinels present ($missing healed); patches in place"
  else
    die "libhardware patch marker missing (bt_av.h)"
  fi
}

# ---------------------------------------------------------------------------
# Stage 3: compile environment (mirrors fluoride_defaults + module include_dirs)
# ---------------------------------------------------------------------------
write_env() {
  local bd_def="-DHAS_BDROID_BUILDCFG"
  if [ "$TREE" = nougat ] && [ ! -f "$BDCFG_DIR/bdroid_buildcfg.h" ]; then
    bd_def=""   # pristine gate: pure AOSP N defaults from bt_target.h
    echo "[env] nougat pristine: no bdroid_buildcfg (AOSP defaults)"
  fi
  cat > "$WORKDIR/env.sh" <<ENVEOF
HERE="$WORKDIR"
SRC="$SRC"
BT="$SRC/bt"
NDK="$NDK"
BDCFG="$BDCFG_DIR"
CCXX="\$NDK/bin/clang++"
CC="\$NDK/bin/clang"
AR="\$NDK/bin/llvm-ar"
READELF="\$NDK/bin/llvm-readelf"
TARGET=(--target=armv7a-linux-androideabi26)
SYSROOT=(--sysroot="\$NDK/sysroot")
BT_INC=(
  -I"\$BT" -I"\$BT/include" -I"\$BT/stack/include"
  -I"\$BT/stack/avct" -I"\$BT/stack/btm" -I"\$BT/stack/avrc" -I"\$BT/stack/l2cap"
  -I"\$BT/stack/avdt" -I"\$BT/stack/gatt" -I"\$BT/stack/gap" -I"\$BT/stack/pan"
  -I"\$BT/stack/bnep" -I"\$BT/stack/hid" -I"\$BT/stack/sdp" -I"\$BT/stack/smp"
  -I"\$BT/stack/srvc" -I"\$BT/stack/btu" -I"\$BT/stack/hcic" -I"\$BT/stack/a2dp"
  -I"\$BT/btif/include" -I"\$BT/btif/co"
  -I"\$BT/bta/include" -I"\$BT/bta/sys" -I"\$BT/bta/dm"
  -I"\$BT/bta/ar" -I"\$BT/bta/av" -I"\$BT/bta/gatt" -I"\$BT/bta/hh"
  -I"\$BT/bta/hl" -I"\$BT/bta/jv"
  -I"\$BT/btcore/include" -I"\$BT/hci/include" -I"\$BT/udrv/include"
  -I"\$BT/utils/include" -I"\$BT/vnd/include" -I"\$BT/vnd/ble"
  -I"\$BT/types" -I"\$BT/device/include"
  -I"\$BT/embdrv/sbc/encoder/include" -I"\$BT/embdrv/sbc/decoder/include"
  -I"\$BT/audio_a2dp_hw/include"
  -I"\$BDCFG"
  -I"\$SRC/libchrome" -I"\$SRC/gtest/googletest/include"
  -I"\$SRC/libhardware/include"
  -I"\$SRC/core/libcutils/include" -I"\$SRC/core/libutils/include"
  -I"\$SRC/core/liblog/include" -I"\$SRC/core/libsystem/include"
  -I"\$SRC/core/base/include"
  -I"\$SRC/native/libs/binder/include" -I"\$SRC/native/libs/ui/include"
  -I"\$SRC/native/libs/gui/include" -I"\$SRC/native/libs/nativewindow/include"
  -I"\$SRC/native/include"
  -I"\$SRC/av/media/libaudioclient/include" -I"\$SRC/av/media/libmedia/include"
  -I"\$SRC/av/media/libaudiohal/include" -I"\$SRC/av/include"
  -I"\$SRC/sysmedia/audio/include" -I"\$SRC/sysmedia/audio_utils/include"
  -I"\$SRC/tinyxml2"
  -I"\$SRC/aac/libAACdec/include" -I"\$SRC/aac/libAACenc/include"
  -I"\$SRC/aac/libSYS/include"
  -I"\$SRC/libldac/inc" -I"\$SRC/libldac/abr/inc"
)
AAC_INC=(
  -I"\$SRC/aac/libAACdec/include" -I"\$SRC/aac/libAACenc/include"
  -I"\$SRC/aac/libSYS/include" -I"\$SRC/aac/libFDK/include"
  -I"\$SRC/aac/libMpegTPDec/include" -I"\$SRC/aac/libMpegTPEnc/include"
  -I"\$SRC/aac/libPCMutils/include" -I"\$SRC/aac/libSBRdec/include"
  -I"\$SRC/aac/libSBRenc/include"
  -I"\$SRC/aac/libFDK/src" -I"\$SRC/aac/libSYS/src"
  -I"\$SRC/core/liblog/include" -I"\$SRC/core/libsystem/include"
)
LDAC_INC=( -I"\$SRC/libldac/inc" -I"\$SRC/libldac/abr/inc" )
DEFS=(
  -DEXPORT_SYMBOL=__attribute__\\(\\(visibility\\(\\"default\\"\\)\\)\\)
  -DLOG_NDEBUG=1 -DBUILDCFG $bd_def -DNDEBUG
)
CXXFLAGS=( "\${TARGET[@]}" "\${SYSROOT[@]}" -std=gnu++14 -c -O0 -fPIC -w -fno-strict-aliasing -fvisibility=hidden "\${DEFS[@]}" )
CFLAGS=(   "\${TARGET[@]}" "\${SYSROOT[@]}" -std=c99     -c -O0 -fPIC -w -fno-strict-aliasing -fvisibility=hidden -fcommon "\${DEFS[@]}" )
ENVEOF
  cat > "$WORKDIR/compile_one.sh" <<'ONEEOF'
#!/usr/bin/env bash
set -u
# shellcheck source=/dev/null
source "$1/env.sh"
WD="$1"; ROOTKEY="$2"; REL="$3"
OBJ="$WD/obj"
case "$ROOTKEY" in
  bt)   ABS="$BT/$REL";        INC=("${BT_INC[@]}") ;;
  aac)  ABS="$SRC/aac/$REL";   INC=("${AAC_INC[@]}") ;;
  ldac) ABS="$SRC/libldac/$REL"; INC=("${LDAC_INC[@]}") ;;
  *)    echo "FAIL $ROOTKEY/$REL (unknown root)"; exit 0 ;;
esac
O="$OBJ/${ROOTKEY}_$(echo "$REL" | tr '/' '_').o"
ERR="$OBJ/${ROOTKEY}_$(echo "$REL" | tr '/' '_').err"
case "$ABS" in
  *.cc|*.cpp) CMD=("$CCXX" "${CXXFLAGS[@]}") ;;
  *)          CMD=("$CC"  "${CFLAGS[@]}") ;;
esac
if "${CMD[@]}" "${INC[@]}" "$ABS" -o "$O" 2>"$ERR"; then
  rm -f "$ERR"; echo "PASS $ROOTKEY/$REL"
else
  echo "FAIL $ROOTKEY/$REL"
fi
ONEEOF
  chmod +x "$WORKDIR/compile_one.sh"
}

build_tu_list() {
  local LIST="$WORKDIR/tu_list.txt"
  : > "$LIST"
  if [ "$SMOKE" = 1 ]; then
    cat >> "$LIST" <<'SMOKEOF'
bt stack/a2dp/a2dp_aac.cc
bt stack/a2dp/a2dp_aac_decoder.cc
bt stack/a2dp/a2dp_codec_config.cc
bt stack/a2dp/a2dp_sbc.cc
bt stack/a2dp/a2dp_aac_encoder.cc
bt btif/src/btif_a2dp_sink.cc
bt btif/src/btif_av.cc
bt main/bte_main.cc
bt main/bte_init.cc
bt osi/src/alarm.cc
bt stack/btm/btm_sec.cc
bt hci/src/hci_layer.cc
bt bta/av/bta_av_aact.cc
bt audio_a2dp_hw/src/audio_a2dp_hw.cc
SMOKEOF
    return 0
  fi
  # system/bt -- everything that lands in bluetooth.default / audio.a2dp.default
  (cd "$SRC/bt" && find main hci btif stack bta btcore device osi types utils \
       audio_a2dp_hw udrv embdrv gki \
       \( -name '*.cc' -o -name '*.c' \) 2>/dev/null) \
    | grep -vE '(^|/)(test|tests)/|_test\.cc$|_unittest\.cc$|_test_support\.cc$|hci_layer_android\.cc$|^osi/src/metrics\.cc$' \
    | sed 's#^#bt #' >> "$LIST"
  # external/aac -- Android.bp globs top-level "lib*/src/*.cpp" only
  local l files
  for l in libAACdec libAACenc libPCMutils libFDK libSYS libMpegTPDec libMpegTPEnc libSBRdec libSBRenc; do
    if files="$(cd "$SRC/aac" && ls "$l"/src/*.cpp 2>/dev/null)"; then
      printf '%s\n' "$files" | sed 's#^#aac #' >> "$LIST"
    fi
  done
  # external/libldac -- Android.bp: 2-file enc source list + 1-file abr (8.x only)
  if [ -d "$SRC/libldac" ]; then
    (cd "$SRC/libldac" && ls src/ldaclib.c src/ldacBT.c abr/src/ldacBT_abr.c 2>/dev/null) \
      | sed 's#^#ldac #' >> "$LIST"
  fi
  sort -o "$LIST" "$LIST"
}

compile_all() {
  local LIST="$WORKDIR/tu_list.txt" TOTAL P F
  TOTAL="$(wc -l < "$LIST")"
  echo "[build] compiling $TOTAL translation units (-j$JOBS)"
  # feed "<rootkey>\n<rel>" pairs to xargs -> compile_one.sh <wd> <root> <rel>
  awk '{print $1"\n"$2}' "$LIST" \
    | xargs -P"$JOBS" -n2 "$WORKDIR/compile_one.sh" "$WORKDIR" \
      > "$LOGS/results.log" 2>&1 || true
  P="$(grep -c '^PASS' "$LOGS/results.log" || true)"
  F="$(grep -c '^FAIL' "$LOGS/results.log" || true)"
  echo "[build] result: $P/$TOTAL passed, $F failed"
  if [ "$F" != 0 ]; then
    grep '^FAIL' "$LOGS/results.log" | head -30
    echo "[build] first failing TU diagnostics:"
    local -a fails
    mapfile -t fails < <(grep '^FAIL' "$LOGS/results.log" | head -3 | awk '{print $2}')
    local f err
    for f in "${fails[@]}"; do
      err="$OBJ/$(echo "$f" | tr '/' '_').err"
      [ -f "$err" ] && { echo "--- $f"; head -8 "$err"; }
    done
    return 1
  fi
}

# ---------------------------------------------------------------------------
# Stage 4 (--link): produce ELF artifacts.  Platform shared libs stay as
# undefined symbols (normal for -shared); NDK provides libc/libm/liblog/libz/
# libdl and static libc++.
# ---------------------------------------------------------------------------
link_artifacts() {
  # shellcheck source=/dev/null
  source "$WORKDIR/env.sh"
  local -a aac_objs bt_objs a2dp_objs osi_objs
  echo "[link] libFraunhoferAAC.a"
  mapfile -t aac_objs < <(find "$OBJ" -name 'aac_*.o' | sort)
  "$AR" rcs "$OUTDIR/libFraunhoferAAC.a" "${aac_objs[@]}"
  if [ -f "$OBJ/ldac_src_ldaclib.c.o" ]; then
    echo "[link] libldacBT_enc.so + libldacBT_abr.so"
    "$CC" "${TARGET[@]}" "${SYSROOT[@]}" -shared -o "$OUTDIR/libldacBT_enc.so" \
      "$OBJ/ldac_src_ldaclib.c.o" "$OBJ/ldac_src_ldacBT.c.o" -llog || return 1
    "$CC" "${TARGET[@]}" "${SYSROOT[@]}" -shared -o "$OUTDIR/libldacBT_abr.so" \
      "$OBJ/ldac_abr_src_ldacBT_abr.c.o" -llog || return 1
  else
    echo "[link] libldacBT_*: no objects (nougat pristine tree) -- skipped"
  fi
  echo "[link] bluetooth.default.so (all stack objects; undefined platform symbols allowed)"
  mapfile -t bt_objs < <(find "$OBJ" -name 'bt_*.o' ! -name 'bt_audio_a2dp_hw_*' | sort)
  "$CCXX" "${TARGET[@]}" "${SYSROOT[@]}" -shared -static-libstdc++ \
    -o "$OUTDIR/bluetooth.default.so" "${bt_objs[@]}" "${aac_objs[@]}" \
    -Wl,-Bsymbolic -llog -lz -ldl || return 1
  echo "[link] audio.a2dp.default.so"
  mapfile -t a2dp_objs < <(find "$OBJ" -name 'bt_audio_a2dp_hw_src_*.o' | sort)
  mapfile -t osi_objs < <(find "$OBJ" -name 'bt_osi_*.o' | sort)
  "$CCXX" "${TARGET[@]}" "${SYSROOT[@]}" -shared -static-libstdc++ \
    -o "$OUTDIR/audio.a2dp.default.so" "${a2dp_objs[@]}" "${osi_objs[@]}" \
    -Wl,-Bsymbolic -llog || return 1
  echo "[link] artifacts:"
  ls -la "$OUTDIR"
  local f und
  for f in "$OUTDIR"/*.so; do
    und="$("$READELF" --dyn-syms "$f" 2>/dev/null | grep -c ' UND ' || true)"
    echo "  $(basename "$f"): DT_NEEDED=[$("$READELF" -d "$f" | grep NEEDED | awk '{print $NF}' | tr -d '[]' | tr '\n' ' ')] undefined_syms=$und"
  done
}

# ---------------------------------------------------------------------------
NDK=""
if [ "$SKIP_SETUP" = 0 ]; then
  setup_ndk
  setup_sources
  apply_patches
else
  NDK="$(find "$WORKDIR" -maxdepth 5 -type d -path '*/toolchains/llvm/prebuilt/linux-x86_64' | head -1)"
  [ -n "$NDK" ] || die "--skip-setup but no NDK found under $WORKDIR"
fi
verify_headers
write_env
build_tu_list
if compile_all && { [ "$LINK" = 0 ] || link_artifacts; }; then
  echo "[done] OK -- objects in $OBJ, artifacts in $OUTDIR"
else
  echo "[done] FAILED -- see $LOGS/results.log and $OBJ/*.err" >&2
  exit 1
fi
