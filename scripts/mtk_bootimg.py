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

Requires: gzip, cpio (GNU), openssl (for signing).  Uses sudo transparently
when not running as root (ramdisk files are root-owned).
"""

from __future__ import annotations

import argparse
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
    p1.stdout.close()
    p1.stdin.write(ramdisk_gz)
    p1.stdin.close()
    p1.wait()
    if p2.wait() != 0:
        raise RuntimeError("cpio extract failed")


class _BytesSink:
    """Minimal file-object shim collecting gzip output in memory."""

    def __init__(self) -> None:
        self.out: List[bytes] = []

    def write(self, b: bytes) -> int:
        self.out.append(b)
        return len(b)


def repack_ramdisk(src_dir: str) -> bytes:
    # Sorted file list for deterministic output; cpio runs under sudo when
    # needed so root-owned modes/owners are archived verbatim (-R root:root
    # additionally normalises ownership in the archive header).
    files = subprocess.run(["find", ".", "-mindepth", "1"], cwd=src_dir,
                           check=True, capture_output=True, text=True)
    listing = "\n".join(sorted(files.stdout.splitlines())) + "\n"

    import threading
    sink = _BytesSink()
    gz = gzip.GzipFile(mode="wb", fileobj=sink, mtime=0)
    p1 = subprocess.Popen(_sudo() + ["cpio", "-o", "-H", "newc", "--quiet",
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
        with open(path, "r", encoding="utf-8", errors="surrogateescape") as f:
            lines = f.readlines()
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
            with open(path, "w", encoding="utf-8",
                      errors="surrogateescape") as f:
                f.writelines(lines)
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

    with tempfile.TemporaryDirectory() as td:
        rd = os.path.join(td, "rd")
        extract_ramdisk(ramdisk_gz, rd)
        edits = edit_fstab_disable_verity(rd, args.fstab_glob)
        if edits == 0:
            log("WARNING: no /system 'verify' flag found in any fstab -- "
                "repacking anyway")
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
    with tempfile.TemporaryDirectory() as td:
        rd = os.path.join(td, "rd2")
        extract_ramdisk(bytes(h2["ramdisk"]), rd)               # type: ignore
        leftovers = 0
        for path in glob.glob(os.path.join(rd, args.fstab_glob)):
            txt = open(path, encoding="utf-8",
                         errors="surrogateescape").read()
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

    args = ap.parse_args(argv)
    if args.cmd == "disable-verity" and not args.no_sign:
        if not (args.pk8 and args.x509):
            ap.error("signing needs --pk8 and --x509 (or use --no-sign)")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
