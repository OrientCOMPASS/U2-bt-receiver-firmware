#!/usr/bin/env python3
"""bt_symbol_gate.py -- B3 link-feasibility gate for the Nougat BT swap.

Compares the undefined dynamic symbols of a freshly compiled module (our
7.0-ported bluetooth.default.so) against everything the BASELINE firmware can
provide at runtime:

    baseline set  = every defined dynamic symbol of every *.so extracted from
                    the official system image (B1 artifact
                    `baseline-bt-extracted`)
    sysroot set   = every defined dynamic symbol of the NDK stub libs
                    (libc/libm/libdl/liblog/libz/libstdc++/libandroid ...)

Gate rule (NOUGAT_BT_SWAP.md B3, "link zero-gap"):

    strong UND(ours)  is a subset of  baseline U sysroot   -> exit 0
    anything missing                                       -> exit 1 + report

Weak undefined symbols are listed but never fail the gate (they bind to 0 at
runtime by design -- optional hooks).  Symbol version suffixes (@VER / @@VER)
are stripped.  Pure standard library (struct-based ELF32/ELF64 .dynsym walk,
same approach as scripts/baseline_probe.py).

Usage:
    bt_symbol_gate.py --ours out/bluetooth.default.so \
        --baseline-dir DIR [--baseline-dir DIR2 ...] \
        --sysroot-lib DIR [--sysroot-lib DIR2 ...] \
        [--allowlist FILE] [--out report.json]
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
from typing import List, Optional, Set, Tuple

SHT_DYNSYM = 11
STB_LOCAL, STB_GLOBAL, STB_WEAK = 0, 1, 2
SHN_UNDEF = 0


class Elf:
    """Minimal ELF32/64 reader exposing the .dynsym table."""

    def __init__(self, path: str):
        with open(path, "rb") as f:
            self.data = f.read()
        d = self.data
        if d[:4] != b"\x7fELF":
            raise ValueError(f"{path}: not an ELF")
        self.cls = d[4]  # 1 = 32-bit, 2 = 64-bit
        self.endian = "<" if d[5] == 1 else ">"
        e = self.endian
        if self.cls == 1:
            (self.e_shoff,) = struct.unpack_from(e + "I", d, 0x20)
            (self.e_shentsize,) = struct.unpack_from(e + "H", d, 0x2E)
            (self.e_shnum,) = struct.unpack_from(e + "H", d, 0x30)
            (self.e_shstrndx,) = struct.unpack_from(e + "H", d, 0x32)
        else:
            (self.e_shoff,) = struct.unpack_from(e + "Q", d, 0x28)
            (self.e_shentsize,) = struct.unpack_from(e + "H", d, 0x3A)
            (self.e_shnum,) = struct.unpack_from(e + "H", d, 0x3C)
            (self.e_shstrndx,) = struct.unpack_from(e + "H", d, 0x3E)

    def _sections(self) -> List[Tuple[str, int, int, int, int]]:
        d, e = self.data, self.endian
        hdr = self.e_shoff + self.e_shstrndx * self.e_shentsize
        if self.cls == 1:
            (shstr_off,) = struct.unpack_from(e + "I", d, hdr + 0x10)
        else:
            (shstr_off,) = struct.unpack_from(e + "Q", d, hdr + 0x18)

        def secname(nameoff: int) -> str:
            end = d.index(b"\0", shstr_off + nameoff)
            return d[shstr_off + nameoff:end].decode("utf-8", "replace")

        out = []
        for i in range(self.e_shnum):
            off = self.e_shoff + i * self.e_shentsize
            if self.cls == 1:
                nm, sh_type = struct.unpack_from(e + "II", d, off)
                sh_offset, sh_size = struct.unpack_from(e + "II", d, off + 0x10)
                (sh_link,) = struct.unpack_from(e + "I", d, off + 0x18)
            else:
                nm, sh_type = struct.unpack_from(e + "II", d, off)
                sh_offset, sh_size = struct.unpack_from(e + "QQ", d, off + 0x18)
                (sh_link,) = struct.unpack_from(e + "I", d, off + 0x28)
            out.append((secname(nm), sh_type, sh_offset, sh_size, sh_link))
        return out

    def dynsyms(self) -> List[Tuple[str, int, int]]:
        """(name, bind, shndx) for every .dynsym entry."""
        d, e = self.data, self.endian
        secs = self._sections()
        for _name, sh_type, sh_offset, sh_size, sh_link in secs:
            if sh_type != SHT_DYNSYM:
                continue
            strtab_off = secs[sh_link][2]
            entsize = 16 if self.cls == 1 else 24
            out = []
            for i in range(sh_size // entsize):
                so = sh_offset + i * entsize
                if self.cls == 1:
                    st_name, _v, _s, st_info, _o, st_shndx = \
                        struct.unpack_from(e + "IIIBBH", d, so)
                else:
                    st_name, st_info, _o, st_shndx, _v, _s = \
                        struct.unpack_from(e + "IBBHQQ", d, so)
                if st_name == 0:
                    continue
                end = d.index(b"\0", strtab_off + st_name)
                nm = d[strtab_off + st_name:end].decode("utf-8", "replace")
                out.append((nm, st_info >> 4, st_shndx))
            return out
        return []


def strip_ver(sym: str) -> str:
    return sym.split("@", 1)[0]


def exports_of(paths: List[str]) -> Set[str]:
    syms: Set[str] = set()
    for p in paths:
        try:
            for nm, bind, shndx in Elf(p).dynsyms():
                if shndx != SHN_UNDEF and bind in (STB_GLOBAL, STB_WEAK):
                    syms.add(strip_ver(nm))
        except (ValueError, OSError, struct.error) as ex:
            print(f"[gate] WARN: cannot parse {p}: {ex}", file=sys.stderr)
    return syms


def unds_of(paths: List[str]) -> Set[str]:
    """Undefined symbols of stock device libraries: whatever THEY bind to on
    the real device is proven runtime-resolvable in the BT process."""
    syms: Set[str] = set()
    for p in paths:
        try:
            for nm, bind, shndx in Elf(p).dynsyms():
                if shndx == SHN_UNDEF and nm:
                    syms.add(strip_ver(nm))
        except (ValueError, OSError, struct.error) as ex:
            print(f"[gate] WARN: cannot parse {p}: {ex}", file=sys.stderr)
    return syms


def find_so(dirs: List[str]) -> List[str]:
    out = []
    for root in dirs:
        if os.path.isfile(root):
            out.append(root)
            continue
        for dirpath, _dn, filenames in os.walk(root):
            for fn in sorted(filenames):
                if ".so" in fn:
                    out.append(os.path.join(dirpath, fn))
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ours", required=True)
    ap.add_argument("--baseline-dir", action="append", default=[])
    ap.add_argument("--sysroot-lib", action="append", default=[])
    ap.add_argument("--allowlist")
    ap.add_argument("--proven-und", action="append", default=[],
                    help="ELF (e.g. the stock 32-bit lib/hw/bluetooth.default.so "
                         "shipped by the device) whose UNDEFINED symbols are "
                         "proven runtime-resolvable in the target BT process; "
                         "they join the provided set.  Needed because the "
                         "baseline extraction carries 64-bit libmedia/libutils "
                         "only, while size_t-mangled symbols (AudioTrack::write, "
                         "ctor) differ between 32/64-bit.")
    ap.add_argument("--out")
    args = ap.parse_args(argv)

    ours = Elf(args.ours)
    strong_und: Set[str] = set()
    weak_und: Set[str] = set()
    for nm, bind, shndx in ours.dynsyms():
        if shndx != SHN_UNDEF:
            continue
        (weak_und if bind == STB_WEAK else strong_und).add(strip_ver(nm))

    allow: Set[str] = set()
    if args.allowlist:
        with open(args.allowlist, encoding="utf-8") as f:
            allow = {ln.strip() for ln in f
                     if ln.strip() and not ln.lstrip().startswith("#")}

    base_libs = find_so(args.baseline_dir)
    sys_libs = find_so(args.sysroot_lib)
    base_syms = exports_of(base_libs)
    sys_syms = exports_of(sys_libs)

    proven_libs = find_so(args.proven_und)
    proven_syms = unds_of(proven_libs)

    provided = base_syms | sys_syms | allow | proven_syms
    missing = sorted(strong_und - provided)
    weak_missing = sorted(weak_und - provided)

    report = {
        "ours": args.ours,
        "strong_undefined": len(strong_und),
        "weak_undefined": len(weak_und),
        "baseline_libs": len(base_libs),
        "baseline_export_syms": len(base_syms),
        "sysroot_libs": len(sys_libs),
        "sysroot_export_syms": len(sys_syms),
        "allowlist": sorted(allow),
        "proven_und_libs": proven_libs,
        "proven_und_syms": len(proven_syms),
        "missing": missing,
        "weak_missing": weak_missing,
        "verdict": "PASS" if not missing else "FAIL",
    }
    txt = json.dumps(report, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(txt + "\n")
    print(f"[gate] ours={os.path.basename(args.ours)} "
          f"strongUND={len(strong_und)} weakUND={len(weak_und)}")
    print(f"[gate] baseline: {len(base_libs)} libs / {len(base_syms)} exports; "
          f"sysroot: {len(sys_libs)} libs / {len(sys_syms)} exports; "
          f"allowlist: {len(allow)}; proven-und: {len(proven_libs)} libs / "
          f"{len(proven_syms)} syms")
    if missing:
        print(f"[gate] FAIL: {len(missing)} strong undefined symbols NOT "
              f"provided by baseline+sysroot:")
        for m in missing[:60]:
            print(f"    {m}")
        if len(missing) > 60:
            print(f"    ... and {len(missing) - 60} more (see --out JSON)")
    else:
        print("[gate] PASS: every strong undefined symbol resolves against "
              "baseline U sysroot U allowlist U proven-und")
        if weak_missing:
            print(f"[gate] note: {len(weak_missing)} WEAK undefined remain "
                  f"(bind to 0; first 10): {weak_missing[:10]}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
