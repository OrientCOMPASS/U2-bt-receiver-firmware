#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_stub_apk.py -- build a minimal VALID stub APK (binary manifest only).

Why this exists (debloat v3, dropper neutralization round 2)
------------------------------------------------------------
debloat v2 deleted the six payload APKs under /system/vendor/operator/app/
and the device then hung at first boot (looping progress bar; v0/v1 with the
payloads present boot fine).  The MTK USP first-boot customization
(com.mediatek.usp.CustomOperator inside services.odex) plus the vendor-
modified PackageInstaller walk that directory; with it emptied the flow
apparently never completes.  v3 therefore keeps the payload TREE, FILE NAMES
and PACKAGE NAMES exactly as stock and replaces only the APK CONTENTS with
~2 KiB valid stubs: same install flow, zero third-party code shipped.

The AXML encoder mirrors, byte for byte, the layout aapt produced in this
very firmware (probe v8 ground truth: ExactCalculator.apk manifest):

  * root chunk 0x0003, StringPool 0x0001 (UTF-16, flags=0, no styles),
    ResourceMap 0x0180, StartNamespace 0x0100 / EndNamespace 0x0101,
    StartTag 0x0102 / EndTag 0x0103;
  * the resource map covers exactly the leading string-pool entries, which
    must therefore be the namespaced android:* attribute names;
  * `package` and `platformBuildVersion*` carry NO namespace (0xFFFFFFFF)
    and no resource id, like real aapt output;
  * typed values: STRING(0x03) with rawValue index, INT_DEC(0x10),
    FLOAT(0x04) for platformBuildVersionName (7.0f), INT_BOOL(0x12) for
    android:hasCode=false (the stub ships no classes.dex -- hasCode=false
    makes PackageManagerService skip dexopt entirely).

Signing: JDK jarsigner, v1 (JAR) scheme, SHA-256 -- accepted by the
Android 7 PackageParser.  Self-check re-parses the result with
fw_inventory.manifest_apk (the same parser that drives every CI inventory
assert), so a malformed stub can never leave the build.
"""

from __future__ import annotations

import argparse
import os
import shutil
import struct
import subprocess
import sys
import zipfile
from typing import Dict, List, Optional

NS_URI = "http://schemas.android.com/apk/res/android"
NS_PREFIX = "android"

# android:* attribute resource ids -- verified against AOSP 7.0 public.xml
# AND against a real manifest from the baseline firmware (probe v8).
RES_ATTR_IDS: Dict[str, int] = {
    "versionCode": 0x0101021B,
    "versionName": 0x0101021C,
    "minSdkVersion": 0x0101020C,
    "targetSdkVersion": 0x01010270,
    "hasCode": 0x0101000C,
}

TYPE_STRING = 0x03
TYPE_FLOAT = 0x04
TYPE_INT_DEC = 0x10
TYPE_INT_BOOL = 0x12
NO_REF = 0xFFFFFFFF

CHUNK_XML = 0x0003
CHUNK_STRING_POOL = 0x0001
CHUNK_RES_MAP = 0x0180
CHUNK_START_NS = 0x0100
CHUNK_END_NS = 0x0101
CHUNK_START_TAG = 0x0102
CHUNK_END_TAG = 0x0103


def _u16(x: int) -> bytes:
    return struct.pack("<H", x & 0xFFFF)


def _u32(x: int) -> bytes:
    return struct.pack("<I", x & 0xFFFFFFFF)


def _f32bits(x: float) -> int:
    return struct.unpack("<I", struct.pack("<f", x))[0]


class StringPool:
    """UTF-16 string pool with the aapt chunk layout."""

    def __init__(self) -> None:
        self._strings: List[str] = []
        self._index: Dict[str, int] = {}

    def add(self, s: str) -> int:
        if s not in self._index:
            self._index[s] = len(self._strings)
            self._strings.append(s)
        return self._index[s]

    def idx(self, s: str) -> int:
        return self._index[s]

    def build(self) -> bytes:
        offs: List[int] = []
        blob = b""
        for s in self._strings:
            offs.append(len(blob))
            enc = s.encode("utf-16-le")
            blob += _u16(len(s)) + enc + b"\x00\x00"
        blob += b"\x00" * ((-len(blob)) % 4)  # 4-byte align the pool tail
        n = len(self._strings)
        hdr = (_u16(CHUNK_STRING_POOL) + _u16(28)
               + _u32(28 + 4 * n + len(blob))
               + _u32(n) + _u32(0)          # stringCount, styleCount
               + _u32(0)                    # flags: UTF-16
               + _u32(28 + 4 * n)           # stringsStart (chunk-relative)
               + _u32(0))                   # stylesStart
        return hdr + b"".join(_u32(o) for o in offs) + blob


def _resmap(attr_names: List[str]) -> bytes:
    ids = [RES_ATTR_IDS[s] for s in attr_names]
    return (_u16(CHUNK_RES_MAP) + _u16(8) + _u32(8 + 4 * len(ids))
            + b"".join(_u32(i) for i in ids))


def _ns_chunk(typ: int, line: int, prefix_idx: int, uri_idx: int) -> bytes:
    return (_u16(typ) + _u16(16) + _u32(24) + _u32(line) + _u32(NO_REF)
            + _u32(prefix_idx) + _u32(uri_idx))


def _attr(ns_idx: int, name_idx: int, raw_idx: int, dtype: int,
          data: int) -> bytes:
    return (_u32(ns_idx) + _u32(name_idx) + _u32(raw_idx)
            + _u16(8) + bytes([0, dtype]) + _u32(data))


def _start_tag(line: int, name_idx: int, attrs: List[bytes]) -> bytes:
    ext = (_u32(NO_REF) + _u32(name_idx)          # element ns, name
           + _u16(20) + _u16(20) + _u16(len(attrs))  # attrStart/Size/Count
           + _u16(0) + _u16(0) + _u16(0))            # id/class/style index
    body = _u32(line) + _u32(NO_REF) + ext + b"".join(attrs)
    return _u16(CHUNK_START_TAG) + _u16(16) + _u32(8 + len(body)) + body


def _end_tag(line: int, name_idx: int) -> bytes:
    body = _u32(line) + _u32(NO_REF) + _u32(NO_REF) + _u32(name_idx)
    return _u16(CHUNK_END_TAG) + _u16(16) + _u32(8 + len(body)) + body


def build_manifest_axml(package: str, version_code: int, version_name: str,
                        min_sdk: int, target_sdk: int) -> bytes:
    pool = StringPool()
    # 1) namespaced android:* attr names FIRST (resource-map order)
    android_attrs = ["versionCode", "versionName", "minSdkVersion",
                     "targetSdkVersion", "hasCode"]
    for a in android_attrs:
        pool.add(a)
    k = len(android_attrs)
    # 2) everything else
    i_prefix = pool.add(NS_PREFIX)
    i_uri = pool.add(NS_URI)
    pool.add("")                       # aapt always carries the empty string
    i_package = pool.add("package")
    i_pbvc = pool.add("platformBuildVersionCode")
    i_pbvn = pool.add("platformBuildVersionName")
    i_manifest = pool.add("manifest")
    i_pkg = pool.add(package)
    i_vname = pool.add(version_name)
    i_usessdk = pool.add("uses-sdk")
    i_app = pool.add("application")
    pool.add(str(min_sdk))             # raw string aapt keeps for numbers
    i_70 = pool.add("7.0")

    ev: List[bytes] = []
    ev.append(_ns_chunk(CHUNK_START_NS, 1, i_prefix, i_uri))
    ev.append(_start_tag(1, i_manifest, [
        _attr(i_uri, pool.idx("versionCode"), NO_REF, TYPE_INT_DEC,
              version_code),
        _attr(i_uri, pool.idx("versionName"), i_vname, TYPE_STRING, i_vname),
        _attr(NO_REF, i_package, i_pkg, TYPE_STRING, i_pkg),
        _attr(NO_REF, i_pbvc, NO_REF, TYPE_INT_DEC, min_sdk),
        _attr(NO_REF, i_pbvn, i_70, TYPE_FLOAT, _f32bits(7.0)),
    ]))
    ev.append(_start_tag(2, i_usessdk, [
        _attr(i_uri, pool.idx("minSdkVersion"), NO_REF, TYPE_INT_DEC,
              min_sdk),
        _attr(i_uri, pool.idx("targetSdkVersion"), NO_REF, TYPE_INT_DEC,
              target_sdk),
    ]))
    ev.append(_end_tag(2, i_usessdk))
    ev.append(_start_tag(3, i_app, [
        # no classes.dex in the stub -> tell PMS there is no code to dexopt
        _attr(i_uri, pool.idx("hasCode"), NO_REF, TYPE_INT_BOOL, 0),
    ]))
    ev.append(_end_tag(3, i_app))
    ev.append(_end_tag(1, i_manifest))
    ev.append(_ns_chunk(CHUNK_END_NS, 4, i_prefix, i_uri))

    body = pool.build() + _resmap(android_attrs[:k]) + b"".join(ev)
    return _u16(CHUNK_XML) + _u16(8) + _u32(8 + len(body)) + body


def write_apk(out_path: str, axml: bytes) -> None:
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("AndroidManifest.xml", axml)


def sign_apk_v1(apk: str, keystore: str, alias: str, storepass: str) -> None:
    subprocess.run(["jarsigner", "-keystore", keystore,
                    "-storepass", storepass,
                    "-sigalg", "SHA256withRSA", "-digestalg", "SHA-256",
                    apk, alias], check=True, capture_output=True)
    r = subprocess.run(["jarsigner", "-verify", apk],
                       capture_output=True, text=True, check=True)
    if "jar verified" not in r.stdout:
        raise RuntimeError(f"jarsigner verify failed: {r.stdout} {r.stderr}")


def self_check(apk: str, package: str, version_code: int) -> Dict:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import fw_inventory  # same repo dir
    m = fw_inventory.manifest_apk(apk)
    assert m.get("parse_error") in (None, ""), f"stub manifest parse: {m}"
    assert m.get("package") == package, f"package mismatch: {m}"
    assert m.get("versionCode") == version_code, f"versionCode mismatch: {m}"
    return m


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", help="output .apk path")
    ap.add_argument("--package", required=True)
    ap.add_argument("--version-code", type=int, default=1)
    ap.add_argument("--version-name", default="1.0")
    ap.add_argument("--min-sdk", type=int, default=24)
    ap.add_argument("--target-sdk", type=int, default=24)
    ap.add_argument("--keystore", default="",
                    help="JKS keystore for v1 signing (jarsigner)")
    ap.add_argument("--alias", default="stubkey")
    ap.add_argument("--storepass", default="android")
    ap.add_argument("--max-bytes", type=int, default=8192,
                    help="hard size cap (default 8192)")
    ap.add_argument("--no-self-check", action="store_true")
    args = ap.parse_args(argv)

    axml = build_manifest_axml(args.package, args.version_code,
                               args.version_name, args.min_sdk,
                               args.target_sdk)
    write_apk(args.out, axml)
    if args.keystore:
        if not shutil.which("jarsigner"):
            raise SystemExit("jarsigner not found -- cannot sign")
        sign_apk_v1(args.out, args.keystore, args.alias, args.storepass)
    if not args.no_self_check:
        self_check(args.out, args.package, args.version_code)
    size = os.path.getsize(args.out)
    assert size <= args.max_bytes, f"stub too large: {size} > {args.max_bytes}"
    print(f"[stub] {args.out}: {size} B  package={args.package} "
          f"versionCode={args.version_code} signed={'yes' if args.keystore else 'NO'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
