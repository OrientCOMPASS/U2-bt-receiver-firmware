#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mtk_bootimg.py -- unpack/repack Android boot.img (header v0, MTK layout).

Why this exists
---------------
The B1 "nswap" firmware keeps the stock 7.0 system and replaces only
/system/lib/hw/bluetooth.default.so with the 8.1-built stack.  The stock
ramdisk's fstab mounts /system with the ",verify" fs_mgr flag (dm-verity),
so a modified system image would fail verification and bootloop.  This tool
repacks the stock boot.img with the ",verify" flag removed from the /system
fstab line, then re-signs the result with the **public AOSP testkey** -- the
same key the ODM used (the X.509 certificate embedded in the stock signature
tail is the AOSP testkey: CN=Android, android@android.com).  The signature
reproduces SignApk's output shape: CMS SignedData, sha1WithRSAEncryption,
no signed attributes, content detached, appended after the page-aligned
image body.

Boot header v0 (all little-endian, page_size almost always 2048 on MTK N):
  magic[8] "ANDROID!"
  kernel_size  kernel_addr  ramdisk_size  ramdisk_addr
  second_size  second_addr  tags_addr  page_size  dt_size  unused[2]
  name[16]  cmdline[512]  id[32]  extra_cmdline[1024]
Body layout: page-aligned header, kernel pages, ramdisk pages (, second
pages, dt pages).  MTK ODM builds append the CMS signature tail after the
body; loaders here ignore trailing bytes.

Subcommands
-----------
  unpack IN OUTDIR
      writes header.json, kernel.bin, ramdisk.cpio.gz, tail.der (if any)

  disable-verity IN OUT --pk8 K.pk8 --x509 C.pem [--no-sign] [--fstab-glob G]
      full pipeline: unpack -> edit every fstab* file in the ramdisk (drop
      the verify flag from /system lines) -> repack -> re-sign -> self-check.

  adb-enable IN OUT --pk8 K.pk8 --x509 C.pem [--no-sign] [--rc-file F]
                 [--assert-kernel-md5 MD5]
      debug/forensics boot: flip the four adb gate props in default.prop
      (ro.debuggable=1, ro.adb.secure=0, ro.secure=0,
      persist.sys.usb.config=mtp,adb) and append an adbd keepalive trigger
      to the MTK usb rc -> repack -> re-sign -> self-check.  Used by
      fw-debugboot.yml to make a hung first boot diagnosable over USB.

Requires: gzip, cpio (GNU), openssl (for signing).  Uses sudo transparently
when not running as root (ramdisk files are root-owned).
"""

from __future__ import annotations

import argparse
import contextlib
import glob
import gzip
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Tuple

HDR_MAGIC = b"ANDROID!"
# magic[8] + 11 uint32 (kernel_size, kernel_addr, ramdisk_size, ramdisk_addr,
# second_size, second_addr, tags_addr, page_size, dt_size, unused[2])
# + name[16] + cmdline[512] + id[32] + extra_cmdline[1024] = 1636 bytes.
HDR_FMT = "<8s11I16s512s32s1024s"
HDR_SIZE = struct.calcsize(HDR_FMT)
OFF_RAMDISK_SIZE = 8 + 4 * 2   # byte offset of ramdisk_size in the header


def log(msg: str) -> None:
    print(f"[mtk_bootimg] {msg}")


def err(msg: str) -> None:
    print(f"[mtk_bootimg] ERROR: {msg}", file=sys.stderr)


def _sudo() -> List[str]:
    return [] if os.geteuid() == 0 else ["sudo"]


def _run(cmd: List[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, **kw)


def _sudo_read(path: str) -> bytes:
    """Read a file that may be root-owned (extracted ramdisk entries)."""
    return subprocess.run(_sudo() + ["cat", path], check=True,
                          capture_output=True).stdout


def _sudo_write(path: str, data: bytes) -> None:
    """Write a file that may be root-owned (extracted ramdisk entries)."""
    subprocess.run(_sudo() + ["tee", path], input=data, check=True,
                   stdout=subprocess.DEVNULL)


@contextlib.contextmanager
def _workdir(prefix: str = "mtkboot"):
    """Temp dir whose contents may end up root-owned; cleaned via sudo."""
    td = tempfile.mkdtemp(prefix=prefix)
    try:
        yield td
    finally:
        sudocmd = _sudo()
        if sudocmd:
            subprocess.run(sudocmd + ["rm", "-rf", td],
                           capture_output=True, check=False)
        else:
            shutil.rmtree(td, ignore_errors=True)


# --------------------------------------------------------------------------- #
# parsing / building
# --------------------------------------------------------------------------- #
def parse_boot(data: bytes) -> Dict[str, object]:
    if data[:8] != HDR_MAGIC:
        raise ValueError("not an ANDROID! boot image")
    f = struct.unpack_from(HDR_FMT, data, 0)
    ksize, kaddr, rsize, raddr = f[1], f[2], f[3], f[4]
    ssize, saddr, tags, pagesize, dtsize = f[5], f[6], f[7], f[8], f[9]
    name, cmdline, img_id, extra = f[12], f[13], f[14], f[15]
    if pagesize == 0:
        raise ValueError("page_size = 0")
    if ssize or dtsize:
        raise ValueError(f"unsupported: second_size={ssize} dt_size={dtsize} "
                         "(this tool handles kernel+ramdisk only)")

    def pages(n: int) -> int:
        return (n + pagesize - 1) // pagesize * pagesize

    koff = pagesize
    roff = koff + pages(ksize)
    body_end = roff + pages(rsize)
    if len(data) < body_end:
        raise ValueError(f"file too small: {len(data)} < {body_end}")
    return {
        "kernel_size": ksize, "kernel_addr": kaddr,
        "ramdisk_size": rsize, "ramdisk_addr": raddr,
        "second_addr": saddr, "tags_addr": tags,
        "page_size": pagesize, "dt_size": dtsize,
        "name": name, "cmdline": cmdline, "id": img_id, "extra_cmdline": extra,
        "header_raw": data[:pagesize],   # pristine header page, patched on rebuild
        "kernel": data[koff:koff + ksize],
        "ramdisk": data[roff:roff + rsize],
        "tail": data[body_end:],
        "body_end": body_end,
    }


def build_boot(h: Dict[str, object], kernel: bytes, ramdisk: bytes,
               tail: bytes = b"") -> bytes:
    """Rebuild from the PRISTINE header page (only ramdisk_size is patched),
    so name/cmdline/id/extra and any MTK-specific header bytes survive
    verbatim -- no struct repacking, no field drift."""
    pagesize = int(h["page_size"])  # type: ignore[arg-type]

    def pages(n: int) -> int:
        return (n + pagesize - 1) // pagesize * pagesize

    hdr = bytearray(h["header_raw"])  # type: ignore[arg-type]
    struct.pack_into("<I", hdr, OFF_RAMDISK_SIZE, len(ramdisk))
    body = bytes(hdr)
    body += kernel + b"\0" * (pages(len(kernel)) - len(kernel))
    if ramdisk:
        body += ramdisk + b"\0" * (pages(len(ramdisk)) - len(ramdisk))
    return body + tail


# --------------------------------------------------------------------------- #
# ramdisk (cpio) editing
# --------------------------------------------------------------------------- #
def extract_ramdisk(ramdisk_gz: bytes, dest_dir: str) -> None:
    os.makedirs(dest_dir, exist_ok=True)
    p1 = subprocess.Popen(["gzip", "-dc"], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE)
    p2 = subprocess.Popen(_sudo() + ["cpio", "-idm", "--quiet"],
                          stdin=p1.stdout, cwd=dest_dir)
    assert p1.stdout is not None and p1.stdin is not None
    p1.stdout.close()
    p1.stdin.write(ramdisk_gz)
    p1.stdin.close()
    p1.wait()
    if p2.wait() != 0:
        raise RuntimeError("cpio extract failed")


def parse_cpio_index(ramdisk_gz: bytes) -> List[Tuple[str, int]]:
    """Walk a newc cpio archive; return [(name, mtime_seconds), ...].

    cpio extraction cannot perfectly restore timestamps (directory mtimes
    are bumped by later child creation; symlink mtimes are not restored on
    some systems), so we read the original mtimes from the archive and
    re-apply them explicitly before repacking -- otherwise the rebuilt
    ramdisk is not reproducible.
    """
    raw = gzip.decompress(ramdisk_gz)
    out: List[Tuple[str, int]] = []
    off = 0

    def pad4(n: int) -> int:
        return (n + 3) // 4 * 4

    while off + 110 <= len(raw):
        hdr = raw[off:off + 110]
        if hdr[:6] != b"070701":
            break
        fields = [int(hdr[6 + 8 * i:14 + 8 * i], 16) for i in range(13)]
        mtime, fsz, namesz = fields[5], fields[6], fields[11]
        name = raw[off + 110:off + 110 + namesz - 1].decode(
            "utf-8", "surrogateescape")
        if name == "TRAILER!!!":
            break
        out.append((name, mtime))
        off = pad4(pad4(off + 110 + namesz) + fsz)
    return out


_RESTORE_SCRIPT = (
    "import os, sys\n"
    "for line in open(sys.argv[1], encoding='utf-8', errors='surrogateescape'):\n"
    "    mt, path = line.rstrip('\\n').split('\\t', 1)\n"
    "    ns = int(mt) * 10**9\n"
    "    try:\n"
    "        os.utime(os.path.join(sys.argv[2], path), ns=(ns, ns),\n"
    "                 follow_symlinks=False)\n"
    "    except OSError:\n"
    "        pass\n"
)


def restore_mtimes(src_dir: str, entries: List[Tuple[str, int]]) -> None:
    """Re-apply archived mtimes, deepest paths first (children before the
    directories whose mtime their creation would bump)."""
    ordered = sorted(entries, key=lambda t: (-t[0].count("/"), t[0]))
    with tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False,
                                     encoding="utf-8",
                                     errors="surrogateescape") as f:
        tsv = f.name
        for name, mt in ordered:
            f.write(f"{mt}\t{name}\n")
    try:
        subprocess.run(_sudo() + ["python3", "-c", _RESTORE_SCRIPT,
                                  tsv, src_dir], check=True)
    finally:
        os.unlink(tsv)


class _BytesSink:
    """Minimal file-object shim collecting gzip output in memory."""

    def __init__(self) -> None:
        self.out: List[bytes] = []

    def write(self, b: bytes) -> int:
        self.out.append(b)
        return len(b)


def repack_ramdisk(src_dir: str) -> bytes:
    # Sorted file list for deterministic output; find and cpio run under sudo
    # when needed (extracted entries are root-owned, some dirs not traversable
    # by the invoking user).  -R root:root normalises ownership in the archive.
    files = subprocess.run(_sudo() + ["find", ".", "-mindepth", "1"],
                           cwd=src_dir, check=True, capture_output=True,
                           text=True)
    listing = "\n".join(sorted(files.stdout.splitlines())) + "\n"

    import threading
    sink = _BytesSink()
    gz = gzip.GzipFile(mode="wb", fileobj=sink, mtime=0)
    p1 = subprocess.Popen(_sudo() + ["cpio", "-o", "-H", "newc", "--quiet",
                                     "--renumber-inodes",
                                     "-R", "root:root"],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          cwd=src_dir)

    def _pump() -> None:
        assert p1.stdout is not None
        while True:
            b = p1.stdout.read(65536)
            if not b:
                break
            gz.write(b)
        gz.close()

    t = threading.Thread(target=_pump)
    t.start()
    assert p1.stdin is not None
    p1.stdin.write(listing.encode())
    p1.stdin.close()
    t.join()
    if p1.wait() != 0:
        raise RuntimeError("cpio create failed")
    return b"".join(sink.out)


def edit_fstab_disable_verity(ramdisk_dir: str, fstab_glob: str) -> int:
    """Drop the fs_mgr 'verify' flag from /system lines of every fstab file.

    Returns the number of edited lines.  Handles ',verify' / 'verify,' and a
    lone 'verify' flag token.
    """
    edits = 0
    for path in glob.glob(os.path.join(ramdisk_dir, fstab_glob)):
        if not os.path.isfile(path):
            continue
        raw = _sudo_read(path).decode("utf-8", "surrogateescape")
        lines = raw.splitlines(keepends=True)
        changed = False
        for i, line in enumerate(lines):
            if "/system" not in line or "verify" not in line:
                continue
            # Flag forms in the wild: "wait,verify" / "verify,check" / " verify ".
            new = (line.replace(",verify", "")
                       .replace("verify,", "")
                       .replace(" verify ", " "))
            if new != line:
                lines[i] = new
                changed = True
                edits += 1
                log(f"fstab edit {os.path.basename(path)}: "
                    f"{line.strip()!r} -> {new.strip()!r}")
        if changed:
            _sudo_write(path, "".join(lines).encode("utf-8", "surrogateescape"))
    return edits


# --------------------------------------------------------------------------- #
# signing (CMS SignedData, sha1, no attrs, detached content) -- SignApk shape
# --------------------------------------------------------------------------- #
def sign_body(body: bytes, pk8: str, x509: str) -> bytes:
    with tempfile.TemporaryDirectory() as td:
        body_f = os.path.join(td, "body")
        tail_f = os.path.join(td, "tail.der")
        with open(body_f, "wb") as f:
            f.write(body)
        _run(["openssl", "cms", "-sign", "-binary",
              "-in", body_f, "-outform", "DER", "-out", tail_f,
              "-signer", x509,
              "-inkey", pk8, "-keyform", "DER",
              "-noattr", "-md", "sha1"])
        with open(tail_f, "rb") as f:
            return f.read()


def check_tail_parses(tail: bytes) -> bool:
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "t.der")
        with open(p, "wb") as f:
            f.write(tail)
        r = subprocess.run(["openssl", "asn1parse", "-inform", "DER",
                            "-in", p], capture_output=True)
        return r.returncode == 0


# --------------------------------------------------------------------------- #
# subcommands
# --------------------------------------------------------------------------- #
def cmd_unpack(args: argparse.Namespace) -> int:
    data = open(args.image, "rb").read()
    h = parse_boot(data)
    os.makedirs(args.outdir, exist_ok=True)
    with open(os.path.join(args.outdir, "kernel.bin"), "wb") as f:
        f.write(h["kernel"])                                    # type: ignore
    with open(os.path.join(args.outdir, "ramdisk.cpio.gz"), "wb") as f:
        f.write(h["ramdisk"])                                    # type: ignore
    if h["tail"]:
        with open(os.path.join(args.outdir, "tail.der"), "wb") as f:
            f.write(h["tail"])                                   # type: ignore
    meta = {k: (v.hex() if isinstance(v, bytes) else v)
            for k, v in h.items()
            if k not in ("kernel", "ramdisk", "tail", "header_raw")}
    with open(os.path.join(args.outdir, "header.json"), "w") as f:
        json.dump(meta, f, indent=1)
    log(f"unpacked: kernel={h['kernel_size']}B ramdisk={h['ramdisk_size']}B "
        f"tail={len(h['tail'])}B page={h['page_size']}")  # type: ignore
    return 0


def cmd_disable_verity(args: argparse.Namespace) -> int:
    data = open(args.image, "rb").read()
    h = parse_boot(data)
    kernel = bytes(h["kernel"])                                  # type: ignore
    ramdisk_gz = bytes(h["ramdisk"])                             # type: ignore

    with _workdir() as td:
        rd = os.path.join(td, "rd")
        extract_ramdisk(ramdisk_gz, rd)
        edits = edit_fstab_disable_verity(rd, args.fstab_glob)
        if edits == 0:
            log("WARNING: no /system 'verify' flag found in any fstab -- "
                "repacking anyway")
        restore_mtimes(rd, parse_cpio_index(ramdisk_gz))
        new_ramdisk = repack_ramdisk(rd)

    body = build_boot(h, kernel, new_ramdisk)
    if args.no_sign:
        tail = b""
        log("--no-sign: signature tail omitted")
    else:
        tail = sign_body(body, args.pk8, args.x509)
        log(f"signed with {os.path.basename(args.x509)} "
            f"(CMS sha1WithRSA, tail={len(tail)}B)")
    out = body + tail

    # ---- self-check ------------------------------------------------------- #
    h2 = parse_boot(out)
    assert bytes(h2["kernel"]) == kernel, "kernel mismatch after rebuild"
    assert h2["ramdisk_size"] == len(new_ramdisk), "ramdisk size mismatch"
    assert h2["page_size"] == h["page_size"], "page size changed"
    with _workdir() as td:
        rd = os.path.join(td, "rd2")
        extract_ramdisk(bytes(h2["ramdisk"]), rd)               # type: ignore
        leftovers = 0
        for path in glob.glob(os.path.join(rd, args.fstab_glob)):
            txt = _sudo_read(path).decode("utf-8", "surrogateescape")
            for line in txt.splitlines():
                if "/system" in line and "verify" in line:
                    leftovers += 1
        assert leftovers == 0, "verify flag survived the rebuild"
        if not args.no_sign:
            assert check_tail_parses(tail), "signature tail not valid DER"
    with open(args.out, "wb") as f:
        f.write(out)
    log(f"wrote {args.out}: {len(out)}B "
        f"(kernel={len(kernel)}B ramdisk {h['ramdisk_size']}B -> "
        f"{len(new_ramdisk)}B, tail={len(tail)}B)")
    return 0


def cmd_sign(args: argparse.Namespace) -> int:
    """Append a CMS signature tail (stock shape) to an existing boot image.

    Used for the v1 u2-target build: mkbootimg output carries no signature
    tail, while the ODM stock boot is CMS-signed with the public AOSP
    testkey.  If lk enforces signature presence/verification, an unsigned
    boot would be rejected -- signing reproduces the stock shape.  Any
    existing tail is stripped first so signing is idempotent.
    """
    data = open(args.image, "rb").read()
    h = parse_boot(data)
    body = data[:int(h["body_end"])]  # type: ignore[arg-type]
    tail = sign_body(body, args.pk8, args.x509)
    out = body + tail
    h2 = parse_boot(out)
    assert bytes(h2["kernel"]) == bytes(h["kernel"]), "kernel changed"     # type: ignore
    assert bytes(h2["ramdisk"]) == bytes(h["ramdisk"]), "ramdisk changed"  # type: ignore
    assert check_tail_parses(tail), "signature tail not valid DER"
    with open(args.out, "wb") as f:
        f.write(out)
    log(f"wrote {args.out}: {len(out)}B "
        f"(body={len(body)}B, tail={len(tail)}B, "
        f"old tail {len(data) - len(body)}B replaced)")
    return 0


# --------------------------------------------------------------------------- #
# adb-enable -- debug/forensics boot image (used by fw-debugboot.yml)
# --------------------------------------------------------------------------- #
# Fresh-/data user builds ship Settings.Global.ADB_ENABLED=0 and
# ro.debuggable=0 + ro.adb.secure=1, so adb is unreachable exactly when we
# need it most: a hung first boot.  This patch flips the four gate props and
# appends a keepalive trigger to the MTK usb rc, so adbd comes up from init
# even if system_server never reaches UsbDeviceManager.
ADB_PROPS = {
    "ro.debuggable": "1",
    "ro.adb.secure": "0",
    "ro.secure": "0",
    "persist.sys.usb.config": "mtp,adb",
}
ADB_RC_FILE = "init.mt6735.usb.rc"
ADB_RC_MARKER = "### repo-debug-boot adb keepalive ###"
ADB_RC_PATCH = """
# ### repo-debug-boot adb keepalive ###
# Debug/forensics boot patch (scripts/mtk_bootimg.py adb-enable).
# 1) Force the initial usb config at `on boot`, so the keepalive trigger
#    below fires even when system_server never reaches UsbDeviceManager --
#    exactly the hung-first-boot case this image exists to debug.
#    (init.mt6735.usb.rc's own `on boot` sets sys.usb.vid first: this block
#    is appended at the end of the same file, so parse order guarantees it.)
# 2) Whenever the framework asks for plain "mtp" (a user build with a fresh
#    /data has Settings.Global.ADB_ENABLED=0), force the composite mtp,adb
#    gadget and (re)start adbd.  sys.usb.state is deliberately NOT touched:
#    the stock #6.mtp trigger already set it to "mtp", which keeps
#    system_server's UsbDeviceManager satisfied -- no property ping-pong.
on boot
    setprop sys.usb.config mtp

on property:sys.usb.config=mtp
    start adbd
    write /sys/class/android_usb/android0/enable 0
    write /sys/class/android_usb/android0/idVendor ${sys.usb.vid}
    write /sys/class/android_usb/android0/idProduct 201D
    write /sys/class/android_usb/android0/functions mtp,adb
    write /sys/class/android_usb/android0/enable 1
"""

# On-device evidence (2026-10-30): with ONLY the patch above, the host saw
# `device unauthorized`.  adbd + gadget came up (so the rc keepalive works),
# but /system/build.prop's ro.adb.secure=1 overrides the default.prop flip
# (build.prop is loaded later; ro props are first-set-wins), and the on-screen
# confirmation dialog can never be answered while bootanim loops.  The fix
# that does NOT depend on any ro prop: seed the operator's host public key
# into /data/misc/adb/adb_keys from init (rc builtins run in the init domain,
# which owns /data setup), then restorecon so the file carries
# u:object_r:adb_data_file:s0 and adbd is permitted to read it.  adbd treats
# a key found in adb_keys as pre-authorized -- no dialog needed.
ADB_KEY_FILE = "adbkey.pub"   # seeded at the ramdisk root as /adbkey.pub
ADB_KEYSEED_MARKER = "### repo-debug-boot adb key seeding ###"

# MTK puts adbd in the BOOT RAMDISK (/sbin/adbd; this build has NO
# /system/bin/adbd at all).  Evidence from the on-device binary (2026-10-30):
#   * it reads BOTH /adb_keys (ramdisk root) AND /data/misc/adb/adb_keys;
#   * it contains NO reference to ro.adb.secure -- auth cannot be turned off
#     via any prop on this device (explains 'device unauthorized' despite the
#     default.prop flip);
#   * stock init.rc runs `restorecon /adb_keys`, and the stock sepolicy has
#     u:object_r:adb_keys_file:s0 -- the ramdisk-root key file is the
#     ODM-intended pre-authorization path.  Seed the operator's host key
#     there and adbd authorizes the host with no dialog and no /data access.
ADB_AUTHKEYS_FILE = "adb_keys"   # seeded at the ramdisk root as /adb_keys

# On-device evidence (2026-10-30, v4 boot): MTK's /sbin/adbd REFUSES to stay
# root even with ro.debuggable=1 + ro.secure=0 + service.adb.root=1 (`adb root`
# says "restarting adbd as root", then comes back as uid 2000).  But everything
# else needed for root work IS proven on the device:
#   * init rc edits run (adbd keepalive works; the init `copy` builtin seeded
#     /data/misc/adb/adb_keys as root);
#   * SELinux is Permissive (getenforce);
#   * shell-level adb works (log/dmesg readable, /data/local/tmp writable).
# So: give init a generic root executor -- if the operator pushes a script to
# /data/local/tmp/rootexec.sh (shell adb), init runs it AS ROOT on every boot
# at post-fs-data (after mount_all, so /system is mounted and can be remounted
# rw by the script).  Missing script => silent no-op.  Results are written to
# /data/local/tmp (chmod 666) and read back over shell adb.
ADB_ROOTEXEC_MARKER = "### repo-debug-boot root executor ###"
ADB_ROOTEXEC_RC = """
# ### repo-debug-boot root executor ###
# v6: service + start.  (v5 used the `exec` builtin at post-fs-data; on this
# MTK init it silently never ran -- no rootexec.log, no execve error -- even
# though a same-file same-trigger `copy` (keyseed) merged-action command DID
# run, proven by /data/misc/adb/adb_keys mtime.  Do not use `exec` here.)
# The service/start path is proven LIVE on the device: `setprop ctl.start
# preinstall` from adb shell (uid 2000) made init run /system/bin/preinstall.sh
# as uid 0 under Permissive (dmesg: "Starting service 'preinstall'" +
# transition/entrypoint avc denials with permissive=1 + "exited with status 0").
# Triggers, all no-op when /data/local/tmp/rootexec.sh is absent:
#   post-fs-data            -- every boot, early
#   sys.boot_completed=1    -- fallback if the early path ever fails
#   repo.exec=1             -- operator on-demand: adb shell setprop repo.exec 1
#                              (re-fire: setprop repo.exec 0; setprop repo.exec 1)
#   on boot                 -- parse/fire marker only (diagnostic ladder)
# v7: explicit seclabel.  v6 proved on-device that the property trigger fires
# and init finds the service, but Service::Start() aborted with "Service
# repoexec does not have a SELinux domain defined." -- AOSP 7.0 service.cpp
# computes security_compute_create(init_ctx, getfilecon(binary)) and rejects
# when the result equals init's own context (no transition rule).  Device
# labels: /system/bin/sh = shell_exec (NO init->shell transition on this
# modified MTK policy!), /system/bin/preinstall.sh = system_file (HAS one --
# which is why ctl.start preinstall ran as uid 0 in the shell domain).
# With an explicit seclabel, service.cpp uses it verbatim (no lookup):
#   if (!seclabel_.empty()) { scon = seclabel_; }
# u:r:shell:s0 replicates the proven preinstall execution shape (uid 0,
# shell domain, Permissive).
service repoexec /system/bin/sh /data/local/tmp/rootexec.sh
    disabled
    oneshot
    seclabel u:r:shell:s0

on boot
    write /dev/kmsg repo-rootexec-v6-onboot

on post-fs-data
    write /dev/kmsg repo-rootexec-v6-pfd
    start repoexec

on property:sys.boot_completed=1
    write /dev/kmsg repo-rootexec-v6-bc
    start repoexec

on property:repo.exec=1
    write /dev/kmsg repo-rootexec-v6-propexec
    start repoexec
"""
ADB_KEYSEED_RC = """
# ### repo-debug-boot adb key seeding ###
# Pre-authorize the operator's adb host key (notes: ADB_KEYSEED_MARKER in
# scripts/mtk_bootimg.py).  copy overwrites adb_keys with exactly this key --
# acceptable on a forensics image; runs on every boot (idempotent).
on post-fs-data
    mkdir /data/misc/adb 0770 system shell
    copy /adbkey.pub /data/misc/adb/adb_keys
    chown system shell /data/misc/adb/adb_keys
    chmod 0640 /data/misc/adb/adb_keys
    restorecon /data/misc/adb/adb_keys
"""


def edit_default_props(text: str, props: Dict[str, str]) -> Tuple[str, int]:
    """Set/replace `key=value` lines in default.prop text.

    Returns (new_text, n_props_applied).  Existing keys are replaced
    in-place (line order and every other byte preserved); missing keys are
    appended at the end.  Comment/blank lines are never touched.
    """
    lines = text.splitlines(keepends=True)
    remaining = dict(props)
    applied = 0
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key = s.split("=", 1)[0]
        if key in remaining:
            nl = "\n" if line.endswith("\n") else ""
            lines[i] = f"{key}={remaining.pop(key)}{nl}"
            applied += 1
    for k, v in remaining.items():
        lines.append(f"{k}={v}\n")
        applied += 1
    return "".join(lines), applied


def cmd_adb_enable(args: argparse.Namespace) -> int:
    import hashlib
    data = open(args.image, "rb").read()
    h = parse_boot(data)
    kernel = bytes(h["kernel"])                                  # type: ignore
    if args.assert_kernel_md5:
        got = hashlib.md5(kernel).hexdigest()
        want = args.assert_kernel_md5.strip().lower()
        assert got == want, f"kernel md5 {got} != expected {want}"
        log(f"kernel md5 OK ({got})")
    ramdisk_gz = bytes(h["ramdisk"])                             # type: ignore
    idx = parse_cpio_index(ramdisk_gz)
    names = {n for n, _ in idx}
    assert "default.prop" in names, "ramdisk has no default.prop"
    assert args.rc_file in names, f"ramdisk has no {args.rc_file}"

    key_line = ""
    if getattr(args, "adb_pubkey_file", ""):
        import re
        raw = open(args.adb_pubkey_file, "r", encoding="utf-8").read().strip()
        assert raw and "\n" not in raw, "pubkey file must hold ONE line"
        tok = raw.split()[0]
        assert re.fullmatch(r"[A-Za-z0-9+/=]{100,}", tok), \
            "pubkey does not look like an adbkey.pub base64 blob"
        key_line = raw
        log(f"adb pubkey: blob {len(tok)} chars, "
            f"comment {raw[len(tok):].strip() or '-'!r}")

    authkeys = ""
    if getattr(args, "adb_authkeys_file", ""):
        import re
        lines = [l.strip() for l in
                 open(args.adb_authkeys_file, "r", encoding="utf-8")
                 if l.strip()]
        for l in lines:
            tok = l.split()[0]
            assert re.fullmatch(r"[A-Za-z0-9+/=]{100,}", tok), \
                f"authkeys line does not look like an adbkey.pub blob: {l[:40]}..."
        authkeys = "\n".join(lines) + "\n"
        log(f"adb authkeys: {len(lines)} key line(s) for /adb_keys")

    with _workdir() as td:
        rd = os.path.join(td, "rd")
        extract_ramdisk(ramdisk_gz, rd)
        # 1) the four gate props in default.prop
        dp = os.path.join(rd, "default.prop")
        old = _sudo_read(dp).decode("utf-8", "surrogateescape")
        new, n = edit_default_props(old, ADB_PROPS)
        assert n == len(ADB_PROPS), f"set {n}/{len(ADB_PROPS)} props"
        _sudo_write(dp, new.encode("utf-8", "surrogateescape"))
        log(f"default.prop: {n} props flipped "
            f"({', '.join(sorted(ADB_PROPS))})")
        # 2) the usb rc keepalive trigger
        rcp = os.path.join(rd, args.rc_file)
        rct = _sudo_read(rcp).decode("utf-8", "surrogateescape")
        assert ADB_RC_MARKER not in rct, "rc file already patched"
        patch = ADB_RC_PATCH + (ADB_KEYSEED_RC if key_line else "") \
                + (ADB_ROOTEXEC_RC if getattr(args, "rootexec", False) else "")
        _sudo_write(rcp, (rct.rstrip("\n") + "\n" + patch)
                    .encode("utf-8", "surrogateescape"))
        log(f"{args.rc_file}: appended adb keepalive "
            f"(on-boot config + mtp->mtp,adb gadget force)"
            + (" + adb_keys seeding" if key_line else "")
            + (" + root executor" if getattr(args, "rootexec", False) else ""))
        # 3) the operator's host pubkey at the ramdisk root
        if key_line:
            kp = os.path.join(rd, ADB_KEY_FILE)
            _sudo_write(kp, (key_line + "\n").encode("utf-8"))
            _run(_sudo() + ["chmod", "0644", kp])
            log(f"/{ADB_KEY_FILE}: seeded ({len(key_line)} chars)")
        # 4) /adb_keys -- read directly by MTK's /sbin/adbd (ramdisk adbd);
        #    init.rc's `restorecon /adb_keys` gives it adb_keys_file label.
        if authkeys:
            akp = os.path.join(rd, ADB_AUTHKEYS_FILE)
            _sudo_write(akp, authkeys.encode("utf-8"))
            _run(_sudo() + ["chmod", "0444", akp])
            log(f"/{ADB_AUTHKEYS_FILE}: seeded ({len(authkeys)} bytes)")
        restore_mtimes(rd, idx)
        new_ramdisk = repack_ramdisk(rd)

    body = build_boot(h, kernel, new_ramdisk)
    if args.no_sign:
        tail = b""
        log("--no-sign: signature tail omitted")
    else:
        tail = sign_body(body, args.pk8, args.x509)
        log(f"signed with {os.path.basename(args.x509)} "
            f"(CMS sha1WithRSA, tail={len(tail)}B)")
    out = body + tail

    # ---- self-check: re-parse what we are about to write ------------------ #
    h2 = parse_boot(out)
    assert bytes(h2["kernel"]) == kernel, "kernel changed in rebuild"
    assert h2["page_size"] == h["page_size"], "page size changed"
    idx2 = parse_cpio_index(bytes(h2["ramdisk"]))                # type: ignore
    expect_names = names | ({ADB_KEY_FILE} if key_line else set()) \
                         | ({ADB_AUTHKEYS_FILE} if authkeys else set())
    assert {n for n, _ in idx2} == expect_names, "ramdisk file list changed"
    with _workdir() as td:
        rd = os.path.join(td, "rd2")
        extract_ramdisk(bytes(h2["ramdisk"]), rd)                # type: ignore
        dp2 = _sudo_read(os.path.join(rd, "default.prop")) \
            .decode("utf-8", "surrogateescape")
        got_props = {l.split("=", 1)[0]: l.split("=", 1)[1]
                     for l in dp2.splitlines()
                     if l.strip() and not l.startswith("#") and "=" in l}
        for k, v in ADB_PROPS.items():
            assert got_props.get(k) == v, f"prop {k}={got_props.get(k)!r} != {v!r}"
        rc2 = _sudo_read(os.path.join(rd, args.rc_file)) \
            .decode("utf-8", "surrogateescape")
        assert ADB_RC_MARKER in rc2, "rc marker vanished"
        assert "functions mtp,adb" in rc2 and "start adbd" in rc2, "rc patch broken"
        if getattr(args, "rootexec", False):
            assert ADB_ROOTEXEC_MARKER in rc2, "rootexec marker vanished"
            assert ("service repoexec /system/bin/sh "
                    "/data/local/tmp/rootexec.sh") in rc2, "repoexec service missing"
            assert rc2.count("start repoexec") == 3, "expected 3 start repoexec triggers"
            assert "on property:repo.exec=1" in rc2, "on-demand trigger missing"
            assert "    seclabel u:r:shell:s0\n" in rc2, \
                "v7 seclabel missing (service start would be rejected)"
            assert "\n    exec /system/bin/sh" not in rc2, \
                "v5 exec builtin must be gone (never ran on MTK init)"
            log("self-check: root executor v7 (service repoexec + seclabel + 4 triggers) verified")
        if key_line:
            assert ADB_KEYSEED_MARKER in rc2, "keyseed marker vanished"
            assert "copy /adbkey.pub /data/misc/adb/adb_keys" in rc2
            assert "restorecon /data/misc/adb/adb_keys" in rc2
            kp2 = _sudo_read(os.path.join(rd, ADB_KEY_FILE)) \
                .decode("utf-8").strip()
            assert kp2 == key_line, "seeded pubkey content mismatch"
            log("self-check: /adbkey.pub + keyseed rc verified in rebuilt ramdisk")
        if authkeys:
            ak2 = _sudo_read(os.path.join(rd, ADB_AUTHKEYS_FILE)) \
                .decode("utf-8")
            assert ak2 == authkeys, "/adb_keys content mismatch"
            log("self-check: /adb_keys verified in rebuilt ramdisk "
                f"({len(ak2)} bytes)")
        if not args.no_sign:
            assert check_tail_parses(tail), "signature tail not valid DER"
    with open(args.out, "wb") as f:
        f.write(out)
    log(f"wrote {args.out}: {len(out)}B (kernel={len(kernel)}B, ramdisk "
        f"{h['ramdisk_size']}B -> {len(new_ramdisk)}B, tail={len(tail)}B)")  # type: ignore
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    u = sub.add_parser("unpack", help="split a boot.img into parts")
    u.add_argument("image")
    u.add_argument("outdir")
    u.set_defaults(fn=cmd_unpack)

    d = sub.add_parser("disable-verity",
                       help="repack boot.img with the /system verify flag removed")
    d.add_argument("image")
    d.add_argument("out")
    d.add_argument("--pk8", help="signing key (DER PKCS#8, e.g. AOSP testkey.pk8)")
    d.add_argument("--x509", help="signing cert (PEM, e.g. AOSP testkey.x509.pem)")
    d.add_argument("--no-sign", action="store_true",
                   help="do not append a signature tail")
    d.add_argument("--fstab-glob", default="fstab*",
                   help="ramdisk fstab file glob (default: fstab*)")
    d.set_defaults(fn=cmd_disable_verity)

    s = sub.add_parser("sign",
                       help="append a stock-shape CMS signature tail to a boot image")
    s.add_argument("image")
    s.add_argument("out")
    s.add_argument("--pk8", required=True,
                   help="signing key (DER PKCS#8, e.g. AOSP testkey.pk8)")
    s.add_argument("--x509", required=True,
                   help="signing cert (PEM, e.g. AOSP testkey.x509.pem)")
    s.set_defaults(fn=cmd_sign)

    a = sub.add_parser("adb-enable",
                       help="repack boot.img with adb always on (debug/forensics image)")
    a.add_argument("image")
    a.add_argument("out")
    a.add_argument("--pk8", help="signing key (DER PKCS#8, e.g. AOSP testkey.pk8)")
    a.add_argument("--x509", help="signing cert (PEM, e.g. AOSP testkey.x509.pem)")
    a.add_argument("--no-sign", action="store_true",
                   help="do not append a signature tail (tests only)")
    a.add_argument("--rc-file", default=ADB_RC_FILE,
                   help=f"ramdisk usb rc to patch (default: {ADB_RC_FILE})")
    a.add_argument("--assert-kernel-md5", default="",
                   help="abort unless the kernel md5 equals this hex string")
    a.add_argument("--adb-pubkey-file", default="",
                   help="one-line adbkey.pub to seed into "
                        "/data/misc/adb/adb_keys at boot (pre-authorizes that "
                        "host with no on-screen dialog)")
    a.add_argument("--adb-authkeys-file", default="",
                   help="adb_keys file (one pubkey per line) seeded at the "
                        "ramdisk root as /adb_keys -- read directly by the "
                        "MTK ramdisk adbd; the reliable pre-authorization path")
    a.add_argument("--rootexec", action="store_true",
                   help="append the root-executor rc block (v6): service "
                        "repoexec + start triggers (post-fs-data, "
                        "sys.boot_completed=1, repo.exec=1) so init runs "
                        "/data/local/tmp/rootexec.sh AS ROOT (no-op if the "
                        "script is absent); on-demand: setprop repo.exec 1")
    a.set_defaults(fn=cmd_adb_enable)

    args = ap.parse_args(argv)
    if args.cmd in ("disable-verity", "adb-enable") and not args.no_sign:
        if not (args.pk8 and args.x509):
            ap.error("signing needs --pk8 and --x509 (or use --no-sign)")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
