#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for scripts/make_stub_apk.py (debloat v3 operator stubs).

Covers: AXML structure walk (chunk sizes must sum to the root size), the
fw_inventory round trip (the same parser every CI inventory assert uses),
resource-map coverage rules, the zip container, optional jarsigner v1
signing, and the integrity of debloat/stub-list.txt.
"""

from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
_REPO = os.path.dirname(_SCRIPTS)
for p in (_SCRIPTS,):
    if p not in sys.path:
        sys.path.insert(0, p)

import make_stub_apk as ms  # noqa: E402
import fw_inventory as fi  # noqa: E402

HAVE_JARSIGNER = (shutil.which("jarsigner") is not None
                  and shutil.which("keytool") is not None)


def walk_axml(ax: bytes):
    """Yield (type, header_size, size, offset) for every chunk; assert that
    the sizes tile the file exactly (no gaps, no overlaps)."""
    root_t, root_h, root_s = struct.unpack_from("<HHI", ax, 0)
    assert root_t == ms.CHUNK_XML, f"root type 0x{root_t:04x}"
    assert root_s == len(ax), f"root size {root_s} != file {len(ax)}"
    off = root_h
    while off < root_s:
        t, h, s = struct.unpack_from("<HHI", ax, off)
        assert s >= h > 0, f"bad chunk hdr at {off}: h={h} s={s}"
        yield t, h, s, off
        off += s
    assert off == root_s, f"chunks end at {off}, root says {root_s}"


class TestManifestAxml(unittest.TestCase):
    def setUp(self):
        self.ax = ms.build_manifest_axml("com.example.stub", 7, "7.7",
                                         24, 24)

    def test_chunk_tiling_and_order(self):
        types = [t for t, _, _, _ in walk_axml(self.ax)]
        self.assertEqual(types[:4], [ms.CHUNK_STRING_POOL, ms.CHUNK_RES_MAP,
                                     ms.CHUNK_START_NS, ms.CHUNK_START_TAG])
        self.assertEqual(types[-1], ms.CHUNK_END_NS)
        self.assertIn(ms.CHUNK_END_TAG, types)

    def test_resmap_covers_leading_pool(self):
        # resource-map ids must map 1:1 onto the FIRST K pool strings
        chunks = list(walk_axml(self.ax))
        pool_off = chunks[0][3]
        n = struct.unpack_from("<I", self.ax, pool_off + 8)[0]
        rm_off = chunks[1][3]
        k = (chunks[1][2] - 8) // 4
        self.assertLessEqual(k, n)
        ids = [struct.unpack_from("<I", self.ax, rm_off + 8 + i * 4)[0]
               for i in range(k)]
        self.assertEqual(ids, [0x0101021B, 0x0101021C, 0x0101020C,
                               0x01010270, 0x0101000C])

    def test_fw_inventory_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            apk = os.path.join(td, "s.apk")
            ms.write_apk(apk, self.ax)
            m = fi.manifest_apk(apk)
        self.assertEqual(m["package"], "com.example.stub")
        self.assertEqual(m["versionCode"], 7)
        self.assertEqual(m["versionName"], "7.7")
        self.assertIn(m.get("parse_error", None), (None, ""))

    def test_unicode_package_ok(self):
        ax = ms.build_manifest_axml("uni.UNI557FDDF", 1, "1.0", 24, 24)
        list(walk_axml(ax))   # structure valid
        self.assertLess(len(ax), 1500)


class TestApkContainer(unittest.TestCase):
    def test_zip_and_size(self):
        with tempfile.TemporaryDirectory() as td:
            apk = os.path.join(td, "s.apk")
            r = subprocess.run([sys.executable,
                                os.path.join(_SCRIPTS, "make_stub_apk.py"),
                                apk, "--package", "com.quark.browser"],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            with zipfile.ZipFile(apk) as z:
                self.assertEqual(z.namelist(), ["AndroidManifest.xml"])
                self.assertIsNone(z.testzip())
            self.assertLess(os.path.getsize(apk), 8192)


@unittest.skipUnless(HAVE_JARSIGNER, "needs jarsigner + keytool")
class TestSigning(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.ks = os.path.join(cls.tmp, "stub.jks")
        subprocess.run(["keytool", "-genkeypair", "-keystore", cls.ks,
                        "-storepass", "android", "-alias", "stubkey",
                        "-keyalg", "RSA", "-keysize", "2048",
                        "-validity", "9125", "-dname", "CN=stub-test"],
                       check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_signed_stub_verifies(self):
        apk = os.path.join(self.tmp, "signed.apk")
        r = subprocess.run([sys.executable,
                            os.path.join(_SCRIPTS, "make_stub_apk.py"),
                            apk, "--package", "com.hiby.music",
                            "--keystore", self.ks, "--storepass", "android",
                            "--alias", "stubkey"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with zipfile.ZipFile(apk) as z:
            names = z.namelist()
            self.assertIn("AndroidManifest.xml", names)
            self.assertTrue(any(n.startswith("META-INF/") and
                                n.upper().endswith((".SF", ".RSA", ".DSA"))
                                for n in names), names)
        v = subprocess.run(["jarsigner", "-verify", apk],
                           capture_output=True, text=True)
        self.assertEqual(v.returncode, 0, v.stdout + v.stderr)
        self.assertIn("jar verified", v.stdout)
        # still parses after signing
        m = fi.manifest_apk(apk)
        self.assertEqual(m["package"], "com.hiby.music")


class TestStubList(unittest.TestCase):
    EXPECTED = {
        "vendor/operator/app/HiBy/HiBy.apk": "com.hiby.music",
        "vendor/operator/app/WDJ/WDJ.apk": "com.wandoujia.phoenix2",
        "vendor/operator/app/kuake/kuake.apk": "com.quark.browser",
        "vendor/operator/app/mxsq/mxsq.apk": "uni.UNI557FDDF",
        "vendor/operator/app/wanneglianbo/wanneglianbo.apk": "com.iqiyi.wlanplay",
        "vendor/operator/app/xunfeishurufa/xunfeishurufa.apk": "com.iflytek.inputmethod",
    }

    def test_stub_list_matches_probe_v8(self):
        path = os.path.join(_REPO, "debloat", "stub-list.txt")
        got = {}
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            rel, pkg = line.split()
            got[rel] = pkg
        self.assertEqual(got, self.EXPECTED)
        for rel in got:
            self.assertRegex(rel, r"^vendor/operator/app/[^/]+/[^/]+\.apk$")

    def test_remove_list_no_longer_deletes_payload(self):
        path = os.path.join(_REPO, "debloat", "remove-list.txt")
        active = [l.strip() for l in open(path, encoding="utf-8")
                  if l.strip() and not l.strip().startswith("#")]
        self.assertEqual(len(active), 7)
        self.assertFalse(any("vendor/operator" in a for a in active), active)


if __name__ == "__main__":
    unittest.main()
