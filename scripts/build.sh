#!/usr/bin/env bash
# build.sh -- end-to-end build wrapper for the KeenPlus U2 AOSP 8.1 firmware.
#
# THIS DOES NOT RUN IN CI.  A full AOSP 8.1 build needs >=8 cores, >=32 GB RAM,
# >=300 GB SSD with the default shallow sync (>=600 GB for full history) and
# JDK 8 (plan section 11).  This script automates the sequence
# on a properly provisioned build host:
#
#   1. check prerequisites
#   2. repo init + sync android-8.1.0_r80
#   3. apply the U2 patch series (scripts/apply-patches.sh --aosp-root)
#   4. link device/ and vendor/ into the tree
#   5. extract proprietary blobs (device/keenplus/u2/extract-files.sh)
#   6. lunch u2-userdebug && make
#
# Usage:
#   build.sh --workdir <dir> --system-image <baseline-system.img> [--jobs N]
#            [--lunch u2-userdebug|u2-user] [--sync] [--make] [--deep]
#   build.sh --workdir <dir> --incremental    # rebuild BT modules + snod only
#
# Full build:  --sync (re)downloads sources (shallow by default; --deep for full
#              history), --make builds everything.  Without them the script only
#              checks prerequisites and prints the plan (dry run).
# Incremental: --incremental rebuilds ONLY the Bluetooth modules + system.img
#              (m bluetooth.default libbluetooth_jni Bluetooth snod) on top of an
#              existing full build's tree+out/.  See docs/INCREMENTAL_BUILD.md.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/.." && pwd)"
MANIFEST_BRANCH="android-8.1.0_r80"

WORKDIR=""
SYSTEM_IMAGE=""
JOBS="$(nproc 2>/dev/null || echo 4)"
LUNCH="u2-userdebug"
DO_SYNC=0
DO_MAKE=0
DO_INCREMENTAL=0
SHALLOW=1   # --depth=1 repo sync (smaller checkout); pass --deep for full history
# Modules our patches touch; incremental rebuild recompiles just these + system.img.
BT_MODULES=(bluetooth.default libbluetooth_jni Bluetooth)

usage() { sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 2; }
log()  { printf '[build] %s\n' "$*"; }
err()  { printf '[build] ERROR: %s\n' "$*" >&2; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --workdir)       WORKDIR="$2"; shift 2 ;;
        --system-image)  SYSTEM_IMAGE="$2"; shift 2 ;;
        --jobs)          JOBS="$2"; shift 2 ;;
        --lunch)         LUNCH="$2"; shift 2 ;;
        --sync)          DO_SYNC=1; shift ;;
        --make)          DO_MAKE=1; shift ;;
        --incremental)   DO_INCREMENTAL=1; shift ;;
        --deep)          SHALLOW=0; shift ;;
        -h|--help)       usage ;;
        *) err "unknown arg: $1"; usage ;;
    esac
done

check_prereqs() {
    log "checking prerequisites..."
    local missing=0
    for tool in git curl python3 unzip java readelf debugfs; do
        command -v "${tool}" >/dev/null 2>&1 || { err "missing tool: ${tool}"; missing=1; }
    done
    if command -v java >/dev/null 2>&1; then
        local jv
        jv="$(java -version 2>&1 | head -1 || true)"
        log "  java: ${jv}"
        case "${jv}" in
            *'1.8'*) : ;;
            *) err "  AOSP 8.1 requires JDK 8; detected: ${jv}" ;;
        esac
    fi
    # crude resource check
    local memkb cpus
    memkb="$(awk '/MemTotal/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)"
    cpus="$(nproc 2>/dev/null || echo 0)"
    log "  cpu=${cpus} mem=$((memkb/1024)) MiB"
    if [[ "${memkb}" -lt 30000000 ]]; then
        err "  <30 GB RAM detected; a full 8.1 build will likely thrash/fail"
    fi
    [[ "${missing}" -eq 0 ]] || { err "install the missing tools first (plan section 11)"; exit 1; }
    log "prerequisites OK (warnings above are non-fatal)."
}

do_sync() {
    [[ -n "${WORKDIR}" ]] || { err "--workdir required for --sync"; exit 1; }
    mkdir -p "${WORKDIR}"; cd "${WORKDIR}"
    # Shallow (--depth=1) + current-branch-only (-c) + --no-tags keeps the
    # checkout at ~60-80 GB instead of ~100 GB+ (full history).  A buildable
    # tree still needs the platform + prebuilts toolchain, so this is the floor,
    # not "a few GB" -- but it lets the build fit on a smaller large-runner.
    local -a depth_arg=(--depth=1)
    [[ "${SHALLOW}" == "1" ]] || depth_arg=()
    if [[ ! -d .repo ]]; then
        log "repo init -b ${MANIFEST_BRANCH} (shallow=${SHALLOW})"
        repo init -u https://android.googlesource.com/platform/manifest \
            -b "${MANIFEST_BRANCH}" "${depth_arg[@]}" --no-tags
    fi
    log "repo sync -c -j${JOBS} --no-tags --no-clone-bundle --optimized-fetch (this takes a while)"
    repo sync -c -j"${JOBS}" --no-tags --no-clone-bundle --optimized-fetch
}

link_trees() {
    [[ -n "${WORKDIR}" ]] || { err "--workdir required"; exit 1; }
    local dev="${WORKDIR}/device/keenplus/u2" ven="${WORKDIR}/vendor/keenplus/u2"
    mkdir -p "$(dirname "${dev}")" "$(dirname "${ven}")"
    log "linking device tree -> ${dev}"
    ln -sfn "${REPO_ROOT}/device/keenplus/u2" "${dev}"
    if [[ -d "${REPO_ROOT}/vendor/keenplus/u2" ]]; then
        log "linking vendor tree -> ${ven}"
        ln -sfn "${REPO_ROOT}/vendor/keenplus/u2" "${ven}"
    fi
}

apply() {
    [[ -n "${WORKDIR}" ]] || { err "--workdir required"; exit 1; }
    log "applying patch series"
    "${HERE}/apply-patches.sh" --aosp-root "${WORKDIR}"
}

extract() {
    if [[ -n "${SYSTEM_IMAGE}" ]]; then
        log "extracting blobs from ${SYSTEM_IMAGE}"
        "${REPO_ROOT}/device/keenplus/u2/extract-files.sh" "${SYSTEM_IMAGE}"
    else
        log "no --system-image given; skipping blob extraction"
        log "  (the build will fail at PRODUCT_COPY_FILES that reference vendor/keenplus/u2/proprietary/*"
        log "   until you run device/keenplus/u2/extract-files.sh <baseline system.img>)"
    fi
}

make_images() {
    [[ -n "${WORKDIR}" ]] || { err "--workdir required for --make"; exit 1; }
    cd "${WORKDIR}"
    log "source build/envsetup.sh && lunch ${LUNCH} && make -j${JOBS}"
    # shellcheck disable=SC1091
    source build/envsetup.sh
    lunch "${LUNCH}"
    make -j"${JOBS}"
    log "images under ${WORKDIR}/out/target/product/u2/"
}

make_incremental() {
    # Rebuild ONLY the modules our patches touch, then regenerate system.img with
    # 'snod' (Systemimage, NO Dependencies) -- ~10-20 min vs a full build.  This
    # requires a prior FULL build's tree + out/ to exist (see docs/INCREMENTAL_BUILD.md).
    # NOTE: it rebuilds the tree AS-IS.  If you changed the patches, refresh them
    # first (scripts/apply-patches.sh --aosp-root <workdir> after a git reset of
    # the patched projects) -- this step does not manage patch state.
    [[ -n "${WORKDIR}" ]] || { err "--workdir required for --incremental"; exit 1; }
    local sysdir="${WORKDIR}/out/target/product/u2/system"
    if [[ ! -d "${sysdir}" ]]; then
        err "no prior full build found (${sysdir} missing)."
        err "run a full build first: build.sh --workdir ${WORKDIR} --sync --make"
        exit 1
    fi
    cd "${WORKDIR}"
    log "incremental: m ${BT_MODULES[*]} snod  (reusing existing out/)"
    # shellcheck disable=SC1091
    source build/envsetup.sh
    lunch "${LUNCH}"
    m -j"${JOBS}" "${BT_MODULES[@]}" snod
    log "regenerated ${WORKDIR}/out/target/product/u2/system.img"
}

check_prereqs
if [[ "${DO_INCREMENTAL}" == "1" ]]; then
    make_incremental
    exit 0
fi
[[ "${DO_SYNC}" == "1" ]] && do_sync
if [[ -n "${WORKDIR}" && -d "${WORKDIR}/system/bt" ]]; then
    apply
    link_trees
    extract
fi
[[ "${DO_MAKE}" == "1" ]] && make_images
if [[ "${DO_SYNC}" == "0" && "${DO_MAKE}" == "0" ]]; then
    log "dry run complete. Re-run with --sync and/or --make on a provisioned build host."
fi
