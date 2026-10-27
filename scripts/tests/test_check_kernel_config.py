#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for kernel/check-kernel-config.py."""

from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
_KERNEL = os.path.join(_REPO, "kernel")
_SPEC = importlib.util.spec_from_file_location(
    "check_kernel_config", os.path.join(_KERNEL, "check-kernel-config.py"))
ck = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ck)

FRAGMENT = os.path.join(_KERNEL, "u2_8.1_config.fragment")


class TestLoaders(unittest.TestCase):
    def test_fragment_parses(self):
        reqs = ck.load_fragment(FRAGMENT)
        self.assertGreater(len(reqs), 20, "fragment should list many options")
        names = dict(reqs)
        # A few options we know the fragment mandates.
        self.assertIn("CONFIG_ZRAM", names)
        self.assertEqual(names["CONFIG_ZRAM"], "y")
        self.assertIn("CONFIG_ANDROID_BINDER_IPC", names)

    def test_load_assignments_ignores_comments(self):
        with tempfile.NamedTemporaryFile("w", suffix=".config", delete=False) as f:
            f.write("# comment\nCONFIG_FOO=y\n\nCONFIG_BAR=m\n")
            path = f.name
        try:
            got = ck.load_assignments(path)
            self.assertEqual(got, {"CONFIG_FOO": "y", "CONFIG_BAR": "m"})
        finally:
            os.unlink(path)


class TestAudit(unittest.TestCase):
    def _write_config_from_fragment(self, drop=None, override=None):
        """Build a .config that satisfies the fragment, optionally dropping one."""
        lines = []
        for name, val in ck.load_fragment(FRAGMENT):
            if drop and name == drop:
                continue
            if override and name in override:
                lines.append(f"{name}={override[name]}")
            else:
                lines.append(f"{name}={val}")
        fd, path = tempfile.mkstemp(suffix=".config")
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(lines) + "\n")
        return path

    def test_full_config_passes(self):
        path = self._write_config_from_fragment()
        try:
            self.assertEqual(ck.main([path, "--fragment", FRAGMENT]), 0)
        finally:
            os.unlink(path)

    def test_missing_option_fails(self):
        path = self._write_config_from_fragment(drop="CONFIG_ZRAM")
        try:
            self.assertEqual(ck.main([path, "--fragment", FRAGMENT]), 1)
        finally:
            os.unlink(path)

    def test_mismatched_value_fails(self):
        # CONFIG_ANDROID_BINDER_DEVICES is a string; changing it must fail.
        path = self._write_config_from_fragment(
            override={"CONFIG_ANDROID_BINDER_DEVICES": '"binder"'})
        try:
            self.assertEqual(ck.main([path, "--fragment", FRAGMENT]), 1)
        finally:
            os.unlink(path)

    def test_y_vs_m_is_tolerated(self):
        # DM_VERITY is "=m" in the fragment; a "=y" kernel should still pass.
        path = self._write_config_from_fragment(override={"CONFIG_DM_VERITY": "y"})
        try:
            self.assertEqual(ck.main([path, "--fragment", FRAGMENT]), 0)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
