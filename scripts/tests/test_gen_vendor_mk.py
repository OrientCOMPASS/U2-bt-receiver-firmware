#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for scripts/gen-vendor-mk.py (blob-list parsing + makefile gen)."""

from __future__ import annotations

import importlib.util
import os
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
_SPEC = importlib.util.spec_from_file_location(
    "gen_vendor_mk", os.path.join(_SCRIPTS, "gen-vendor-mk.py"))
gv = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gv)


class TestParseList(unittest.TestCase):
    def test_strips_comments_and_blanks(self):
        text = (
            "# a comment\n"
            "\n"
            "vendor/lib/foo.so\n"
            "   \n"
            "vendor/bin/bar   # trailing comment\n"
        )
        self.assertEqual(gv.parse_list(text),
                         ["vendor/lib/foo.so", "vendor/bin/bar"])

    def test_inline_comment_removed(self):
        self.assertEqual(gv.parse_list("a/b.so  # note\n"), ["a/b.so"])


class TestClassify(unittest.TestCase):
    def test_shared_library(self):
        e = gv.classify("vendor/lib/libfoo.so")
        self.assertEqual(e.kind, "lib")
        self.assertEqual(e.module, "libfoo")
        self.assertTrue(e.vendor)
        self.assertFalse(e.hw)

    def test_colliding_name_is_renamed_with_stem(self):
        # libion collides with system/core/libion -- renamed module, original
        # install filename preserved via LOCAL_MODULE_STEM.
        e = gv.classify("vendor/lib/libion.so")
        self.assertEqual(e.kind, "lib")
        self.assertEqual(e.module, "libion_u2")
        self.assertEqual(e.stem, "libion.so")

    def test_non_colliding_name_has_no_stem(self):
        e = gv.classify("vendor/lib/libaed.so")
        self.assertEqual(e.module, "libaed")
        self.assertEqual(e.stem, "")

    def test_egl_library(self):
        e = gv.classify("vendor/lib/egl/libGLES_mali.so")
        self.assertEqual(e.kind, "lib")
        self.assertTrue(e.egl)
        self.assertFalse(e.hw)

    def test_lib64_shim_is_data_not_prebuilt(self):
        # The 64-bit runtime shim must go through PRODUCT_COPY_FILES, never
        # BUILD_PREBUILT (product is 32-bit-only).
        e = gv.classify("lib64/libc.so")
        self.assertEqual(e.kind, "data")
        e = gv.classify("bin/linker64")
        self.assertEqual(e.kind, "data")

    def test_hw_library(self):
        e = gv.classify("vendor/lib/hw/audio.primary.mt6735m.so")
        self.assertEqual(e.kind, "lib")
        self.assertEqual(e.module, "audio.primary.mt6735m")
        self.assertTrue(e.hw)
        self.assertTrue(e.vendor)

    def test_executable(self):
        e = gv.classify("vendor/bin/wmt_loader")
        self.assertEqual(e.kind, "bin")
        self.assertEqual(e.module, "wmt_loader")

    def test_firmware_is_data(self):
        e = gv.classify("etc/firmware/WMT_SOC.cfg")
        self.assertEqual(e.kind, "data")
        self.assertEqual(e.module, "")

    def test_extensionless_firmware_is_data_not_bin(self):
        # WIFI_RAM_CODE_6735 has no dot but is under etc/firmware, not bin/.
        e = gv.classify("etc/firmware/WIFI_RAM_CODE_6735")
        self.assertEqual(e.kind, "data")

    def test_directory_entry_is_data(self):
        e = gv.classify("vendor/etc/audio_param")
        self.assertEqual(e.kind, "data")

    def test_leading_slash_normalised(self):
        e = gv.classify("/vendor/lib/libfoo.so")
        self.assertEqual(e.relpath, "vendor/lib/libfoo.so")


class TestGeneration(unittest.TestCase):
    def setUp(self):
        self.paths = [
            "vendor/lib/hw/gralloc.mt6735m.so",
            "vendor/lib/libfoo.so",
            "vendor/bin/wmt_loader",
            "etc/firmware/WMT_SOC.cfg",
        ]
        self.lib_e, self.bin_e, self.data_e = gv.build_entries(self.paths)

    def test_counts(self):
        self.assertEqual(len(self.lib_e), 2)
        self.assertEqual(len(self.bin_e), 1)
        self.assertEqual(len(self.data_e), 1)

    def test_android_mk_has_modules_and_prebuilt(self):
        mk = gv.gen_android_mk(self.lib_e, self.bin_e, self.data_e)
        self.assertIn("LOCAL_MODULE := gralloc.mt6735m", mk)
        self.assertIn("LOCAL_MODULE := libfoo", mk)
        self.assertIn("LOCAL_MODULE := wmt_loader", mk)
        self.assertIn("include $(BUILD_PREBUILT)", mk)
        # hw lib gets the relative path, plain lib does not
        self.assertIn("LOCAL_MODULE_RELATIVE_PATH := hw", mk)
        # data file appears only as a comment, never as a module
        self.assertIn("#   etc/firmware/WMT_SOC.cfg", mk)
        self.assertNotIn("LOCAL_MODULE := WMT_SOC", mk)

    def test_blobs_mk_lists_every_module(self):
        mk = gv.gen_blobs_mk(self.lib_e, self.bin_e, self.data_e)
        self.assertIn("PRODUCT_PACKAGES +=", mk)
        for mod in ("gralloc.mt6735m", "libfoo", "wmt_loader"):
            self.assertIn(mod, mk)
        # data file is a comment note, not a package
        self.assertIn("#   etc/firmware/WMT_SOC.cfg", mk)

    def test_last_package_has_no_trailing_backslash(self):
        mk = gv.gen_blobs_mk(self.lib_e, self.bin_e, self.data_e)
        # The final module line must not end with a line-continuation backslash.
        pkg_lines = [l for l in mk.splitlines()
                     if l.startswith("    ") and not l.startswith("    #")]
        self.assertTrue(pkg_lines, "expected at least one package line")
        self.assertFalse(pkg_lines[-1].rstrip().endswith("\\"),
                         f"last package line ends with backslash: {pkg_lines[-1]!r}")


class TestRealList(unittest.TestCase):
    """The committed proprietary-files.txt must classify into a sane mix."""

    def test_real_file_generates(self):
        paths = gv.parse_list(gv.read(gv.LIST_FILE))
        lib_e, bin_e, data_e = gv.build_entries(paths)
        self.assertGreater(len(lib_e), 20, "expected many prebuilt libraries")
        self.assertGreaterEqual(len(bin_e), 5, "expected several executables")
        self.assertGreater(len(data_e), 5, "expected firmware/config data")
        # No module names should be empty or contain slashes.
        for e in lib_e + bin_e:
            self.assertTrue(e.module)
            self.assertNotIn("/", e.module)
        # Generated text is non-trivial.
        self.assertIn("BUILD_PREBUILT",
                      gv.gen_android_mk(lib_e, bin_e, data_e))


if __name__ == "__main__":
    unittest.main(verbosity=2)
