#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for scripts/debloat_system.py (fake mounted-system tree)."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))
sys.path.insert(0, _HERE)

import debloat_system as ds  # noqa: E402
from test_fw_inventory import make_apk  # noqa: E402


def build_fake_system(root):
    make_apk(os.path.join(root, "app/Junk/Junk.apk"), "com.junk.app")
    make_apk(os.path.join(root, "app/Good/Good.apk"), "com.good.app")
    make_apk(os.path.join(root, "priv-app/Evil/Evil.apk"), "com.evil.app")
    os.makedirs(os.path.join(root, "app/Junk/oat/arm"))
    with open(os.path.join(root, "app/Junk/oat/arm/Junk.odex"), "wb") as f:
        f.write(b"\x00" * 4096)
    os.makedirs(os.path.join(root, "etc/permissions"))
    with open(os.path.join(root, "etc/permissions/junk-perms.xml"), "w") as f:
        f.write('<permissions><assign-permission package="com.junk.app" />'
                '</permissions>\n')
    with open(os.path.join(root, "etc/permissions/keep.xml"), "w") as f:
        f.write('<permissions><feature name="x" /></permissions>\n')


def write_list(path, lines):
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def run_main(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = ds.main(argv)
    return rc, buf.getvalue()


class TestDebloat(unittest.TestCase):
    def test_happy_path(self):
        with tempfile.TemporaryDirectory() as td:
            mnt = os.path.join(td, "sys")
            build_fake_system(mnt)
            lst = os.path.join(td, "list.txt")
            write_list(lst, ["# comment", "", "app/Junk", "priv-app/Evil"])
            out = os.path.join(td, "removed.json")
            rc, _ = run_main(["--mount", mnt, "--list", lst, "--json", out])
            self.assertEqual(rc, 0)
            self.assertFalse(os.path.exists(os.path.join(mnt, "app/Junk")))
            self.assertFalse(os.path.exists(os.path.join(mnt, "priv-app/Evil")))
            self.assertTrue(os.path.isdir(os.path.join(mnt, "app/Good")))
            with open(out) as f:
                rep = json.load(f)
            self.assertGreater(rep["freed_bytes"], 4096)
            pkgs = {p["package"] for p in rep["planned"]}
            self.assertEqual(pkgs, {"com.junk.app", "com.evil.app"})
            self.assertIn({"file": "etc/permissions/junk-perms.xml",
                           "package": "com.junk.app"}, rep["dangling_refs"])
            self.assertTrue(os.path.isfile(
                os.path.join(mnt, "etc/permissions/keep.xml")))

    def test_dry_run(self):
        with tempfile.TemporaryDirectory() as td:
            mnt = os.path.join(td, "sys")
            build_fake_system(mnt)
            lst = os.path.join(td, "list.txt")
            write_list(lst, ["app/Junk"])
            rc, _ = run_main(["--mount", mnt, "--list", lst, "--dry-run"])
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.isdir(os.path.join(mnt, "app/Junk")))

    def test_rejects_unsafe_paths(self):
        with tempfile.TemporaryDirectory() as td:
            mnt = os.path.join(td, "sys")
            build_fake_system(mnt)
            for bad in ("../etc", "etc/permissions", "app/Junk/oat",
                        "/app/Junk", "app/", "framework"):
                lst = os.path.join(td, "list.txt")
                write_list(lst, [bad])
                rc, _ = run_main(["--mount", mnt, "--list", lst])
                self.assertEqual(rc, 2, f"path {bad!r} must be rejected")
            self.assertTrue(os.path.isdir(os.path.join(mnt, "app/Junk")))

    def test_rejects_missing_target(self):
        with tempfile.TemporaryDirectory() as td:
            mnt = os.path.join(td, "sys")
            build_fake_system(mnt)
            lst = os.path.join(td, "list.txt")
            write_list(lst, ["app/NotThere"])
            rc, _ = run_main(["--mount", mnt, "--list", lst])
            self.assertEqual(rc, 2)

    def test_rejects_empty_list(self):
        with tempfile.TemporaryDirectory() as td:
            mnt = os.path.join(td, "sys")
            build_fake_system(mnt)
            lst = os.path.join(td, "list.txt")
            write_list(lst, ["# nothing", ""])
            rc, _ = run_main(["--mount", mnt, "--list", lst])
            self.assertEqual(rc, 2)

    def test_rejects_duplicates(self):
        with tempfile.TemporaryDirectory() as td:
            mnt = os.path.join(td, "sys")
            build_fake_system(mnt)
            lst = os.path.join(td, "list.txt")
            write_list(lst, ["app/Junk", "app/Junk"])
            rc, _ = run_main(["--mount", mnt, "--list", lst])
            self.assertEqual(rc, 2)

    def test_load_list_strips(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "l.txt")
            write_list(p, ["  app/A  ", "#c", ""])
            self.assertEqual(ds.load_list(p), ["app/A"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
