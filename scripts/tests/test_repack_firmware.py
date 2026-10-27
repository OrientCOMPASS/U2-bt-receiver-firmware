#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for scripts/repack_firmware.py (local synthetic data, no network).

The live HTTP-range fetch of real MTK-private partitions is exercised by the CI
repack dry-run; here we validate the assembly/validation/zip logic deterministically.
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

import mtk_scatter as ms       # noqa: E402
import repack_firmware as rp   # noqa: E402

SCATTER = """\
- general: MTK_PLATFORM_CFG
  info:
    - config_version: V1.1.2
      platform: MT6735M
      project: test
      storage: EMMC
      block_size: 0x20000
- partition_index: SYS0
  partition_name: preloader
  file_name: preloader.bin
  is_download: true
  partition_size: 0x40000
  region: EMMC_BOOT_1
- partition_index: SYS1
  partition_name: tee1
  file_name: trustzone.bin
  is_download: true
  partition_size: 0x100000
  region: EMMC_USER
- partition_index: SYS2
  partition_name: boot
  file_name: boot.img
  is_download: true
  partition_size: 0x1000000
  region: EMMC_USER
- partition_index: SYS3
  partition_name: system
  file_name: system.img
  is_download: true
  partition_size: 0x2000000
  region: EMMC_USER
"""
SCATTER_NAME = "MT6735M_Android_scatter.txt"


def _w(path, data=b"\x00"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


class RepackBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.private_dir = os.path.join(self.tmp, "private")
        self.build_out = os.path.join(self.tmp, "build")
        os.makedirs(self.private_dir)
        # scatter + private (reused) images
        _w(os.path.join(self.private_dir, SCATTER_NAME), SCATTER.encode())
        _w(os.path.join(self.private_dir, "preloader.bin"), b"PRE" * 100)
        _w(os.path.join(self.private_dir, "trustzone.bin"), b"TEE" * 100)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestAssembleBuilt(RepackBase):
    def test_built_images_used(self):
        _w(os.path.join(self.build_out, "boot.img"), b"BOOT" * 100)
        _w(os.path.join(self.build_out, "system.img"), b"SYS" * 100)
        pkgdir = os.path.join(self.tmp, "pkg", "PKG")
        rep = rp.assemble(None, self.private_dir, None, self.build_out,
                          False, pkgdir, "PKG")
        self.assertEqual(sorted(rep["built"]), ["boot.img", "system.img"])
        self.assertIn("preloader.bin", rep["private"])
        self.assertIn("trustzone.bin", rep["private"])
        # scatter kept its canonical name
        self.assertTrue(os.path.isfile(os.path.join(pkgdir, SCATTER_NAME)))
        for fn in ("preloader.bin", "trustzone.bin", "boot.img", "system.img"):
            self.assertTrue(os.path.isfile(os.path.join(pkgdir, fn)), fn)
        # built content came from build_out, not a dummy
        with open(os.path.join(pkgdir, "boot.img"), "rb") as f:
            self.assertEqual(f.read(), b"BOOT" * 100)
        # validates against the scatter
        sc = ms.load_scatter(os.path.join(pkgdir, SCATTER_NAME))
        self.assertTrue(ms.validate_package(sc, pkgdir)["ok"])


class TestAssembleDryRun(RepackBase):
    def test_dry_run_generates_dummies(self):
        pkgdir = os.path.join(self.tmp, "pkg", "PKG")
        rep = rp.assemble(None, self.private_dir, None, None,
                          True, pkgdir, "PKG")
        self.assertEqual(sorted(rep["dry"]), ["boot.img", "system.img"])
        self.assertEqual(rep["built"], [])
        for fn in ("boot.img", "system.img"):
            p = os.path.join(pkgdir, fn)
            self.assertTrue(os.path.isfile(p))
            self.assertEqual(os.path.getsize(p), rp.DRY_SIZES[fn])
        # private still real
        with open(os.path.join(pkgdir, "preloader.bin"), "rb") as f:
            self.assertEqual(f.read(), b"PRE" * 100)

    def test_missing_built_without_dry_run_raises(self):
        pkgdir = os.path.join(self.tmp, "pkg", "PKG")
        with self.assertRaises(FileNotFoundError):
            rp.assemble(None, self.private_dir, None, None,
                        False, pkgdir, "PKG")


class TestAssembleMissingPrivate(RepackBase):
    def test_missing_private_raises(self):
        os.remove(os.path.join(self.private_dir, "trustzone.bin"))
        _w(os.path.join(self.build_out, "boot.img"), b"B")
        _w(os.path.join(self.build_out, "system.img"), b"S")
        pkgdir = os.path.join(self.tmp, "pkg", "PKG")
        with self.assertRaises(FileNotFoundError):
            rp.assemble(None, self.private_dir, None, self.build_out,
                        False, pkgdir, "PKG")


class TestZipPackage(RepackBase):
    def test_zip_contains_pkg_folder(self):
        _w(os.path.join(self.build_out, "boot.img"), b"BOOT")
        _w(os.path.join(self.build_out, "system.img"), b"SYS")
        pkgdir = os.path.join(self.tmp, "pkg", "PKG")
        rp.assemble(None, self.private_dir, None, self.build_out,
                    False, pkgdir, "PKG")
        out_zip = os.path.join(self.tmp, "out.zip")
        size = rp.zip_package(pkgdir, out_zip)
        self.assertGreater(size, 0)
        with zipfile.ZipFile(out_zip) as z:
            names = z.namelist()
        self.assertIn(f"PKG/{SCATTER_NAME}", names)
        self.assertIn("PKG/boot.img", names)
        self.assertIn("PKG/system.img", names)
        self.assertIn("PKG/preloader.bin", names)
        self.assertIn("PKG/trustzone.bin", names)


class TestClassify(unittest.TestCase):
    def test_built_set(self):
        self.assertEqual(rp.BUILT, {"boot.img", "recovery.img", "system.img"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
