#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for scripts/publish_release.py pure helpers + dry-run (no network)."""

from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
_SPEC = importlib.util.spec_from_file_location(
    "publish_release", os.path.join(_SCRIPTS, "publish_release.py"))
pr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pr)


class TestUploadEndpoint(unittest.TestCase):
    def test_strips_template_and_adds_name(self):
        tmpl = "https://uploads.github.com/repos/o/r/releases/5/assets{?name,label}"
        got = pr.upload_endpoint(tmpl, "U2_AOSP81.zip")
        self.assertEqual(got,
                         "https://uploads.github.com/repos/o/r/releases/5/assets"
                         "?name=U2_AOSP81.zip")

    def test_url_encodes_name(self):
        tmpl = "https://x/assets{?name,label}"
        got = pr.upload_endpoint(tmpl, "a b+c.zip")
        self.assertIn("name=a+b%2Bc.zip", got)

    def test_existing_query_uses_amp(self):
        tmpl = "https://x/assets?foo=1{&name}"
        got = pr.upload_endpoint(tmpl, "f.zip")
        self.assertTrue(got.startswith("https://x/assets?foo=1&name=f.zip"))


class TestAssetIdForName(unittest.TestCase):
    def test_finds_existing(self):
        assets = [{"id": 7, "name": "a.zip"}, {"id": 9, "name": "b.zip"}]
        self.assertEqual(pr.asset_id_for_name(assets, "b.zip"), 9)

    def test_missing_returns_none(self):
        self.assertIsNone(pr.asset_id_for_name([{"id": 1, "name": "a"}], "z"))


class TestPlan(unittest.TestCase):
    def test_ok(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"x" * 123)
            path = f.name
        try:
            self.assertEqual(pr.plan([path]), [(path, 123)])
        finally:
            os.unlink(path)

    def test_missing_raises(self):
        with self.assertRaises(FileNotFoundError):
            pr.plan(["/no/such/file.bin"])

    def test_empty_raises(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = f.name  # zero bytes
        try:
            with self.assertRaises(ValueError):
                pr.plan([path])
        finally:
            os.unlink(path)


class TestDryRunMain(unittest.TestCase):
    def test_dry_run_returns_zero(self):
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            f.write(b"PK\x03\x04" + b"\x00" * 100)
            path = f.name
        try:
            rc = pr.main(["--tag", "vtest", "--files", path, "--dry-run"])
            self.assertEqual(rc, 0)
        finally:
            os.unlink(path)

    def test_dry_run_missing_file_returns_one(self):
        rc = pr.main(["--tag", "vtest", "--files", "/no/such.zip", "--dry-run"])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
