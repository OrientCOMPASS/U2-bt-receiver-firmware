#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""baseline_probe.py -- dissect the official U2 baseline firmware WITHOUT root,
loop mounts, or Android tooling: pure-stdlib zip + ext4 + ELF32 parsing.

Purpose (milestone B1 of docs/NOUGAT_BT_SWAP.md):
  * read /system/build.prop  -> exact Android version / security patch / MTK bits
  * inventory every Bluetooth-related file in system.img
  * parse ELF32 DT_NEEDED/SONAME/symbol tables of the device's BT libraries
  * extract the BT files + the transitive closure of their shared-library
    dependencies, so milestone B3 can link OUR modules against the REAL device
    libraries and check symbol coverage before ever flashing.

Inputs (mutually exclusive):
  --zip PATH      official firmware zip (system.img may live in a subfolder and
                  be deflate-compressed; it is extracted to a temp file first)
  --image PATH    a raw ext4 system.img

Outputs:
  --outdir DIR    report.json, extracted/ (files from the image), symbols/
                  (per-library undefined/exported symbol dumps for B3)
  human-readable summary on stdout.

Exit codes: 0 ok; 2 usage; 3 image/parse error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
import tempfile
import zipfile

EXT4_MAGIC = 0xEF53
S_IFMT = 0xF000
S_IFDIR = 0x4000
S_IFREG = 0x8000
S_IFLNK = 0xA000
EXT4_EXTENTS_FL = 0x80000
EXT4_INLINE_DATA_FL = 0x10000000
EXTENT_MAGIC = 0xF30A
FT_REG, FT_DIR, FT_LNK = 1, 2, 7

INCOMPAT_64BIT = 0x80
INCOMPAT_EXTENTS = 0x40
INCOMPAT_FILETYPE = 0x02
INCOMPAT_META_BG = 0x10

DT_NEEDED, DT_STRSZ, DT_SONAME = 1, 10, 14
DT_VERNEEDNUM = 0x6FFFFFFF
SHN_UNDEF = 0


class ProbeError(Exception):
    """image/parse level failure (exit 3)."""


def parse_dirents(data: bytes) -> list:
    """linear-scan ext4_dir_entry_2 records; [(name, ino, file_type)]."""
    out = []
    off = 0
    while off + 8 <= len(data):
        e_ino, rec_len, name_len, ftype = struct.unpack_from("<IHBB", data, off)
        if rec_len < 8 or off + rec_len > len(data):
            break
        if e_ino != 0 and name_len:
            name = data[off + 8: off + 8 + name_len].decode("utf-8", "replace")
            if name not in (".", ".."):
                out.append((name, e_ino, ftype))
        off += rec_len
    return out


# --------------------------------------------------------------------------
# ext4
# --------------------------------------------------------------------------
class Ext4Image:
    """Minimal read-only ext4 reader: superblock, group descriptors, extents,
    directory entries, file reads.  Covers what make_ext4fs/mke2fs produce for
    Android system images (extents, filetype, sparse_super, large_file, and
    defensively 64bit/meta_bg)."""

    def __init__(self, path: str):
        self.f = open(path, "rb")
        self.f.seek(1024)
        sb = self.f.read(1024)
        if len(sb) < 0x100:
            raise ProbeError("image too small for a superblock")
        magic, = struct.unpack_from("<H", sb, 0x38)
        if magic != EXT4_MAGIC:
            self.f.seek(0)
            m0, = struct.unpack("<I", self.f.read(4))
            if m0 == 0xED26FF3A:
                raise ProbeError("image is MTK-sparse; convert with "
                                 "scripts/mtk_sparse2raw.py first")
            raise ProbeError("not an ext4 image (magic 0x%04x)" % magic)
        (self.inodes_count, self.blocks_count_lo, _r, _fb, _fi,
         self.first_data_block, log_bs, _lcs, self.blocks_per_group,
         _cpg, self.inodes_per_group) = struct.unpack_from("<11I", sb, 0)
        self.block_size = 1024 << log_bs
        self.inode_size, = struct.unpack_from("<H", sb, 0x58)
        self.feat_compat, self.feat_incompat, self.feat_ro = \
            struct.unpack_from("<3I", sb, 0x5C)
        self.desc_size, = struct.unpack_from("<H", sb, 0xFE)
        self.blocks_count_hi, = struct.unpack_from("<I", sb, 0x150)
        self.uuid = sb[0x68:0x78].hex()
        self.vol_name = sb[0x78:0x88].split(b"\0")[0].decode(errors="replace")
        if self.feat_incompat & INCOMPAT_64BIT:
            self.blocks_count = (self.blocks_count_hi << 32) | self.blocks_count_lo
            if self.desc_size < 64:
                self.desc_size = 64
        else:
            self.blocks_count = self.blocks_count_lo
        if not (self.feat_incompat & INCOMPAT_EXTENTS):
            raise ProbeError("only extent-mapped ext4 is supported")
        self.groups = (self.blocks_count + self.blocks_per_group - 1) \
            // self.blocks_per_group
        # group descriptor table location
        if self.block_size == 1024:
            gdt_block = 2
        else:
            gdt_block = 1
        gd_bytes = self.groups * self.desc_size
        self._gdt = self.block(gdt_block, (gd_bytes + self.block_size - 1)
                               // self.block_size)[:gd_bytes]
        self._dir_cache: dict[int, list[tuple[str, int, int]]] = {}

    # -- low level ---------------------------------------------------------
    def block(self, n: int, count: int = 1) -> bytes:
        self.f.seek(n * self.block_size)
        data = self.f.read(count * self.block_size)
        if len(data) != count * self.block_size:
            raise ProbeError("short read at block %d" % n)
        return data

    def group_desc(self, bg: int) -> dict:
        off = bg * self.desc_size
        gd = self._gdt[off:off + self.desc_size]
        if len(gd) < 32:
            raise ProbeError("group descriptor %d out of range" % bg)
        bmap, imap, itable = struct.unpack_from("<3I", gd, 0)
        if self.feat_incompat & INCOMPAT_64BIT:
            bhi, ihi, thi = struct.unpack_from("<3I", gd, 0x20)
            bmap |= bhi << 32
            imap |= ihi << 32
            itable |= thi << 32
        return {"block_bitmap": bmap, "inode_bitmap": imap,
                "inode_table": itable}

    def inode(self, ino: int) -> dict:
        if ino <= 0 or ino > self.inodes_count:
            raise ProbeError("inode %d out of range" % ino)
        bg = (ino - 1) // self.inodes_per_group
        idx = (ino - 1) % self.inodes_per_group
        gd = self.group_desc(bg)
        off = gd["inode_table"] * self.block_size + idx * self.inode_size
        self.f.seek(off)
        raw = self.f.read(self.inode_size)
        if len(raw) < 0x80:
            raise ProbeError("short inode read for %d" % ino)
        mode, = struct.unpack_from("<H", raw, 0)
        size_lo, = struct.unpack_from("<I", raw, 4)
        flags, = struct.unpack_from("<I", raw, 0x20)
        size_hi, = struct.unpack_from("<I", raw, 0x6C)
        iblock = raw[0x28:0x64]
        links, = struct.unpack_from("<H", raw, 0x1A)
        size = size_lo
        if self.feat_ro & 0x8 or size_hi:      # HUGE_FILE / high bits in use
            size |= size_hi << 32
        return {"ino": ino, "mode": mode, "size": size, "flags": flags,
                "iblock": iblock, "links": links, "raw": raw}

    # -- extents ------------------------------------------------------------
    def _extent_ranges(self, iblock: bytes) -> list:
        """walk an extent tree rooted in inode i_block; return
        [(logical_block, physical_block, length), ...] sorted by logical."""
        out: list[tuple[int, int, int]] = []
        stack = [iblock]
        while stack:
            node = stack.pop()
            magic, entries, _max, depth, _gen = struct.unpack_from("<HHHHI", node, 0)
            if magic != EXTENT_MAGIC:
                raise ProbeError("bad extent magic 0x%04x" % magic)
            for i in range(entries):
                e = node[12 + i * 12: 12 + (i + 1) * 12]
                if depth > 0:      # index node
                    _ei_block, ei_leaf_lo, ei_leaf_hi, _unused = \
                        struct.unpack("<IIHH", e)
                    leaf = (ei_leaf_hi << 32) | ei_leaf_lo
                    stack.append(self.block(leaf))
                else:              # leaf extent
                    ee_block, ee_len, ee_hi, ee_lo = struct.unpack("<IHHI", e)
                    ee_len &= 0x7FFF          # clear uninitialized flag
                    phys = (ee_hi << 32) | ee_lo
                    out.append((ee_block, phys, ee_len))
        out.sort()
        return out

    def read_file(self, ino: int, max_bytes: int | None = None) -> bytes:
        inn = self.inode(ino)
        if (inn["mode"] & S_IFMT) == S_IFLNK and inn["size"] < 60:
            return inn["iblock"][:inn["size"]]      # fast symlink
        if inn["flags"] & EXT4_INLINE_DATA_FL:
            raise ProbeError("inline-data inodes are not supported (ino %d)" % ino)
        size = inn["size"]
        if max_bytes is not None:
            size = min(size, max_bytes)
        ranges = self._extent_ranges(inn["iblock"])
        buf = bytearray(size)
        bs = self.block_size
        for logical, phys, length in ranges:
            lo = logical * bs
            if lo >= size:
                break
            n = min(length * bs, size - lo)         # bytes wanted from extent
            nblk = (n + bs - 1) // bs               # read only what we need
            chunk = self.block(phys, nblk)
            buf[lo:lo + n] = chunk[:n]
        return bytes(buf)

    # -- directories --------------------------------------------------------
    def dir_entries(self, ino: int) -> list[tuple[str, int, int]]:
        """[(name, child_ino, file_type)] for a directory inode."""
        if ino in self._dir_cache:
            return self._dir_cache[ino]
        inn = self.inode(ino)
        if (inn["mode"] & S_IFMT) != S_IFDIR:
            raise ProbeError("inode %d is not a directory" % ino)
        if inn["flags"] & EXT4_INLINE_DATA_FL:
            raise ProbeError("inline directories not supported (ino %d)" % ino)
        out = parse_dirents(self.read_file(ino))
        self._dir_cache[ino] = out
        return out

    def lookup(self, path: str, follow: bool = True, _depth: int = 0) -> int | None:
        """resolve an absolute in-image path; return inode or None."""
        if _depth > 40:
            return None
        ino = 2                                   # root
        sofar = ""
        for comp in [c for c in path.split("/") if c]:
            cur = self.inode(ino)
            if (cur["mode"] & S_IFMT) == S_IFLNK and follow:
                tgt = self.read_file(ino).decode("utf-8", "replace")
                if not tgt.startswith("/"):       # relative to parent dir
                    tgt = sofar + "/" + tgt
                return self.lookup(tgt.rstrip("/") + "/" + comp, True,
                                   _depth + 1)
            if (cur["mode"] & S_IFMT) != S_IFDIR:
                return None
            found = None
            for name, c_ino, _ft in self.dir_entries(ino):
                if name == comp:
                    found = c_ino
                    break
            if found is None:
                return None
            sofar = sofar + "/" + comp
            ino = found
        if follow and (self.inode(ino)["mode"] & S_IFMT) == S_IFLNK:
            tgt = self.read_file(ino).decode("utf-8", "replace")
            if not tgt.startswith("/"):
                tgt = "/" + tgt
            return self.lookup(tgt, True, _depth + 1)
        return ino

    def walk(self, ino: int, prefix: str, depth: int = 0, max_depth: int = 8):
        """yield (path, ino, file_type, size) recursively."""
        for name, c_ino, ftype in self.dir_entries(ino):
            path = prefix.rstrip("/") + "/" + name
            size = 0
            if ftype == FT_REG or ftype == FT_LNK:
                try:
                    size = self.inode(c_ino)["size"]
                except ProbeError:
                    pass
            yield path, c_ino, ftype, size
            if ftype == FT_DIR and depth < max_depth:
                yield from self.walk(c_ino, path, depth + 1, max_depth)


# --------------------------------------------------------------------------
# ELF32 (little endian, ARM)
# --------------------------------------------------------------------------
def parse_elf32(data: bytes) -> dict:
    """Extract SONAME / DT_NEEDED / symbol stats from an ELF32LE shared object.
    Returns {} for non-ELF inputs."""
    if data[:4] != b"\x7fELF":
        return {}
    ei_class, ei_data = data[4], data[5]
    if ei_class != 1 or ei_data != 1:
        return {"note": "not ELF32LE (class=%d data=%d)" % (ei_class, ei_data)}
    e_type, e_machine = struct.unpack_from("<HH", data, 16)
    e_shoff, = struct.unpack_from("<I", data, 32)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", data, 46)
    if e_shentsize < 40:
        return {"note": "unexpected e_shentsize %d" % e_shentsize}
    sections = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        if off + 40 > len(data):
            break
        # name,type,flags,addr,offset,size,link,info,align,entsize
        sh = struct.unpack_from("<10I", data, off)
        sections.append(sh)
    if e_shstrndx >= len(sections):
        return {"note": "bad shstrndx"}
    strtab_sec = sections[e_shstrndx]
    strtab = data[strtab_sec[4]:strtab_sec[4] + strtab_sec[5]]

    def sec_named(n: int) -> str:
        end = strtab.find(b"\0", n)
        return strtab[n:end].decode()

    by_name = {}
    for sh in sections:
        by_name.setdefault(sec_named(sh[0]), sh)
    out: dict = {"e_type": e_type, "e_machine": e_machine}
    dyn_sec = by_name.get(".dynamic")
    dynstr_sec = by_name.get(".dynstr")
    dynsym_sec = by_name.get(".dynsym")
    if dyn_sec and dynstr_sec:
        dynstr = data[dynstr_sec[4]:dynstr_sec[4] + dynstr_sec[5]]
        needed, soname, verneed = [], None, 0
        dyn = data[dyn_sec[4]:dyn_sec[4] + dyn_sec[5]]
        for i in range(len(dyn) // 8):
            tag, val = struct.unpack_from("<iI", dyn, i * 8)
            if tag == 0:
                break
            if tag == DT_NEEDED:
                needed.append(dynstr[val:dynstr.find(b"\0", val)].decode())
            elif tag == DT_SONAME:
                soname = dynstr[val:dynstr.find(b"\0", val)].decode()
            elif tag == DT_VERNEEDNUM:
                verneed = val
        out["soname"] = soname
        out["needed"] = needed
        out["verneed"] = verneed
    if dynsym_sec:
        symstr_sec = sections[dynsym_sec[6]]            # sh_link -> strtab
        symstr = data[symstr_sec[4]:symstr_sec[4] + symstr_sec[5]]
        syms = data[dynsym_sec[4]:dynsym_sec[4] + dynsym_sec[5]]
        exported, undefined = [], []
        for i in range(len(syms) // 16):
            st_name, _val, _sz, st_info, _oth, st_shndx = \
                struct.unpack_from("<IIIBBH", syms, i * 16)
            if st_name == 0:
                continue
            name = symstr[st_name:symstr.find(b"\0", st_name)].decode()
            bind = st_info >> 4
            if st_shndx == SHN_UNDEF:
                undefined.append(name)
            elif bind in (1, 2):                        # GLOBAL, WEAK
                exported.append(name)
        out["n_exported"] = len(exported)
        out["n_undefined"] = len(undefined)
        out["exported"] = exported
        out["undefined"] = undefined
    return out


# --------------------------------------------------------------------------
# probe flow
# --------------------------------------------------------------------------
BT_NAME_RE = re.compile(
    r"(?i)(bluetooth|^bt[_-]|libbt|^a2dp|audio\.a2dp|avrcp|^btstack|_bt\.|hci)")
BT_DIRS = ("/lib/hw", "/vendor/lib/hw", "/system/vendor/lib/hw")
FW_DIRS = ("/etc/firmware", "/vendor/firmware", "/firmware")
BUILD_PROP_KEYS = (
    "ro.build.version.release", "ro.build.version.sdk",
    "ro.build.version.security_patch", "ro.build.fingerprint",
    "ro.build.display.id", "ro.build.date", "ro.build.type",
    "ro.product.model", "ro.product.device", "ro.product.board",
    "ro.hardware", "ro.board.platform", "ro.mediatek.version.release",
    "ro.mediatek.platform", "ro.crypto.state", "persist.sys.timezone",
)


def open_image(args) -> tuple[str, bool]:
    """Return (image_path, is_temp)."""
    if args.image:
        if not os.path.isfile(args.image):
            raise SystemExit("baseline_probe: image not found: %s" % args.image)
        return args.image, False
    if not os.path.isfile(args.zip):
        raise SystemExit("baseline_probe: zip not found: %s" % args.zip)
    zf = zipfile.ZipFile(args.zip)
    entry = args.zip_entry
    if not entry:
        cands = [n for n in zf.namelist() if os.path.basename(n) == "system.img"]
        if not cands:
            raise SystemExit("baseline_probe: no system.img entry in zip")
        entry = cands[0]
    dest = args.image_out
    tmp_created = False
    if not dest:
        fd, dest = tempfile.mkstemp(prefix="u2_system_", suffix=".img")
        os.close(fd)
        tmp_created = True
    print("[probe] extracting %s (%.1f MiB compressed) -> %s"
          % (entry, os.path.getsize(args.zip) / 1048576.0, dest), flush=True)
    with zf.open(entry) as src, open(dest, "wb") as out:
        while True:
            chunk = src.read(8 << 20)
            if not chunk:
                break
            out.write(chunk)
    return dest, tmp_created


def collect(img: Ext4Image, verbose: bool) -> dict:
    """Walk the image; inventory BT-related files + lib dirs + build.prop."""
    inv: dict = {"bt_files": [], "libs": {}, "vendor_libs": {}, "firmware": [],
                 "apk_dirs": [], "all_dirs": 0, "all_files": 0}
    root_ino = 2
    for path, ino, ftype, size in img.walk(root_ino, "", 0, 8):
        base = os.path.basename(path)
        d = os.path.dirname(path)
        if ftype == FT_DIR:
            inv["all_dirs"] += 1
            if base == "Bluetooth":
                inv["apk_dirs"].append(path)
            continue
        inv["all_files"] += 1
        is_so = base.endswith(".so")
        # library search-path maps (for DT_NEEDED closure)
        if d in ("/lib", "/lib64") and is_so:
            inv["libs"][base] = (path, ino, size)
        elif d in ("/vendor/lib", "/system/vendor/lib") and is_so:
            inv["vendor_libs"][base] = (path, ino, size)
        # firmware dirs: MTK combo-chip files (mt6625/mt6630 patches etc.)
        # do not always carry "bt" in the name -- take the whole small dir.
        if d in FW_DIRS and size < (16 << 20):
            inv["firmware"].append({"path": path, "ino": ino, "size": size})
        # BT-related set (independent of the lib-map classification above:
        # /lib/libbluetooth_jni.so must land in BOTH)
        why = None
        if BT_NAME_RE.search(base):
            why = "bt-hal-dir+name-match" if d in BT_DIRS else "name-match"
        elif d.startswith("/app/Bluetooth") or d.startswith("/priv-app/Bluetooth"):
            why = "bt-app-dir"
        if why:
            inv["bt_files"].append({"path": path, "ino": ino, "size": size,
                                    "why": why})
        if verbose and inv["all_files"] % 5000 == 0:
            print("[probe] walked %d files..." % inv["all_files"], flush=True)
    return inv


def transitive_closure(img: Ext4Image, seeds: list[str], libmap: dict,
                       vendor_map: dict) -> dict:
    """BFS over DT_NEEDED starting from seed .so paths.
    Returns {path: elf_info} for every library read."""
    seen: dict[str, dict] = {}
    queue = list(seeds)
    while queue:
        path = queue.pop(0)
        if path in seen:
            continue
        ino = None
        for m in (libmap, vendor_map):
            hit = m.get(os.path.basename(path))
            if hit:
                ino = hit[1]
                break
        if ino is None:
            ino = img.lookup(path)
        if ino is None:
            seen[path] = {"missing": True}
            continue
        data = img.read_file(ino)
        info = parse_elf32(data)
        info["size"] = len(data)
        seen[path] = info
        for need in info.get("needed", []):
            if need in libmap:
                queue.append(libmap[need][0])
            elif need in vendor_map:
                queue.append(vendor_map[need][0])
            else:
                seen.setdefault("(unresolved) " + need, {"missing": True})
    return seen


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Dissect the official U2 baseline firmware (zip or raw "
                    "system.img): build.prop, BT inventory, ELF deps, "
                    "extract BT files + dependency closure.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--zip", help="official firmware zip containing system.img")
    src.add_argument("--image", help="raw ext4 system.img")
    ap.add_argument("--zip-entry", help="zip entry name (default: */system.img)")
    ap.add_argument("--image-out", help="where to extract system.img from the zip")
    ap.add_argument("--outdir", required=True,
                    help="output dir for report.json / extracted/ / symbols/")
    ap.add_argument("--no-extract", action="store_true",
                    help="analyze only; do not write extracted files")
    ap.add_argument("--keep-image", action="store_true",
                    help="keep the extracted system.img (default: delete temp)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    img_path, is_temp = open_image(args)
    try:
        return run_probe(img_path, args)
    finally:
        if is_temp and not args.keep_image:
            try:
                os.unlink(img_path)
            except OSError:
                pass


def run_probe(img_path: str, args) -> int:
    outdir = args.outdir
    os.makedirs(outdir, exist_ok=True)
    ext_dir = os.path.join(outdir, "extracted")
    sym_dir = os.path.join(outdir, "symbols")
    os.makedirs(ext_dir, exist_ok=True)
    os.makedirs(sym_dir, exist_ok=True)

    print("[probe] parsing ext4: %s" % img_path, flush=True)
    img = Ext4Image(img_path)
    print("[probe] block_size=%d inodes=%d blocks=%d groups=%d feats "
          "compat=0x%x incompat=0x%x ro=0x%x vol=%r"
          % (img.block_size, img.inodes_count, img.blocks_count, img.groups,
             img.feat_compat, img.feat_incompat, img.feat_ro, img.vol_name))

    report: dict = {"image": os.path.abspath(img_path), "superblock": {
        "block_size": img.block_size, "inodes": img.inodes_count,
        "blocks": img.blocks_count, "groups": img.groups,
        "feature_compat": hex(img.feat_compat),
        "feature_incompat": hex(img.feat_incompat),
        "feature_ro_compat": hex(img.feat_ro), "uuid": img.uuid}}

    # --- build.prop ---------------------------------------------------------
    bp_ino = img.lookup("/build.prop")
    version: dict = {}
    if bp_ino:
        raw = img.read_file(bp_ino).decode("utf-8", "replace")
        if not args.no_extract:
            with open(os.path.join(ext_dir, "build.prop"), "w",
                      encoding="utf-8") as f:
                f.write(raw)
        for line in raw.splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                version[k.strip()] = v.strip()
        report["build_prop_all"] = version
        report["version"] = {k: version.get(k) for k in BUILD_PROP_KEYS
                             if k in version}
        print("[probe] build.prop: release=%s sdk=%s patch=%s fp=%s"
              % (version.get("ro.build.version.release"),
                 version.get("ro.build.version.sdk"),
                 version.get("ro.build.version.security_patch"),
                 version.get("ro.build.fingerprint")))
    else:
        print("[probe] WARNING: /build.prop not found", file=sys.stderr)

    # --- inventory ------------------------------------------------------------
    inv = collect(img, args.verbose)
    print("[probe] walked %d dirs / %d files; %d BT-related files, %d libs, "
          "%d vendor libs, %d fw files, Bluetooth apk dirs: %s"
          % (inv["all_dirs"], inv["all_files"], len(inv["bt_files"]),
             len(inv["libs"]), len(inv["vendor_libs"]), len(inv["firmware"]),
             inv["apk_dirs"]))
    report["inventory"] = {
        "bt_files": sorted(inv["bt_files"], key=lambda b: b["path"]),
        "firmware": inv["firmware"],
        "apk_dirs": inv["apk_dirs"],
        "lib_names": sorted(inv["libs"]),
        "vendor_lib_names": sorted(inv["vendor_libs"]),
        "counts": {"dirs": inv["all_dirs"], "files": inv["all_files"]}}

    # --- ELF analysis + closure ---------------------------------------------
    seeds = [b["path"] for b in inv["bt_files"] if b["path"].endswith(".so")]
    closure = transitive_closure(img, seeds, inv["libs"], inv["vendor_libs"])
    elf_report = {}
    for path, info in sorted(closure.items()):
        slim = {k: v for k, v in info.items()
                if k not in ("exported", "undefined")}
        elf_report[path] = slim
        if info.get("missing"):
            continue
        base = os.path.basename(path)
        if info.get("undefined"):
            with open(os.path.join(sym_dir, base + ".undef.txt"), "w",
                      encoding="utf-8") as f:
                f.write("\n".join(sorted(info["undefined"])))
        if info.get("exported"):
            with open(os.path.join(sym_dir, base + ".exp.txt"), "w",
                      encoding="utf-8") as f:
                f.write("\n".join(sorted(info["exported"])))
    report["elf"] = elf_report
    key_libs = [p for p in elf_report if not p.startswith("(")]
    print("[probe] closure: %d libraries read (%d unresolved sonames)"
          % (len(key_libs), len(elf_report) - len(key_libs)))
    for p in seeds:
        info = elf_report.get(p, {})
        print("  %-46s SONAME=%-28s needed=%d exported=%s undefined=%s"
              % (p, info.get("soname"), len(info.get("needed", [])),
                 info.get("n_exported"), info.get("n_undefined")))

    # --- extraction -------------------------------------------------------------
    extracted = []
    if not args.no_extract:
        want = {b["path"]: b["ino"] for b in inv["bt_files"]}
        for fw in inv["firmware"]:
            want[fw["path"]] = fw["ino"]
        for path, ino in sorted(want.items()):
            data = img.read_file(ino)
            dest = os.path.join(ext_dir, path.lstrip("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(data)
            extracted.append({"path": path, "bytes": len(data)})
        for path in key_libs:
            ino = img.lookup(path)
            if ino is None:
                continue
            data = img.read_file(ino)
            dest = os.path.join(ext_dir, path.lstrip("/"))
            if os.path.exists(dest):
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(data)
            extracted.append({"path": path, "bytes": len(data)})
        total = sum(e["bytes"] for e in extracted)
        print("[probe] extracted %d files (%.1f MiB) -> %s"
              % (len(extracted), total / 1048576.0, ext_dir))
    report["extracted"] = extracted

    rep_path = os.path.join(outdir, "report.json")
    with open(rep_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, sort_keys=True)
    print("[probe] report -> %s" % rep_path)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ProbeError as e:
        print("baseline_probe: ERROR: %s" % e, file=sys.stderr)
        sys.exit(3)
