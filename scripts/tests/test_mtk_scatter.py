#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for scripts/mtk_scatter.py (no network)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import mtk_scatter as ms  # noqa: E402

SCATTER = """\
################################################################
#  General Setting
################################################################
- general: MTK_PLATFORM_CFG
  info:
    - config_version: V1.1.2
      platform: MT6735M
      project: tek6735m_65_d_n
      storage: EMMC
      boot_channel: MSDC_0
      block_size: 0x20000
################################################################
#  Layout Setting
################################################################
- partition_index: SYS0
  partition_name: preloader
  file_name: preloader_tek6735m_65_d_n.bin
  is_download: true
  type: SV5_BL_BIN
  partition_size: 0x40000
  region: EMMC_BOOT_1
  operation_type: BOOTLOADERS

- partition_index: SYS1
  partition_name: nvram
  file_name: NONE
  is_download: false
  partition_size: 0x500000
  region: EMMC_USER
  operation_type: BINREGION

- partition_index: SYS2
  partition_name: tee1
  file_name: trustzone.bin
  is_download: true
  partition_size: 0x500000
  region: EMMC_USER

- partition_index: SYS3
  partition_name: tee2
  file_name: trustzone.bin
  is_download: true
  partition_size: 0x500000
  region: EMMC_USER

- partition_index: SYS4
  partition_name: system
  file_name: system.img
  is_download: true
  partition_size: 0x100000
  region: EMMC_USER
"""


class TestParse(unittest.TestCase):
    def setUp(self):
        self.sc = ms.parse_scatter(SCATTER)

    def test_general(self):
        g = self.sc["general"]
        self.assertEqual(g.get("platform"), "MT6735M")
        self.assertEqual(g.get("project"), "tek6735m_65_d_n")
        self.assertEqual(g.get("config_version"), "V1.1.2")
        self.assertEqual(g.get("block_size"), "0x20000")

    def test_partition_count(self):
        self.assertEqual(len(self.sc["partitions"]), 5)

    def test_partition_fields(self):
        p0 = self.sc["partitions"][0]
        self.assertEqual(p0.name, "preloader")
        self.assertEqual(p0.file_name, "preloader_tek6735m_65_d_n.bin")
        self.assertTrue(p0.is_download)
        self.assertEqual(p0.size_bytes(), 0x40000)

    def test_non_download_none(self):
        nvram = self.sc["partitions"][1]
        self.assertEqual(nvram.name, "nvram")
        self.assertFalse(nvram.is_download)
        self.assertFalse(nvram.needs_file())  # file_name NONE


class TestRequiredFiles(unittest.TestCase):
    def test_unique_required(self):
        sc = ms.parse_scatter(SCATTER)
        req = ms.required_files(sc)
        # trustzone.bin appears for tee1+tee2 but must be listed once.
        self.assertEqual(req, ["preloader_tek6735m_65_d_n.bin",
                               "trustzone.bin", "system.img"])

    def test_none_and_non_download_excluded(self):
        sc = ms.parse_scatter(SCATTER)
        req = ms.required_files(sc)
        self.assertNotIn("NONE", req)


class TestValidate(unittest.TestCase):
    def _write(self, d, name, size):
        with open(os.path.join(d, name), "wb") as f:
            f.write(b"\x00" * size)

    def test_complete_ok(self):
        sc = ms.parse_scatter(SCATTER)
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "preloader_tek6735m_65_d_n.bin", 100)
            self._write(d, "trustzone.bin", 100)
            self._write(d, "system.img", 100)  # < 0x100000
            res = ms.validate_package(sc, d)
            self.assertTrue(res["ok"])
            self.assertEqual(len(res["present"]), 3)
            self.assertEqual(res["missing"], [])

    def test_missing_detected(self):
        sc = ms.parse_scatter(SCATTER)
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "preloader_tek6735m_65_d_n.bin", 100)
            # trustzone.bin and system.img absent
            res = ms.validate_package(sc, d)
            self.assertFalse(res["ok"])
            self.assertIn("trustzone.bin", res["missing"])
            self.assertIn("system.img", res["missing"])

    def test_oversized_detected(self):
        sc = ms.parse_scatter(SCATTER)
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "preloader_tek6735m_65_d_n.bin", 100)
            self._write(d, "trustzone.bin", 100)
            self._write(d, "system.img", 0x100000 + 1)  # 1 byte over 1 MiB cap
            res = ms.validate_package(sc, d)
            self.assertFalse(res["ok"])
            self.assertEqual(len(res["oversized"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
