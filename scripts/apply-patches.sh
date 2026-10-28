#!/usr/bin/env bash
# apply-patches.sh -- apply (or verify) the KeenPlus U2 AOSP source patches.
#
# Two modes:
#
#   apply-patches.sh --aosp-root <dir>
#       Apply every series to an existing AOSP 8.1 tree with `git am`.
#
#   apply-patches.sh --verify [--keep]
#       Fresh shallow-clone each AOSP repo at android-8.1.0_r80 into a temp dir
#       and `git am` the series.  Proves the patches apply to the pristine tag.
#       This is what CI runs.  Non-zero exit if any patch fails to apply.
#
# Requirements: git, network access (for --verify).

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/.." && pwd)"
PATCHES="${REPO_ROOT}/patches"
TAG="android-8.1.0_r80"

# repo-subdir : patches-subdir : git-url ["|lite" suffix]
# "|lite" = huge repo; --verify fetches only the files touched by the series
# (googlesource ?format=TEXT) into a mini repo instead of cloning gigabytes.
SERIES=(
    "hardware/libhardware:hardware_libhardware:https://android.googlesource.com/platform/hardware/libhardware"
    "system/bt:system_bt:https://android.googlesource.com/platform/system/bt"
    "packages/apps/Bluetooth:packages_apps_Bluetooth:https://android.googlesource.com/platform/packages/apps/Bluetooth"
    "prebuilts/sdk:prebuilts_sdk:https://android.googlesource.com/platform/prebuilts/sdk|lite"
)

usage() {
    sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 2
}

log() { printf '[apply-patches] %s\n' "$*"; }
err() { printf '[apply-patches] ERROR: %s\n' "$*" >&2; }

apply_series() {
    # $1 = absolute path to the checked-out repo, $2 = patches subdir
    local repo_dir="$1" pdir="$2"
    local patchdir="${PATCHES}/${pdir}"
    if [[ ! -d "${patchdir}" ]]; then
        err "no patch dir ${patchdir}"
        return 1
    fi
    shopt -s nullglob
    local -a pfiles=("${patchdir}"/[0-9]*.patch)
    shopt -u nullglob
    if [[ ${#pfiles[@]} -eq 0 ]]; then
        log "  (no .patch files in ${pdir}; skipping)"
        return 0
    fi
    ( cd "${repo_dir}" && git am "${pfiles[@]}" )
}

mode_apply() {
    local aosp_root="$1"
    [[ -d "${aosp_root}" ]] || { err "--aosp-root '${aosp_root}' is not a directory"; exit 1; }
    local entry sub pdir _url target
    for entry in "${SERIES[@]}"; do
        IFS=':' read -r sub pdir _url <<<"${entry}"
        target="${aosp_root}/${sub}"
        if [[ ! -d "${target}/.git" && ! -f "${target}/.git" ]]; then
            err "${target} is not a git checkout; run 'repo sync' first"
            exit 1
        fi
        log "applying ${pdir} -> ${sub}"
        apply_series "${target}" "${pdir}"
    done
    log "done."
}

mode_verify() {
    local keep="${1:-0}"
    local workdir
    workdir="$(mktemp -d)"
    # shellcheck disable=SC2064
    trap "[[ '${keep}' == '1' ]] || rm -rf '${workdir}'" EXIT
    log "verify mode: cloning ${TAG} into ${workdir}"
    local entry sub pdir url mode target f
    for entry in "${SERIES[@]}"; do
        IFS=':' read -r sub pdir url <<<"${entry}"
        mode=""
        if [[ "${url}" == *"|lite" ]]; then mode="lite"; url="${url%|lite}"; fi
        target="${workdir}/${sub}"
        mkdir -p "$(dirname "${target}")"
        if [[ "${mode:-}" == "lite" ]]; then
            log "  lite-verify ${sub} @ ${TAG} (fetch series-touched files only)"
            mkdir -p "${target}"
            ( cd "${target}" && git init -q . \
                && git config user.name ci && git config user.email ci@localhost )
            while IFS= read -r f; do
                [[ -n "${f}" ]] || continue
                mkdir -p "${target}/$(dirname "${f}")"
                curl -sfL "${url}/+/refs/tags/${TAG}/${f}?format=TEXT" \
                    | base64 -d > "${target}/${f}"
            done < <(grep -h '^diff --git' "${PATCHES}/${pdir}"/[0-9]*.patch \
                     | sed 's|^diff --git a/||; s| b/.*$||' | sort -u)
            ( cd "${target}" && git add -A \
                && git commit -qm "pristine ${TAG} (series-touched files)" )
        else
            log "  clone ${sub} @ ${TAG}"
            git clone --quiet --depth 1 --branch "${TAG}" "${url}" "${target}"
            ( cd "${target}" && git config user.name "ci" && git config user.email "ci@localhost" )
        fi
        log "  git am ${pdir}"
        if ! apply_series "${target}" "${pdir}"; then
            err "FAILED to apply ${pdir} to ${sub}"
            exit 1
        fi
        log "  OK ${sub}"
    done
    log "all patch series applied cleanly to ${TAG}."
    if [[ "${keep}" == "1" ]]; then
        log "kept workdir: ${workdir}"
    fi
    return 0
}

# ---- arg parsing ---------------------------------------------------------- #
[[ $# -ge 1 ]] || usage
case "$1" in
    --verify)
        keep=0; [[ "${2:-}" == "--keep" ]] && keep=1
        mode_verify "${keep}"
        ;;
    --aosp-root)
        [[ $# -ge 2 ]] || usage
        mode_apply "$2"
        ;;
    -h|--help)
        usage
        ;;
    *)
        usage
        ;;
esac
