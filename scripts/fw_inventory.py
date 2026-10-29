#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fw_inventory.py -- inventory & verity-scan helpers for the baseline-editing
(debloat) route on the official KeenPlus U2 Android 7.0 firmware.

Pure standard library.  Sub-commands:

  manifest APK    parse an APK's binary AndroidManifest.xml (AXML);
                  print {package, versionCode, versionName, label} as JSON
  apps ROOT       scan the app roots of a mounted system image
                  (app/, priv-app/, vendor/app/, vendor/priv-app/,
                  overlay/, vendor/overlay/) and emit a JSON inventory:
                  one record per APK with root/entry/paths/sizes/package/
                  version and oat|odex presence
  verity IMG      scan a raw image for the dm-verity footer magic
                  (0xb001b001) and compare the ext4 fs size against the
                  image size (a trailing gap = hashtree/metadata area)
  zip-list SRC    list entries of the official firmware zip (local path or
                  http(s) URL, range-fetched via mtk_fw_zip) as JSON

Every sub-command accepts --out FILE to also write the JSON to a file.
Used by .github/workflows/fw-probe.yml and fw-debloat.yml; unit-tested in
scripts/tests/test_fw_inventory.py.
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import zipfile
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------- #
# Binary XML (AXML) parsing -- just enough for AndroidManifest.xml attributes
# --------------------------------------------------------------------------- #

CHUNK_STRING_POOL = 0x0001
CHUNK_RESOURCE_MAP = 0x0180
CHUNK_START_ELEMENT = 0x0102

TYPE_REFERENCE = 0x01
TYPE_STRING = 0x03
TYPE_INT_DEC = 0x10
TYPE_INT_HEX = 0x11
TYPE_INT_BOOLEAN = 0x12

RES_LABEL = 0x01010001
RES_VERSION_CODE = 0x0101021B
RES_VERSION_NAME = 0x0101021C


def parse_string_pool(data: bytes, chunk_off: int) -> List[str]:
    """Parse an AXML string-pool chunk starting at chunk_off."""
    (string_count, _style_count, flags, strings_start,
     _styles_start) = struct.unpack_from("<IIIII", data, chunk_off + 8)
    utf8 = bool(flags & (1 << 8))
    offs = struct.unpack_from("<%dI" % string_count, data, chunk_off + 28)
    base = chunk_off + strings_start
    out: List[str] = []
    for o in offs:
        p = base + o
        if utf8:
            n = data[p]
            p += 2 if n & 0x80 else 1          # char length (unused)
            blen = data[p]
            if blen & 0x80:
                blen = ((blen & 0x7F) << 8) | data[p + 1]
                p += 2
            else:
                p += 1
            out.append(data[p:p + blen].decode("utf-8", "replace"))
        else:
            n = struct.unpack_from("<H", data, p)[0]
            p += 4 if n & 0x8000 else 2        # char length (u16, ext.)
            out.append(data[p:p + 2 * n].decode("utf-16-le", "replace"))
    return out


def parse_axml(data: bytes) -> Dict[str, Any]:
    """Extract {package, versionCode, versionName, label} from an AXML doc."""
    if len(data) < 8:
        raise ValueError("AXML too short")
    magic, hdr_size = struct.unpack_from("<HH", data, 0)
    if magic != 0x0003:
        raise ValueError("bad AXML magic 0x%04X" % magic)
    strings: List[str] = []
    resmap: List[int] = []
    result: Dict[str, Any] = {"package": None, "versionCode": None,
                               "versionName": None, "label": None}
    off = hdr_size
    while off + 8 <= len(data):
        ctype, chdr, csize = struct.unpack_from("<HHI", data, off)
        if csize < 8 or off + csize > len(data):
            break
        if ctype == CHUNK_STRING_POOL:
            strings = parse_string_pool(data, off)
        elif ctype == CHUNK_RESOURCE_MAP:
            n = (csize - chdr) // 4
            if n > 0:
                resmap = list(struct.unpack_from("<%dI" % n, data, off + chdr))
        elif ctype == CHUNK_START_ELEMENT:
            e = off + chdr                       # start of attrExt
            name_idx = struct.unpack_from("<i", data, e + 4)[0]
            astart = struct.unpack_from("<H", data, e + 8)[0] or 20
            asize = struct.unpack_from("<H", data, e + 10)[0] or 20
            count = struct.unpack_from("<H", data, e + 12)[0]
            el = strings[name_idx] if 0 <= name_idx < len(strings) else "?"
            attrs: List[Dict[str, Any]] = []
            for i in range(count):
                ao = e + astart + i * asize
                a_ns, a_name, _a_raw = struct.unpack_from("<iii", data, ao)
                _sz, _r0, vtype, vdata = struct.unpack_from("<HBBi", data, ao + 12)
                resid = resmap[a_name] if 0 <= a_name < len(resmap) else 0
                nm = strings[a_name] if 0 <= a_name < len(strings) else "?"
                if vtype == TYPE_STRING:
                    val: Any = strings[vdata] if 0 <= vdata < len(strings) else None
                elif vtype == TYPE_INT_BOOLEAN:
                    val = bool(vdata)
                elif vtype == TYPE_REFERENCE:
                    val = "@0x%08x" % (vdata & 0xFFFFFFFF)
                else:
                    val = vdata
                attrs.append({"ns": a_ns, "name": nm, "resid": resid,
                              "type": vtype, "value": val})
            if el == "manifest" and result["package"] is None:
                for a in attrs:
                    if a["name"] == "package" and a["resid"] == 0:
                        result["package"] = a["value"]
                    elif a["resid"] == RES_VERSION_CODE:
                        result["versionCode"] = a["value"]
                    elif a["resid"] == RES_VERSION_NAME:
                        result["versionName"] = a["value"]
            elif el == "application" and result["label"] is None:
                for a in attrs:
                    if a["resid"] == RES_LABEL:
                        result["label"] = a["value"]
        off += csize
    return result


def manifest_apk(apk_path: str) -> Dict[str, Any]:
    """Parse AndroidManifest.xml out of an APK; never raises."""
    try:
        with zipfile.ZipFile(apk_path) as z:
            with z.open("AndroidManifest.xml") as f:
                return parse_axml(f.read())
    except Exception as e:  # noqa: BLE001
        return {"package": None, "versionCode": None, "versionName": None,
                "label": None, "parse_error": f"{type(e).__name__}: {e}"}


# --------------------------------------------------------------------------- #
# app inventory of a mounted /system
# --------------------------------------------------------------------------- #

APP_ROOTS = ["app", "priv-app", "vendor/app", "vendor/priv-app",
             "overlay", "vendor/overlay", "preload", "preinstall"]


def _dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for fn in files:
            try:
                total += os.lstat(os.path.join(root, fn)).st_size
            except OSError:
                pass
    return total


def scan_apps(root: str) -> List[Dict[str, Any]]:
    """One record per APK found under the conventional app roots of `root`."""
    recs: List[Dict[str, Any]] = []
    for ar in APP_ROOTS:
        base = os.path.join(root, ar)
        if not os.path.isdir(base):
            continue
        for entry in sorted(os.listdir(base)):
            p = os.path.join(base, entry)
            if os.path.isdir(p):
                apks = sorted(f for f in os.listdir(p) if f.endswith(".apk"))
                size = _dir_size(p)
                oat = os.path.isdir(os.path.join(p, "oat"))
                odex = []
                for r2, _d2, f2 in os.walk(p):
                    odex += [os.path.relpath(os.path.join(r2, x), root)
                             for x in f2 if x.endswith((".odex", ".vdex"))]
            elif entry.endswith(".apk"):
                apks = [entry]
                size = os.lstat(p).st_size
                oat = False
                odex = []
            else:
                continue
            for apk in apks:
                ap = os.path.join(p, apk) if os.path.isdir(p) else p
                m = manifest_apk(ap)
                rec = {
                    "root": ar,
                    "entry": entry,
                    "apk": os.path.relpath(ap, root),
                    "apk_size": os.lstat(ap).st_size,
                    "dir_size": size,
                    "has_oat_dir": oat,
                    "odex": odex,
                    "package": m.get("package"),
                    "versionCode": m.get("versionCode"),
                    "versionName": m.get("versionName"),
                    "label": m.get("label"),
                }
                if m.get("parse_error"):
                    rec["parse_error"] = m["parse_error"]
                recs.append(rec)
    recs.sort(key=lambda r: (-r["dir_size"], r["apk"]))
    return recs


# --------------------------------------------------------------------------- #
# dm-verity footer / trailing-area scan of a raw image
# --------------------------------------------------------------------------- #

VERITY_FOOTER_MAGIC = b"\x01\xb0\x01\xb0"        # 0xb001b001 little-endian
EXT4_SUPER_MAGIC = 0xEF53


def ext4_fs_size(img: str) -> Optional[int]:
    """Return blocks*block_size from the ext4 superblock, else None."""
    try:
        with open(img, "rb") as f:
            f.seek(1024)
            sb = f.read(1024)
        if len(sb) < 1024 or struct.unpack_from("<H", sb, 56)[0] != EXT4_SUPER_MAGIC:
            return None
        blocks = struct.unpack_from("<I", sb, 4)[0]
        log_bs = struct.unpack_from("<I", sb, 24)[0]
        return blocks * (1024 << log_bs)
    except OSError:
        return None


def scan_verity(img: str, tail_scan: int = 1 << 20,
                max_trailing_scan: int = 512 << 20) -> Dict[str, Any]:
    """Locate verity footer magic in a raw image and report the fs-size vs
    image-size trailing gap.

    Scan window: the whole trailing area beyond the ext4 fs when it exists
    and is <= max_trailing_scan (that is where hashtree + footer live), else
    the last tail_scan bytes.  The legacy mid-point location is checked too.
    """
    size = os.path.getsize(img)
    fs_size = ext4_fs_size(img)
    trailing = (size - fs_size) if (fs_size and 0 < fs_size < size) else 0
    scan_len = tail_scan
    if 0 < trailing <= max_trailing_scan:
        scan_len = trailing
    scan_len = min(scan_len, size)
    win_start = size - scan_len
    hits: List[Dict[str, Any]] = []
    magic = VERITY_FOOTER_MAGIC
    with open(img, "rb") as f:
        f.seek(win_start)
        pos = win_start
        carry = b""
        while pos < size:
            buf = f.read(min(8 << 20, size - pos))
            if not buf:
                break
            hay = carry + buf
            base = pos - len(carry)
            start = 0
            while True:
                i = hay.find(magic, start)
                if i < 0:
                    break
                hits.append({"offset": base + i,
                             "hexdump": hay[i:i + 72].hex()})
                start = i + 4
            carry = hay[-3:] if len(hay) >= 3 else hay
            pos += len(buf)
        legacy_off = size // 2 - 4096
        legacy = False
        if 0 < legacy_off < size - 4:
            if legacy_off >= win_start:
                legacy = any(h["offset"] == legacy_off for h in hits)
            else:
                f.seek(legacy_off)
                legacy = f.read(4) == magic
    return {
        "image": img,
        "image_size": size,
        "ext4_fs_size": fs_size,
        "trailing_bytes": trailing or None,
        "scanned_bytes": scan_len,
        "footer_hits": hits,
        "footer_at_legacy_midpoint": legacy,
        "verity_present": bool(hits or legacy),
    }


# --------------------------------------------------------------------------- #
# zip entry listing (baseline firmware zip, local or remote)
# --------------------------------------------------------------------------- #

def zip_list(src: str) -> List[Dict[str, Any]]:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import mtk_fw_zip  # noqa: E402  (same-directory helper)
    rz = mtk_fw_zip.RemoteZip(src)
    try:
        return [{"name": e.name, "method": e.method,
                 "compressed": e.comp_size, "uncompressed": e.uncomp_size,
                 "crc32": "%08x" % e.crc}
                for e in sorted(rz.entries.values(), key=lambda x: x.name)]
    finally:
        rz.close()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _emit(obj: Any, out: Optional[str]) -> None:
    text = json.dumps(obj, indent=1, ensure_ascii=False)
    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("manifest", help="APK -> manifest JSON")
    m.add_argument("apk")
    m.add_argument("--out")

    a = sub.add_parser("apps", help="mounted-system app inventory")
    a.add_argument("root", help="mount point of the system image")
    a.add_argument("--out")

    v = sub.add_parser("verity", help="raw image verity-footer/trailing scan")
    v.add_argument("image")
    v.add_argument("--out")

    z = sub.add_parser("zip-list", help="firmware zip entry listing")
    z.add_argument("src", help="zip path or http(s) URL")
    z.add_argument("--out")

    args = ap.parse_args(argv)
    if args.cmd == "manifest":
        _emit(manifest_apk(args.apk), args.out)
    elif args.cmd == "apps":
        _emit(scan_apps(args.root), args.out)
    elif args.cmd == "verity":
        _emit(scan_verity(args.image), args.out)
    elif args.cmd == "zip-list":
        _emit(zip_list(args.src), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
