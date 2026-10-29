#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for scripts/fw_inventory.py.

Hermetic: the AXML parser is exercised against synthetic binary manifests
built here (no Android tooling needed); the verity scan against crafted
files; the ext4 fs-size path only when mkfs.ext4 is available (CI runners
have it).
"""

from __future__ import annotations

import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

import fw_inventory as fi  # noqa: E402


# --------------------------------------------------------------------------- #
# synthetic AXML builder
# --------------------------------------------------------------------------- #

def _utf8_pool(strings):
    offsets, blob = [], b""
    for s in strings:
        offsets.append(len(blob))
        b = s.encode("utf-8")
        assert len(s) < 0x80 and len(b) < 0x80
        blob += bytes([len(s), len(b)]) + b + b"\x00"
    hdr = struct.pack("<HHIIIIII", 0x0001, 28,
                      28 + 4 * len(strings) + len(blob),
                      len(strings), 0, 1 << 8, 28 + 4 * len(strings), 0)
    return hdr + struct.pack("<%dI" % len(strings), *offsets) + blob


def _resmap(ids):
    return (struct.pack("<HHI", 0x0180, 8, 8 + 4 * len(ids))
            + struct.pack("<%dI" % len(ids), *ids))


def _start_el(name_idx, attrs, line=1):
    ext = struct.pack("<iiHHHHHH", -1, name_idx, 20, 20, len(attrs), 0, 0, 0)
    body = b"".join(
        struct.pack("<iiiHBBi", a.get("ns", -1), a["name"], a.get("raw", -1),
                    8, 0, a["type"], a["data"]) for a in attrs)
    size = 16 + len(ext) + len(body)
    return (struct.pack("<HHI", 0x0102, 16, size)
            + struct.pack("<II", line, 0xFFFFFFFF) + ext + body)


def _end_el(name_idx):
    return (struct.pack("<HHI", 0x0103, 16, 24)
            + struct.pack("<II", 1, 0xFFFFFFFF)
            + struct.pack("<ii", -1, name_idx))


def build_manifest(pkg="com.example.test", vcode=5, vname="1.2.3",
                   label_ref=0x7F090001):
    S = ["manifest", "package", "versionCode", "versionName",
         "application", "label", pkg, vname]
    idx = {s: i for i, s in enumerate(S)}
    resmap = [0, 0, fi.RES_VERSION_CODE, fi.RES_VERSION_NAME,
              0, fi.RES_LABEL, 0, 0]
    doc = (_utf8_pool(S) + _resmap(resmap)
           + _start_el(idx["manifest"], [
               {"name": idx["package"], "raw": idx[pkg],
                "type": fi.TYPE_STRING, "data": idx[pkg]},
               {"name": idx["versionCode"], "raw": -1,
                "type": fi.TYPE_INT_DEC, "data": vcode},
               {"name": idx["versionName"], "raw": idx[vname],
                "type": fi.TYPE_STRING, "data": idx[vname]},
           ]) + _end_el(idx["manifest"])
           + _start_el(idx["application"], [
               {"name": idx["label"], "raw": -1,
                "type": fi.TYPE_REFERENCE, "data": label_ref},
           ]) + _end_el(idx["application"]))
    return struct.pack("<HHI", 0x0003, 8, 8 + len(doc)) + doc


def build_manifest_components(pkg="com.example.test", vname="1.2.3"):
    S = ["manifest", "package", "versionCode", "versionName", "application",
         "label", "name", "value", "service", "receiver", "uses-permission",
         "meta-data", pkg, vname, "com.example.MyApp", ".EvilService",
         ".BootRecv", "android.permission.INSTALL_PACKAGES", "cfgKey",
         "cfgVal"]
    idx = {s: i for i, s in enumerate(S)}
    resmap = [0] * len(S)
    resmap[idx["versionCode"]] = fi.RES_VERSION_CODE
    resmap[idx["versionName"]] = fi.RES_VERSION_NAME
    resmap[idx["label"]] = fi.RES_LABEL
    resmap[idx["name"]] = fi.RES_NAME
    resmap[idx["value"]] = fi.RES_VALUE

    def sa(sidx, ttype, data):
        return {"name": idx["name"], "raw": sidx, "type": ttype, "data": data}

    doc = (_utf8_pool(S) + _resmap(resmap)
           + _start_el(idx["manifest"], [
               {"name": idx["package"], "raw": idx[pkg],
                "type": fi.TYPE_STRING, "data": idx[pkg]},
               {"name": idx["versionCode"], "raw": -1,
                "type": fi.TYPE_INT_DEC, "data": 7},
               {"name": idx["versionName"], "raw": idx[vname],
                "type": fi.TYPE_STRING, "data": idx[vname]}])
           + _start_el(idx["application"], [
               {"name": idx["label"], "raw": -1,
                "type": fi.TYPE_REFERENCE, "data": 0x7F090001},
               sa(idx["com.example.MyApp"], fi.TYPE_STRING,
                  idx["com.example.MyApp"])])
           + _start_el(idx["meta-data"], [
               sa(idx["cfgKey"], fi.TYPE_STRING, idx["cfgKey"]),
               {"name": idx["value"], "raw": idx["cfgVal"],
                "type": fi.TYPE_STRING, "data": idx["cfgVal"]}])
           + _end_el(idx["meta-data"])
           + _start_el(idx["service"], [
               sa(idx[".EvilService"], fi.TYPE_STRING, idx[".EvilService"])])
           + _end_el(idx["service"])
           + _start_el(idx["receiver"], [
               sa(idx[".BootRecv"], fi.TYPE_STRING, idx[".BootRecv"])])
           + _end_el(idx["receiver"])
           + _end_el(idx["application"])
           + _start_el(idx["uses-permission"], [
               sa(idx["android.permission.INSTALL_PACKAGES"], fi.TYPE_STRING,
                  idx["android.permission.INSTALL_PACKAGES"])])
           + _end_el(idx["uses-permission"])
           + _end_el(idx["manifest"]))
    return struct.pack("<HHI", 0x0003, 8, 8 + len(doc)) + doc


def make_apk(path, pkg="com.example.test", vcode=5, vname="1.2.3"):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("AndroidManifest.xml", build_manifest(pkg, vcode, vname))
        z.writestr("classes.dex", b"\x00" * 64)


# --------------------------------------------------------------------------- #

class TestAxml(unittest.TestCase):
    def test_roundtrip(self):
        r = fi.parse_axml(build_manifest("com.foo.bar", 42, "9.8.7"))
        self.assertEqual(r["package"], "com.foo.bar")
        self.assertEqual(r["versionCode"], 42)
        self.assertEqual(r["versionName"], "9.8.7")
        self.assertEqual(r["label"], "@0x7f090001")

    def test_bad_magic(self):
        with self.assertRaises(ValueError):
            fi.parse_axml(b"\x00\x00\x00\x00" + b"\x00" * 32)

    def test_manifest_apk(self):
        with tempfile.TemporaryDirectory() as td:
            apk = os.path.join(td, "X.apk")
            make_apk(apk, "com.x.y", 7, "0.1")
            r = fi.manifest_apk(apk)
            self.assertEqual(r["package"], "com.x.y")
            self.assertEqual(r["versionCode"], 7)
            self.assertIsNone(r.get("parse_error"))

    def test_manifest_apk_broken(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "broken.apk")
            with open(p, "wb") as f:
                f.write(b"not a zip at all")
            r = fi.manifest_apk(p)
            self.assertIn("parse_error", r)
            self.assertIsNone(r["package"])


class TestScanApps(unittest.TestCase):
    def test_scan(self):
        with tempfile.TemporaryDirectory() as td:
            make_apk(os.path.join(td, "app/Foo/Foo.apk"), "com.foo")
            os.makedirs(os.path.join(td, "app/Foo/oat/arm"))
            with open(os.path.join(td, "app/Foo/oat/arm/Foo.odex"), "wb") as f:
                f.write(b"\x00" * 1024)
            make_apk(os.path.join(td, "priv-app/Bar/Bar.apk"), "com.bar", 2, "2.0")
            make_apk(os.path.join(td, "app/Loose.apk"), "com.loose")
            make_apk(os.path.join(td, "preload/Pre/Pre.apk"), "com.pre")
            os.makedirs(os.path.join(td, "app/NoApkHere"))          # ignored
            with open(os.path.join(td, "app/readme.txt"), "w") as f:  # ignored
                f.write("x")
            recs = fi.scan_apps(td)
            by_entry = {r["entry"]: r for r in recs}
            self.assertEqual(set(by_entry), {"Foo", "Bar", "Loose.apk", "Pre"})
            self.assertEqual(by_entry["Pre"]["root"], "preload")
            self.assertEqual(by_entry["Foo"]["package"], "com.foo")
            self.assertEqual(by_entry["Foo"]["root"], "app")
            self.assertTrue(by_entry["Foo"]["has_oat_dir"])
            self.assertEqual(by_entry["Foo"]["odex"],
                             ["app/Foo/oat/arm/Foo.odex"])
            self.assertEqual(by_entry["Bar"]["root"], "priv-app")
            self.assertEqual(by_entry["Bar"]["versionName"], "2.0")
            self.assertEqual(by_entry["Loose.apk"]["package"], "com.loose")
            self.assertEqual(by_entry["Loose.apk"]["apk"], "app/Loose.apk")
            # sorted by dir_size desc: Foo carries the 1KiB odex
            self.assertEqual(recs[0]["entry"], "Foo")

    def test_empty_root(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(fi.scan_apps(td), [])


class TestVerity(unittest.TestCase):
    def test_footer_at_tail(self):
        with tempfile.TemporaryDirectory() as td:
            img = os.path.join(td, "img.bin")
            size = 3 << 20
            with open(img, "wb") as f:
                f.write(b"\x00" * (size - 4096))
                f.write(fi.VERITY_FOOTER_MAGIC + b"\x01\x00\x00\x00"
                        + b"\xAA" * 4088)
            r = fi.scan_verity(img)
            self.assertTrue(r["verity_present"])
            self.assertEqual(r["footer_hits"][0]["offset"], size - 4096)
            self.assertEqual(r["image_size"], size)

    def test_no_footer(self):
        with tempfile.TemporaryDirectory() as td:
            img = os.path.join(td, "img.bin")
            with open(img, "wb") as f:
                f.write(b"\x00" * (1 << 20))
            r = fi.scan_verity(img)
            self.assertFalse(r["verity_present"])

    @unittest.skipUnless(shutil.which("mkfs.ext4"), "mkfs.ext4 not available")
    def test_ext4_trailing(self):
        with tempfile.TemporaryDirectory() as td:
            img = os.path.join(td, "fs.img")
            fs_bytes = 16 << 20
            subprocess.run(["mkfs.ext4", "-q", "-b", "4096", "-F", img, "4096"],
                           check=True)
            self.assertEqual(fi.ext4_fs_size(img), fs_bytes)
            with open(img, "ab") as f:
                f.write(b"\x00" * (64 << 10))          # fake hashtree area
            r = fi.scan_verity(img)
            self.assertEqual(r["ext4_fs_size"], fs_bytes)
            self.assertEqual(r["trailing_bytes"], 64 << 10)

    @unittest.skipUnless(shutil.which("mkfs.ext4"), "mkfs.ext4 not available")
    def test_full_trailing_scan_finds_mid_footer(self):
        with tempfile.TemporaryDirectory() as td:
            img = os.path.join(td, "fs.img")
            fs_bytes = 16 << 20
            subprocess.run(["mkfs.ext4", "-q", "-b", "4096", "-F", img, "4096"],
                           check=True)
            off = fs_bytes + (128 << 10)
            with open(img, "r+b") as f:
                f.seek(0, os.SEEK_END)
                f.write(b"\x00" * (1 << 20))       # 1 MiB trailing area
                f.seek(off)
                f.write(fi.VERITY_FOOTER_MAGIC + b"\x00" * 60)
            r = fi.scan_verity(img)
            self.assertTrue(r["verity_present"])
            self.assertIn(off, [h["offset"] for h in r["footer_hits"]])
            self.assertEqual(r["scanned_bytes"], 1 << 20)


class TestZipList(unittest.TestCase):
    def test_local_zip(self):
        with tempfile.TemporaryDirectory() as td:
            zp = os.path.join(td, "fw.zip")
            with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("dir/scatter.txt", "x" * 100)
                z.writestr("dir/boot.img", os.urandom(1000),
                           compress_type=zipfile.ZIP_STORED)
            out = fi.zip_list(zp)
            names = {e["name"]: e for e in out}
            self.assertEqual(names["dir/scatter.txt"]["uncompressed"], 100)
            self.assertEqual(names["dir/scatter.txt"]["method"], 8)
            self.assertEqual(names["dir/boot.img"]["method"], 0)
            self.assertEqual(names["dir/boot.img"]["compressed"], 1000)


class _capture:
    def __init__(self, buf):
        self.buf = buf

    def __enter__(self):
        self._old = sys.stdout
        sys.stdout = self.buf

    def __exit__(self, *a):
        sys.stdout = self._old


class TestCli(unittest.TestCase):
    def test_apps_cli_out(self):
        with tempfile.TemporaryDirectory() as td:
            make_apk(os.path.join(td, "app/A/A.apk"), "com.a")
            out = os.path.join(td, "rep", "apps.json")
            with _capture(io.StringIO()):
                rc = fi.main(["apps", td, "--out", out])
            self.assertEqual(rc, 0)
            with open(out) as f:
                data = json.load(f)
            self.assertEqual(data[0]["package"], "com.a")


class TestComponents(unittest.TestCase):
    def test_components_axml(self):
        r = fi.components_axml(build_manifest_components())
        self.assertEqual(r["package"], "com.example.test")
        by = {}
        for c in r["components"]:
            by.setdefault(c["el"], []).append(c)
        self.assertEqual(by["application"][0]["name"], "com.example.MyApp")
        self.assertIn("service", by)
        self.assertEqual(by["service"][0]["name"], ".EvilService")
        self.assertEqual(by["receiver"][0]["name"], ".BootRecv")
        self.assertEqual(by["uses-permission"][0]["name"],
                         "android.permission.INSTALL_PACKAGES")
        md = by["meta-data"][0]
        self.assertEqual(md["name"], "cfgKey")
        self.assertEqual(md["value"], "cfgVal")
        # non-forensic attrs (label) are deliberately NOT extracted
        self.assertNotIn("label", by["application"][0])

    def test_components_apk(self):
        with tempfile.TemporaryDirectory() as td:
            apk = os.path.join(td, "a.apk")
            with zipfile.ZipFile(apk, "w") as z:
                z.writestr("AndroidManifest.xml",
                           build_manifest_components())
            r = fi.components_apk(apk)
        self.assertEqual(r["package"], "com.example.test")
        names = {c.get("name") for c in r["components"]}
        self.assertIn(".EvilService", names)

    def test_components_apk_broken(self):
        with tempfile.TemporaryDirectory() as td:
            apk = os.path.join(td, "b.apk")
            with zipfile.ZipFile(apk, "w") as z:
                z.writestr("AndroidManifest.xml", b"\x00" * 16)
            r = fi.components_apk(apk)
        self.assertIn("parse_error", r)
        self.assertEqual(r["components"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
