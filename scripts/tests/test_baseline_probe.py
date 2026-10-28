#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for scripts/baseline_probe.py.

Hermetic tests for the pure parsers (dirents, ELF32, superblock rejection,
DT_NEEDED closure with a stub image) plus an ext4 round-trip integration test
that runs only when mkfs.ext4/debugfs are available (they are on CI runners).
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
for _p in (_SCRIPTS, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import baseline_probe as bp  # noqa: E402
import test_mtk_sparse2raw as tms  # noqa: E402


def make_dirent(ino: int, name: str, ftype: int, pad_to: int) -> bytes:
    rec = struct.pack("<IHBB", ino, pad_to, len(name), ftype) + name.encode()
    return rec + b"\0" * (pad_to - len(rec))


class TestParseDirents(unittest.TestCase):
    def test_entries_and_terminator(self):
        block = b""
        block += make_dirent(11, ".", 2, 12)
        block += make_dirent(12, "..", 2, 12)
        block += make_dirent(13, "build.prop", 1, 24)
        block += make_dirent(14, "lib", 2, 16)
        block += make_dirent(0, "", 0, 12)          # terminator
        block += b"\0" * (4096 - len(block))
        got = bp.parse_dirents(block)
        self.assertEqual(got, [("build.prop", 13, 1), ("lib", 14, 2)])

    def test_truncated_tail_is_ignored(self):
        block = make_dirent(13, "x.so", 1, 16) + b"\x01\x00"   # < 8 bytes left
        got = bp.parse_dirents(block)
        self.assertEqual(got, [("x.so", 13, 1)])


def build_elf32(soname: str, needed: list, undef: list, exp: list) -> bytes:
    """Craft a minimal ELF32LE shared object with .dynamic/.dynstr/.dynsym."""
    dynstr = b"\0"
    offs = {}
    for s in [soname] + needed + undef + exp:
        if s and s not in offs:
            offs[s] = len(dynstr)
            dynstr += s.encode() + b"\0"
    dynsym = struct.pack("<IIIBBH", 0, 0, 0, 0, 0, 0)       # null entry
    for name in undef:
        dynsym += struct.pack("<IIIBBH", offs[name], 0, 0, 0x10, 0, 0)  # UND
    for name in exp:
        dynsym += struct.pack("<IIIBBH", offs[name], 4, 4, 0x11, 0, 1)  # GLOBAL
    dynamic = b""
    if soname:
        dynamic += struct.pack("<iI", bp.DT_SONAME, offs[soname])
    for n in needed:
        dynamic += struct.pack("<iI", bp.DT_NEEDED, offs[n])
    dynamic += struct.pack("<iI", 0, 0)                      # DT_NULL
    sec_names = [b".dynamic", b".dynstr", b".dynsym", b".shstrtab"]
    shstrtab = b"\0"
    name_off = []
    for n in sec_names:
        name_off.append(len(shstrtab))
        shstrtab += n + b"\0"

    eh_size, sh_size, nsec = 52, 40, 1 + len(sec_names)
    parts = [dynamic, dynstr, dynsym, shstrtab]
    cur = eh_size
    offsets = []
    for p in parts:
        offsets.append(cur)
        cur += len(p)
    e_shoff = cur
    total = e_shoff + nsec * sh_size
    blob = bytearray(total)
    blob[0:eh_size] = struct.pack(
        "<4sBBBBB7xHHIIIIIHHHHHH",
        b"\x7fELF", 1, 1, 1, 0, 0,
        3, 40,                    # ET_DYN, EM_ARM
        1, 0, 0, e_shoff, 0,      # version, entry, phoff, shoff, flags
        eh_size, 0, 0, sh_size, nsec, nsec - 1)
    for p, o in zip(parts, offsets):
        blob[o:o + len(p)] = p

    def sec(noff, typ, off, size, link=0, entsize=0):
        return struct.pack("<10I", noff, typ, 0x2, 0, off, size,
                           link, 0, 4, entsize)
    shs = b"\0" * sh_size                                       # NULL section
    shs += sec(name_off[0], 6, offsets[0], len(dynamic), link=2, entsize=8)
    shs += sec(name_off[1], 3, offsets[1], len(dynstr))
    shs += sec(name_off[2], 11, offsets[2], len(dynsym), link=2, entsize=16)
    shs += sec(name_off[3], 3, offsets[3], len(shstrtab))
    blob[e_shoff:e_shoff + len(shs)] = shs
    return bytes(blob)


class TestParseElf32(unittest.TestCase):
    def test_soname_needed_symbols(self):
        data = build_elf32("bluetooth.default.so", ["liblog.so", "libc.so"],
                           ["some_platform_fn", "another_fn"], ["hal_open"])
        info = bp.parse_elf32(data)
        self.assertEqual(info["soname"], "bluetooth.default.so")
        self.assertEqual(info["needed"], ["liblog.so", "libc.so"])
        self.assertEqual(info["n_undefined"], 2)
        self.assertEqual(info["n_exported"], 1)
        self.assertIn("some_platform_fn", info["undefined"])
        self.assertEqual(info["e_machine"], 40)         # EM_ARM

    def test_non_elf(self):
        self.assertEqual(bp.parse_elf32(b"not an elf at all"), {})


class TestSuperblockRejection(unittest.TestCase):
    def test_garbage_raises(self):
        with tempfile.NamedTemporaryFile(suffix=".img", delete=False) as t:
            t.write(b"\0" * 4096)
            path = t.name
        try:
            with self.assertRaises(bp.ProbeError):
                bp.Ext4Image(path)
        finally:
            os.unlink(path)

    def test_mtk_sparse_hint(self):
        with tempfile.NamedTemporaryFile(suffix=".img", delete=False) as t:
            t.write(struct.pack("<I", 0xED26FF3A) + b"\0" * 4096)
            path = t.name
        try:
            with self.assertRaises(bp.ProbeError) as ctx:
                bp.Ext4Image(path)
            self.assertIn("MTK-sparse", str(ctx.exception))
        finally:
            os.unlink(path)


class StubImage:
    """Stands in for Ext4Image in the closure test: path -> (ino, data)."""

    def __init__(self, files: dict):
        self.files = files
        self.data = {ino: d for ino, d in files.values()}

    def lookup(self, path, follow=True, _depth=0):
        hit = self.files.get(path)
        return hit[0] if hit else None

    def read_file(self, ino, max_bytes=None):
        return self.data[ino]


class TestTransitiveClosure(unittest.TestCase):
    def test_needed_chain_and_unresolved(self):
        lib_a = build_elf32("a.so", ["libb.so", "libghost.so"], ["u"], [])
        lib_b = build_elf32("libb.so", [], [], ["e"])
        img = StubImage({"/lib/a.so": (100, lib_a),
                         "/lib/libb.so": (101, lib_b)})
        libmap = {"libb.so": ("/lib/libb.so", 101, len(lib_b))}
        got = bp.transitive_closure(img, ["/lib/a.so"], libmap, {})
        self.assertEqual(got["/lib/a.so"]["soname"], "a.so")
        self.assertEqual(got["/lib/libb.so"]["soname"], "libb.so")
        self.assertIn("(unresolved) libghost.so", got)


class TestBtNameRegex(unittest.TestCase):
    def test_matches(self):
        for n in ("bluetooth.default.so", "libbluetooth_jni.so",
                  "libbt-vendor.so", "audio.a2dp.default.so",
                  "Bluetooth.apk", "bt_stack.conf", "libavrcp.so"):
            self.assertTrue(bp.BT_NAME_RE.search(n), n)

    def test_non_matches(self):
        for n in ("libcutils.so", "camera.default.so", "gps.mt6735.so",
                  "audio.primary.mt6735.so", "libutils.so"):
            self.assertFalse(bp.BT_NAME_RE.search(n), n)


class TestSparseGlue(unittest.TestCase):
    def test_desparse_roundtrip(self):
        raw = os.urandom(3 * 4096)
        sp = tms.build_sparse([(tms.RAW, raw)], block_size=4096)
        with tempfile.NamedTemporaryFile(suffix=".img", delete=False) as t:
            t.write(sp)
            spath = t.name
        try:
            self.assertTrue(bp.image_is_sparse(spath))
            rawpath, stats = bp.desparse(spath)
            try:
                with open(rawpath, "rb") as f:
                    self.assertEqual(f.read(), raw)
                self.assertEqual(stats["out_size"], len(raw))
            finally:
                os.unlink(rawpath)
        finally:
            os.unlink(spath)

    def test_plain_file_not_sparse(self):
        with tempfile.NamedTemporaryFile(suffix=".img", delete=False) as t:
            t.write(b"\0" * 64)
            path = t.name
        try:
            self.assertFalse(bp.image_is_sparse(path))
        finally:
            os.unlink(path)


@unittest.skipUnless(shutil.which("mkfs.ext4") and shutil.which("debugfs"),
                     "e2fsprogs not installed")
class TestExt4RoundTrip(unittest.TestCase):
    def test_walk_lookup_read(self):
        tmp = tempfile.mkdtemp(prefix="probe_it_")
        img = os.path.join(tmp, "t.img")
        try:
            subprocess.run(["mkfs.ext4", "-q", "-F", "-b", "4096", img, "16M"],
                           check=True, capture_output=True)
            with open(os.path.join(tmp, "build.prop"), "w", encoding="utf-8") as f:
                f.write("ro.build.version.release=7.0\nro.product.model=U2\n")
            payload = os.urandom(300_000)          # spans many blocks/extents
            with open(os.path.join(tmp, "payload.bin"), "wb") as f:
                f.write(payload)
            for cmd in ("mkdir /lib", "write %s/build.prop /build.prop" % tmp,
                        "write %s/payload.bin /lib/payload.bin" % tmp):
                subprocess.run(["debugfs", "-w", "-R", cmd, img],
                               check=True, capture_output=True)
            image = bp.Ext4Image(img)
            self.assertEqual(image.block_size, 4096)
            ino = image.lookup("/build.prop")
            self.assertIsNotNone(ino)
            text = image.read_file(ino).decode()
            self.assertIn("ro.product.model=U2", text)
            got = image.read_file(image.lookup("/lib/payload.bin"))
            self.assertEqual(got, payload)
            names = {n for n, _, _ in image.dir_entries(2)}
            self.assertIn("lib", names)
            self.assertNotIn(".", names)
            walked = [p for p, _, ft, _ in image.walk(2, "", 0, 8) if ft == 1]
            self.assertIn("/lib/payload.bin", walked)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
