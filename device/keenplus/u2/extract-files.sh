#!/usr/bin/env bash
# extract-files.sh -- pull the KeenPlus U2 proprietary blobs out of the baseline
# firmware's system image into vendor/keenplus/u2/proprietary (plan section 14.4).
#
# Usage:
#   ./extract-files.sh <system-image>
#
# <system-image> may be either:
#   * the sparse system.img from the baseline flash package (block_size 4096), or
#   * an already-converted raw ext4 image.
#
# The script auto-detects the sparse magic (0xED26FF3A) and, when needed,
# converts with scripts/mtk_sparse2raw.py before reading it with debugfs (no root
# required).  Every extracted .so is checked with `readelf -h` and the script
# aborts if a blob is 64-bit only (risk R4 / decision D2: 32-bit userspace).
#
# Requirements: python3, debugfs (e2fsprogs), readelf (binutils).

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../../.." && pwd)"
SPARSE2RAW="${REPO_ROOT}/scripts/mtk_sparse2raw.py"
LIST="${HERE}/proprietary-files.txt"
VENDOR_OUT="${REPO_ROOT}/vendor/keenplus/u2/proprietary"

usage() {
    echo "Usage: ${0##*/} <system-image>" >&2
    echo "  <system-image> : sparse (block_size 4096) or raw ext4 system image" >&2
    exit 2
}

need() {
    command -v "$1" >/dev/null 2>&1 || { echo "ERROR: required tool '$1' not found" >&2; exit 1; }
}

[[ $# -eq 1 ]] || usage
SRC_IMG="$1"
[[ -f "${SRC_IMG}" ]] || { echo "ERROR: '${SRC_IMG}' is not a file" >&2; exit 1; }

need python3
need debugfs
need readelf

WORKDIR="$(mktemp -d)"
trap 'rm -rf "${WORKDIR}"' EXIT

# ---- 1. Detect sparse vs raw and obtain a raw ext4 image -------------------- #
MAGIC_HEX="$(od -An -tx4 -N4 --endian=little "${SRC_IMG}" 2>/dev/null | tr -d ' ' || true)"
RAW_IMG="${SRC_IMG}"
if [[ "${MAGIC_HEX}" == "ed26ff3a" ]]; then
    echo "[*] Sparse image detected (magic 0xED26FF3A); converting to raw..."
    RAW_IMG="${WORKDIR}/system.raw.img"
    python3 "${SPARSE2RAW}" "${SRC_IMG}" "${RAW_IMG}" --check-ext4
else
    echo "[*] Assuming raw ext4 image; verifying superblock..."
    python3 -c "import sys; sys.path.insert(0,'${REPO_ROOT}/scripts'); import mtk_sparse2raw as m; m.check_ext4('${SRC_IMG}')"
fi

# ---- 2. Extract each entry -------------------------------------------------- #
mkdir -p "${VENDOR_OUT}"
extracted=0
skipped=0
dirs=()

# Read the list, ignoring comments and blank lines.
while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line%%#*}"                       # strip inline comments
    line="$(echo "${line}" | sed 's/[[:space:]]\+$//; s/^[[:space:]]\+//')"
    [[ -z "${line}" ]] && continue

    src="/${line#/}"                          # normalise to a leading slash
    dst="${VENDOR_OUT}/${line}"

    # Is it a directory in the image?  debugfs 'ls -d' returns 0 for dirs.
    if debugfs -R "ls -d ${src}" "${RAW_IMG}" >/dev/null 2>&1; then
        echo "[dir] ${src}"
        mkdir -p "${dst}"
        # rdump copies a directory tree recursively.
        debugfs -R "rdump ${src} ${dst%/*}" "${RAW_IMG}" >/dev/null 2>&1 || {
            echo "  WARN: rdump failed for ${src}" >&2; skipped=$((skipped+1)); continue; }
        dirs+=("${line}")
        extracted=$((extracted+1))
        continue
    fi

    mkdir -p "$(dirname "${dst}")"
    if debugfs -R "dump ${src} ${dst}" "${RAW_IMG}" >/dev/null 2>&1 && [[ -s "${dst}" ]]; then
        echo "[file] ${src}"
        extracted=$((extracted+1))
        # 32-bit check for ELF shared objects.
        if readelf -h "${dst}" >/dev/null 2>&1; then
            class="$(readelf -h "${dst}" | awk -F: '/Class:/ {gsub(/ /,"",$2); print $2}')"
            if [[ "${class}" == "ELF64" ]]; then
                echo "ERROR: ${line} is ELF64 but this port is 32-bit-only (D2)." >&2
                echo "       Fall back to D2' (keep 64-bit) for this module or find a 32-bit blob." >&2
                exit 3
            fi
        fi
    else
        echo "  MISSING: ${src} (not present in image)" >&2
        rm -f "${dst}"
        skipped=$((skipped+1))
    fi
done < "${LIST}"

echo
echo "[*] Extracted ${extracted} entries into ${VENDOR_OUT} (${skipped} missing/skipped)."
if [[ ${#dirs[@]} -gt 0 ]]; then
    echo "[*] Directories copied: ${dirs[*]}"
fi
echo "[*] Next: review vendor/keenplus/u2/ and add prebuilt module makefiles as needed."
