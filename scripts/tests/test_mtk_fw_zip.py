#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for scripts/mtk_fw_zip.py using a synthetic LOCAL zip (no network).

The remote HTTP-range path is exercised live in CI's repack dry-run; here we
validate the central-directory parsing, stored/deflate extraction, size handling
and scatter discovery against a zip we build on the fly.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import mtk_fw_zip as mz  # noqa: E402

SCATTER_NAME = "FW/MT6735M_Android_scatter.txt"
SCATTER_BODY = b"- general: MTK_PLATFORM_CFG\n  platform: MT6735M\n"


def build_zip(path: str) -> None:
    with zipfile.ZipFile(path, "w") as z:
        # deflate-compressed entry
        z.writestr(zipfile.ZipInfo(SCATTER_NAME), SCATTER_BODY,
                   compress_type=zipfile.ZIP_DEFLATED)
        # stored (uncompressed) entry
        zi = zipfile.ZipInfo("FW/lk.bin")
        zi.compress_type = zipfile.ZIP_STORED
        z.writestr(zi, b"LKDATA" * 100)
        # a larger compressible entry
        z.writestr("FW/system.img", b"\x00" * 200000,
                   compress_type=zipfile.ZIP_DEFLATED)
        # a nested dir entry
        z.writestr("FW/sub/deep.bin", b"DEEP" * 10)


class TestRemoteZipLocal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.zip_path = os.path.join(cls.tmp, "fw.zip")
        build_zip(cls.zip_path)
        cls.rz = mz.RemoteZip(cls.zip_path)

    @classmethod
    def tearDownClass(cls):
        cls.rz.close()
        import shutil
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_size_matches_file(self):
        self.assertEqual(self.rz.src.size, os.path.getsize(self.zip_path))

    def test_lists_all_entries(self):
        names = set(self.rz.names())
        self.assertIn(SCATTER_NAME, names)
        self.assertIn("FW/lk.bin", names)
        self.assertIn("FW/system.img", names)
        self.assertIn("FW/sub/deep.bin", names)

    def test_read_deflated_entry(self):
        self.assertEqual(self.rz.read_entry(SCATTER_NAME), SCATTER_BODY)

    def test_read_stored_entry(self):
        self.assertEqual(self.rz.read_entry("FW/lk.bin"), b"LKDATA" * 100)

    def test_read_large_deflated_entry(self):
        data = self.rz.read_entry("FW/system.img")
        self.assertEqual(len(data), 200000)
        self.assertEqual(data, b"\x00" * 200000)

    def test_entry_sizes_correct(self):
        e = self.rz.entries["FW/system.img"]
        self.assertEqual(e.uncomp_size, 200000)
        self.assertLess(e.comp_size, 200000)  # it compressed
        self.assertEqual(e.method, 8)

    def test_find_scatter(self):
        self.assertEqual(self.rz.find_scatter(), SCATTER_NAME)

    def test_extract_to_disk(self):
        dest = os.path.join(self.tmp, "out", "lk.bin")
        n = self.rz.extract("FW/lk.bin", dest)
        self.assertEqual(n, 600)
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), b"LKDATA" * 100)

    def test_missing_entry_raises(self):
        with self.assertRaises(KeyError):
            self.rz.read_entry("FW/nope.bin")

    def test_extract_stream_deflated(self):
        dest = os.path.join(self.tmp, "stream_sys.img")
        n = self.rz.extract_stream("FW/system.img", dest, chunk=4096)
        self.assertEqual(n, 200000)
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), b"\x00" * 200000)

    def test_extract_stream_stored(self):
        dest = os.path.join(self.tmp, "stream_lk.bin")
        n = self.rz.extract_stream("FW/lk.bin", dest, chunk=100)
        self.assertEqual(n, 600)
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), b"LKDATA" * 100)

    def test_stream_matches_read_entry(self):
        dest = os.path.join(self.tmp, "stream_scat.txt")
        self.rz.extract_stream(SCATTER_NAME, dest, chunk=7)
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), self.rz.read_entry(SCATTER_NAME))

    def test_extract_stream_missing_raises(self):
        with self.assertRaises(KeyError):
            self.rz.extract_stream("FW/nope.bin", os.path.join(self.tmp, "x"))


class TestHuman(unittest.TestCase):
    def test_human_sizes(self):
        self.assertEqual(mz.human(512), "512B")
        self.assertTrue(mz.human(2048).endswith("KiB"))
        self.assertTrue(mz.human(5 * 1024 * 1024).endswith("MiB"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
