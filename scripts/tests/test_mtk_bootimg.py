#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for scripts/mtk_bootimg.py.

Builds a synthetic header-v0 boot image whose ramdisk is a newc cpio
containing an fstab with a ',verify' flag on the /system line, runs the
disable-verity pipeline (signing with a throwaway openssl-generated key)
and checks kernel preservation, header fidelity, flag removal and tail
validity.  Skips gracefully when cpio/openssl/gzip are unavailable.
"""

from __future__ import annotations

import gzip
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import mtk_bootimg as mb  # noqa: E402

HAVE_CPIO = shutil.which("cpio") is not None
HAVE_OPENSSL = shutil.which("openssl") is not None

PAGE = 2048
FSTAB_LINE = ("/dev/block/platform/mtk-msdc.0/by-name/system /system ext4 "
              "ro wait,verify\n")
FSTAB_OTHER = ("/dev/block/platform/mtk-msdc.0/by-name/userdata /data ext4 "
               "noatime wait,check,forceencrypt=/dev/block/x\n")


def cpio_newc(files: dict) -> bytes:
    """Minimal GNU newc cpio writer: {name: (mode, bytes)} + trailer."""
    out = bytearray()
    ino = 300000
    for name, (mode, payload) in files.items():
        hdr = "070701" + "".join(
            f"{v:08X}" for v in (
                ino, mode, 0, 0, 1, 0o1466000000, len(payload),
                0, 0, 0, 0, len(name) + 1, 0))
        ino += 1
        rec = hdr.encode() + name.encode() + b"\0"
        rec += b"\0" * ((-len(rec)) % 4)
        out += rec + payload + b"\0" * ((-len(payload)) % 4)
    tr = ("070701" + "00000000" * 4 + "00000001" + "00000000" * 6
          + "0000000B" + "00000000")   # nlink=1, namesize=11 ("TRAILER!!!\0")
    rec = tr.encode() + b"TRAILER!!!\0"
    rec += b"\0" * ((-len(rec)) % 4)
    out += rec
    return bytes(out)


def build_synthetic_boot(kernel: bytes, ramdisk_files: dict) -> bytes:
    ramdisk = gzip.compress(cpio_newc(ramdisk_files))
    hdr = struct.pack(
        mb.HDR_FMT, mb.HDR_MAGIC,
        len(kernel), 0x40080000, len(ramdisk), 0x44000000,
        0, 0x40F00000, 0x4E000000, PAGE, 0, 0, 0,
        b"synthetic-name", b"bootopt=64S3,32N2,64N2", b"\x11" * 32,
        b"\0" * 1024)
    body = hdr + b"\0" * (PAGE - len(hdr))
    body += kernel + b"\0" * ((-len(kernel)) % PAGE)
    body += ramdisk + b"\0" * ((-len(ramdisk)) % PAGE)
    return body


@unittest.skipUnless(HAVE_CPIO and HAVE_OPENSSL, "needs cpio + openssl")
class TestDisableVerity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.kernel = os.urandom(50000)
        cls.boot = os.path.join(cls.tmp, "boot.img")
        with open(cls.boot, "wb") as f:
            f.write(build_synthetic_boot(cls.kernel, {
                "fstab.mt6735": (0o644 | 0o100000,
                                 (FSTAB_LINE + FSTAB_OTHER).encode()),
                "init": (0o755 | 0o100000, b"INITBINARY" * 10),
            }))
        # throwaway signing key (DER pk8 + PEM cert)
        pem_key = os.path.join(cls.tmp, "k.pem")
        cls.x509 = os.path.join(cls.tmp, "c.pem")
        cls.pk8 = os.path.join(cls.tmp, "k.pk8")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048",
                        "-keyout", pem_key, "-out", cls.x509, "-nodes",
                        "-days", "1", "-subj", "/CN=test"],
                       check=True, capture_output=True)
        subprocess.run(["openssl", "pkcs8", "-topk8", "-nocrypt",
                        "-inform", "PEM", "-in", pem_key,
                        "-outform", "DER", "-out", cls.pk8],
                       check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _run_cli(self, *args):
        r = subprocess.run([sys.executable,
                            os.path.join(_SCRIPTS, "mtk_bootimg.py"), *args],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        return r

    def test_unpack_roundtrip(self):
        outdir = os.path.join(self.tmp, "un")
        self._run_cli("unpack", self.boot, outdir)
        self.assertEqual(open(os.path.join(outdir, "kernel.bin"), "rb").read(),
                         self.kernel)
        self.assertTrue(os.path.isfile(os.path.join(outdir,
                                                    "ramdisk.cpio.gz")))

    def test_disable_verity_pipeline(self):
        out = os.path.join(self.tmp, "boot_nv.img")
        r = self._run_cli("disable-verity", self.boot, out,
                          "--pk8", self.pk8, "--x509", self.x509)
        self.assertIn("fstab edit", r.stdout)
        data = open(out, "rb").read()
        h = mb.parse_boot(data)
        self.assertEqual(h["kernel"], self.kernel)
        self.assertEqual(h["page_size"], PAGE)
        self.assertGreater(len(h["tail"]), 0)
        self.assertTrue(mb.check_tail_parses(bytes(h["tail"])))
        # extract the new ramdisk and check the fstab
        rd = os.path.join(self.tmp, "rd_check")
        mb.extract_ramdisk(bytes(h["ramdisk"]), rd)
        txt = open(os.path.join(rd, "fstab.mt6735")).read()
        self.assertNotIn("verify", txt.splitlines()[0])
        self.assertIn("wait", txt.splitlines()[0])
        self.assertIn("forceencrypt", txt)      # other lines untouched
        self.assertEqual(open(os.path.join(rd, "init"), "rb").read(),
                         b"INITBINARY" * 10)
        shutil.rmtree(rd, ignore_errors=True)

    def test_no_sign_variant(self):
        out = os.path.join(self.tmp, "boot_nosig.img")
        self._run_cli("disable-verity", self.boot, out, "--no-sign")
        h = mb.parse_boot(open(out, "rb").read())
        self.assertEqual(h["tail"], b"")
        self.assertEqual(h["kernel"], self.kernel)

    def test_sign_subcommand(self):
        # an unsigned image (like mkbootimg output from the v1 u2 build)
        # gains a stock-shape CMS tail; kernel/ramdisk untouched; idempotent
        unsigned = os.path.join(self.tmp, "boot_unsigned.img")
        self._run_cli("disable-verity", self.boot, unsigned, "--no-sign")
        signed = os.path.join(self.tmp, "boot_signed.img")
        self._run_cli("sign", unsigned, signed,
                      "--pk8", self.pk8, "--x509", self.x509)
        h = mb.parse_boot(open(signed, "rb").read())
        self.assertEqual(h["kernel"], self.kernel)
        self.assertGreater(len(h["tail"]), 0)
        self.assertTrue(mb.check_tail_parses(bytes(h["tail"])))
        resigned = os.path.join(self.tmp, "boot_resigned.img")
        self._run_cli("sign", signed, resigned,
                      "--pk8", self.pk8, "--x509", self.x509)
        h2 = mb.parse_boot(open(resigned, "rb").read())
        self.assertEqual(h2["kernel"], self.kernel)
        self.assertEqual(h2["ramdisk"], h["ramdisk"])
        self.assertEqual(len(h2["tail"]), len(h["tail"]))


class TestParseGuards(unittest.TestCase):
    def test_rejects_non_boot(self):
        with self.assertRaises(ValueError):
            mb.parse_boot(b"NOTABOOT" + b"\0" * 2048)

    def test_rejects_second_or_dt(self):
        # craft: set second_size != 0 in a valid synthetic image
        data = bytearray(build_synthetic_boot(b"K" * 10, {}))
        struct.pack_into("<I", data, 8 + 4 * 4, 42)   # second_size
        with self.assertRaises(ValueError):
            mb.parse_boot(bytes(data))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestEditDefaultProps(unittest.TestCase):
    TEXT = ("#\n# ADDITIONAL_DEFAULT_PROPERTIES\n#\n"
            "ro.adb.secure=1\nro.secure=1\nro.debuggable=0\n"
            "persist.sys.usb.config=mtp\n# comment=notaprop\n"
            "persist.sys.timezone=Asia/Shanghai\n")

    def test_replace_in_place(self):
        new, n = mb.edit_default_props(self.TEXT, {"ro.debuggable": "1",
                                                   "ro.secure": "0"})
        self.assertEqual(n, 2)
        lines = new.splitlines()
        self.assertEqual(lines, self.TEXT.splitlines()[:3]
                         + ["ro.adb.secure=1", "ro.secure=0",
                            "ro.debuggable=1", "persist.sys.usb.config=mtp",
                            "# comment=notaprop",
                            "persist.sys.timezone=Asia/Shanghai"])

    def test_append_missing(self):
        new, n = mb.edit_default_props("a=1\n", {"a": "2", "b": "3"})
        self.assertEqual(n, 2)
        self.assertEqual(new, "a=2\nb=3\n")

    def test_adb_props_all_applied_on_stock(self):
        new, n = mb.edit_default_props(self.TEXT, mb.ADB_PROPS)
        self.assertEqual(n, 4)
        for k, v in mb.ADB_PROPS.items():
            self.assertIn(f"{k}={v}", new.splitlines())

    def test_rc_patch_shape(self):
        p = mb.ADB_RC_PATCH
        self.assertIn(mb.ADB_RC_MARKER, p)
        self.assertIn("on boot\n    setprop sys.usb.config mtp", p)
        self.assertIn("on property:sys.usb.config=mtp\n", p)
        self.assertIn("start adbd", p)
        self.assertIn("functions mtp,adb", p)
        self.assertIn("idProduct 201D", p)
        # the keepalive must NOT rewrite sys.usb.state (no framework fight)
        action_lines = [l.strip() for l in p.splitlines()
                        if l.strip() and not l.strip().startswith("#")]
        self.assertFalse(any("sys.usb.state" in l for l in action_lines),
                         action_lines)


@unittest.skipUnless(HAVE_CPIO, "needs cpio")
class TestAdbEnable(unittest.TestCase):
    STOCK_PROPS = ("#\nro.adb.secure=1\nro.secure=1\nro.debuggable=0\n"
                   "persist.sys.usb.config=mtp\npersist.sys.timezone=X\n")
    STOCK_RC = ("on boot\n    setprop sys.usb.vid 0E8D\n\n"
                "#6.mtp\non property:sys.usb.config=mtp\n"
                "    write /sys/class/android_usb/android0/enable 0\n"
                "    setprop sys.usb.state ${sys.usb.config}\n")

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.kernel = os.urandom(40000)
        cls.boot = os.path.join(cls.tmp, "boot.img")
        with open(cls.boot, "wb") as f:
            f.write(build_synthetic_boot(cls.kernel, {
                "default.prop": (0o644 | 0o100000,
                                 cls.STOCK_PROPS.encode()),
                mb.ADB_RC_FILE: (0o644 | 0o100000, cls.STOCK_RC.encode()),
                "init": (0o755 | 0o100000, b"INIT" * 20),
            }))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _run(self, *args, expect_ok=True):
        r = subprocess.run([sys.executable,
                            os.path.join(_SCRIPTS, "mtk_bootimg.py"), *args],
                           capture_output=True, text=True)
        if expect_ok:
            self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        return r

    def test_adb_enable_no_sign(self):
        out = os.path.join(self.tmp, "boot_adb.img")
        self._run("adb-enable", self.boot, out, "--no-sign")
        h = mb.parse_boot(open(out, "rb").read())
        self.assertEqual(h["kernel"], self.kernel)
        self.assertEqual(h["tail"], b"")
        names_before = {n for n, _ in
                        mb.parse_cpio_index(_ramdisk_of(self.boot))}
        names_after = {n for n, _ in
                       mb.parse_cpio_index(bytes(h["ramdisk"]))}
        self.assertEqual(names_before, names_after)
        rd = os.path.join(self.tmp, "rd_adb")
        mb.extract_ramdisk(bytes(h["ramdisk"]), rd)
        props = open(os.path.join(rd, "default.prop")).read()
        self.assertIn("ro.debuggable=1", props)
        self.assertIn("ro.adb.secure=0", props)
        self.assertIn("ro.secure=0", props)
        self.assertIn("persist.sys.usb.config=mtp,adb", props)
        self.assertIn("persist.sys.timezone=X", props)   # others untouched
        rc = open(os.path.join(rd, mb.ADB_RC_FILE)).read()
        self.assertIn(mb.ADB_RC_MARKER, rc)
        self.assertIn("#6.mtp", rc)                      # stock kept
        self.assertTrue(rc.index(mb.ADB_RC_MARKER) > rc.index("#6.mtp"),
                        "keepalive must be appended AFTER the stock trigger")
        shutil.rmtree(rd, ignore_errors=True)

    def test_kernel_md5_gate(self):
        import hashlib
        good = hashlib.md5(self.kernel).hexdigest()
        out = os.path.join(self.tmp, "boot_adb2.img")
        self._run("adb-enable", self.boot, out, "--no-sign",
                  "--assert-kernel-md5", good)
        r = self._run("adb-enable", self.boot, out, "--no-sign",
                      "--assert-kernel-md5", "0" * 32, expect_ok=False)
        self.assertNotEqual(r.returncode, 0)

    def test_idempotence_guard(self):
        out1 = os.path.join(self.tmp, "boot_adb3.img")
        self._run("adb-enable", self.boot, out1, "--no-sign")
        r = self._run("adb-enable", out1, os.path.join(self.tmp, "x.img"),
                      "--no-sign", expect_ok=False)
        self.assertNotEqual(r.returncode, 0)   # already-patched rc is rejected


def _ramdisk_of(path: str) -> bytes:
    h = mb.parse_boot(open(path, "rb").read())
    return bytes(h["ramdisk"])
