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

need debugfs
need readelf
# python3 >= 3.7 required (mtk_sparse2raw.py uses `from __future__ import
# annotations`); on hosts with an older system python3 (e.g. Ubuntu 16.04)
# point PYTHON3 at a modern interpreter.
PYTHON3="${PYTHON3:-python3}"
command -v "$PYTHON3" >/dev/null 2>&1 || { echo "ERROR: python3 ('$PYTHON3') not found" >&2; exit 1; }

WORKDIR="$(mktemp -d)"
trap 'rm -rf "${WORKDIR}"' EXIT

# ---- 1. Detect sparse vs raw and obtain a raw ext4 image -------------------- #
MAGIC_HEX="$(od -An -tx4 -N4 --endian=little "${SRC_IMG}" 2>/dev/null | tr -d ' ' || true)"
RAW_IMG="${SRC_IMG}"
if [[ "${MAGIC_HEX}" == "ed26ff3a" ]]; then
    echo "[*] Sparse image detected (magic 0xED26FF3A); converting to raw..."
    RAW_IMG="${WORKDIR}/system.raw.img"
    "$PYTHON3" "${SPARSE2RAW}" "${SRC_IMG}" "${RAW_IMG}" --check-ext4
else
    echo "[*] Assuming raw ext4 image; verifying superblock..."
    "$PYTHON3" -c "import sys; sys.path.insert(0,'${REPO_ROOT}/scripts'); import mtk_sparse2raw as m; m.check_ext4('${SRC_IMG}')"
fi

# ---- 2. Extract each entry -------------------------------------------------- #
# Clean generated tree: a previous (possibly broken) extraction must never
# leave stale entries behind.
rm -rf "${VENDOR_OUT}"
mkdir -p "${VENDOR_OUT}"
extracted=0
skipped=0
dirs=()

# Classify an image path by parsing `debugfs stat` output.  DO NOT use the
# exit status of `ls -d`: e2fsprogs 1.42.13 (Ubuntu 16.04) returns 0 for
# regular files too, which silently turned every blob into an empty
# directory.  Prints one of: directory | regular | symlink | unknown.
inode_kind() {
    local stat_out type_word
    stat_out="$(debugfs -R "stat $1" "${RAW_IMG}" 2>/dev/null || true)"
    # `Type:` shares the `Inode:` line, e.g.
    #   Inode: 11   Type: directory    Mode: 0755 ...   (e2fsprogs 1.42)
    #   Inode: 11   Type: 0x4000 (directory) ...         (newer e2fsprogs)
    type_word="$(grep -m1 -o 'Type:[[:space:]]*[^[:space:]]*' <<<"${stat_out}" \
                 | awk '{print tolower($2)}' || true)"
    case "${type_word}" in
        directory|0x4000)      echo directory ;;
        symlink|symbolic|0xa000) echo symlink ;;
        regular|0x8000)        echo regular ;;
        *)                     echo unknown ;;
    esac
}

# Resolve a symlink's target string inside the image.  Slow links: `cat`
# returns the target.  Fast links: `stat` prints it after "Fast symlink".
# Echoes '' when the target cannot be determined.
symlink_target() {
    local tgt
    tgt="$(debugfs -R "cat $1" "${RAW_IMG}" 2>/dev/null | head -c 4096 | tr -d '\0\n' || true)"
    if [[ -z "${tgt}" ]]; then
        tgt="$(debugfs -R "stat $1" "${RAW_IMG}" 2>/dev/null \
            | sed -n 's/.*[Ff]ast link[^"]*"\([^"]*\)".*/\1/p' | head -1 || true)"
    fi
    echo "${tgt}"
}

# Read the list, ignoring comments and blank lines.
while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line%%#*}"                       # strip inline comments
    line="$(echo "${line}" | sed 's/[[:space:]]\+$//; s/^[[:space:]]\+//')"
    [[ -z "${line}" ]] && continue

    src="/${line#/}"                          # normalise to a leading slash
    dst="${VENDOR_OUT}/${line}"

    kind="$(inode_kind "${src}")"

    # Symlinks are dereferenced: extract the target's bytes as a regular
    # file (prebuilt modules want real content; image-internal link paths
    # are meaningless in the vendor tree).
    if [[ "${kind}" == "symlink" ]]; then
        tgt="$(symlink_target "${src}")"
        if [[ -n "${tgt}" ]]; then
            [[ "${tgt:0:1}" == "/" ]] || tgt="$(dirname "${src}")/${tgt}"
            tgt="$("$PYTHON3" -c "import posixpath,sys;print(posixpath.normpath(sys.argv[1]))" "${tgt}" 2>/dev/null || echo "${tgt}")"
            kind="$(inode_kind "${tgt}")"
            src="${tgt}"
        fi
        if [[ "${kind}" != "regular" ]]; then
            echo "  WARN: ${line}: unresolvable symlink (target '${tgt:-?}'), skipped" >&2
            skipped=$((skipped+1))
            continue
        fi
    fi

    if [[ "${kind}" == "directory" ]]; then
        echo "[dir] ${src}"
        # NB: rdump fails with rc=0 ("File exists while making directory")
        # if the leaf already exists -- create only the parent and let
        # rdump make the leaf itself.
        mkdir -p "${dst%/*}"
        rm -rf "${dst}"
        debugfs -R "rdump ${src} ${dst%/*}" "${RAW_IMG}" >/dev/null 2>&1 || {
            echo "  WARN: rdump failed for ${src}" >&2; rm -rf "${dst}"; skipped=$((skipped+1)); continue; }
        if [[ -z "$(find "${dst}" -type f -size +0c 2>/dev/null | head -1)" ]]; then
            echo "  WARN: ${src}: directory dump produced no files" >&2
            rm -rf "${dst}"; skipped=$((skipped+1)); continue
        fi
        dirs+=("${line}")
        extracted=$((extracted+1))
        continue
    fi

    mkdir -p "$(dirname "${dst}")"
    if [[ "${kind}" == "regular" ]] && \
       debugfs -R "dump ${src} ${dst}" "${RAW_IMG}" >/dev/null 2>&1 && [[ -s "${dst}" ]]; then
        if [[ "${src}" == "/${line#/}" ]]; then
            echo "[file] ${src}"
        else
            echo "[file] ${line} (symlink dereferenced from ${src})"
        fi
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
