#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mtk_fw_zip.py -- inspect / selectively extract a MediaTek firmware zip.

The KeenPlus U2 baseline firmware is a ~1.9 GiB zip that holds the SP_Flash_Tool
package: a scatter file, the download agent (DA), SP_Flash_Tool itself, and every
partition image (preloader, lk, logo, boot, system, ...).  For the hybrid repack
(plan: reuse the MTK-private partitions + scatter, replace system/boot) we only
need a handful of small/medium entries -- NOT the multi-GB system/userdata.

This tool reads a zip's central directory over HTTP **range requests** (or from a
local file) and extracts individual entries without downloading the whole archive.
Pure standard library.

Usage:
    mtk_fw_zip.py size    <zip-url|path>
    mtk_fw_zip.py list    <zip-url|path>
    mtk_fw_zip.py extract <zip-url|path> <entry-name> <dest>
    mtk_fw_zip.py scatter <zip-url|path> [dest]      # find + dump the *_scatter.txt

Remote access resolves the GitHub release redirect once (the signed asset URL is
valid ~1h) and then issues byte-range GETs directly, so Range is always honoured.
"""

from __future__ import annotations

import argparse
import io
import os
import struct
import sys
import urllib.request
import zlib
from typing import Dict, List, Optional, Tuple

EOCD_SIG = 0x06054B50
EOCD64_SIG = 0x06064B50
EOCD64_LOC_SIG = 0x07064B50
CD_SIG = 0x02014B50
LFH_SIG = 0x04034B50
EOCD_MIN = 22
EOCD_MAX_COMMENT = 0xFFFF


class ZipSource:
    """Byte-range reader over a local file or an HTTP(S) URL."""

    def __init__(self, url_or_path: str) -> None:
        self.is_remote = url_or_path.startswith("http://") or url_or_path.startswith("https://")
        self._fh: Optional[io.BufferedIOBase] = None
        if self.is_remote:
            self.url = self._resolve(url_or_path)
            self.size = self._remote_size()
        else:
            self.url = url_or_path
            self._fh = open(url_or_path, "rb")
            self._fh.seek(0, os.SEEK_END)
            self.size = self._fh.tell()

    @staticmethod
    def _opener() -> urllib.request.OpenerDirector:
        return urllib.request.build_opener()

    def _resolve(self, url: str) -> str:
        """Follow redirects once; return the final (signed) asset URL."""
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": "mtk-fw-zip"})
        with self._opener().open(req, timeout=60) as resp:
            return resp.geturl()

    def _remote_size(self) -> int:
        # A 1-byte range probe returns Content-Range: bytes 0-0/<total>.
        req = urllib.request.Request(self.url, headers={
            "Range": "bytes=0-0", "User-Agent": "mtk-fw-zip"})
        with self._opener().open(req, timeout=60) as resp:
            cr = resp.headers.get("Content-Range", "")
            if "/" in cr:
                return int(cr.rsplit("/", 1)[1])
            cl = resp.headers.get("Content-Length")
            return int(cl) if cl else 0

    def read(self, offset: int, length: int) -> bytes:
        if length <= 0:
            return b""
        if not self.is_remote:
            assert self._fh is not None
            self._fh.seek(offset)
            data = self._fh.read(length)
            if len(data) != length:
                raise IOError(f"short read at {offset}: got {len(data)} want {length}")
            return data
        end = offset + length - 1
        req = urllib.request.Request(self.url, headers={
            "Range": f"bytes={offset}-{end}", "User-Agent": "mtk-fw-zip"})
        with self._opener().open(req, timeout=120) as resp:
            data = resp.read()
        if len(data) != length:
            raise IOError(f"short range read at {offset}: got {len(data)} want {length}")
        return data

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()


class Entry:
    __slots__ = ("name", "method", "comp_size", "uncomp_size", "crc", "local_off")

    def __init__(self, name: str, method: int, comp_size: int, uncomp_size: int,
                 crc: int, local_off: int) -> None:
        self.name = name
        self.method = method
        self.comp_size = comp_size
        self.uncomp_size = uncomp_size
        self.crc = crc
        self.local_off = local_off

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Entry {self.name} m{self.method} {self.uncomp_size}B>"


class RemoteZip:
    def __init__(self, url_or_path: str) -> None:
        self.src = ZipSource(url_or_path)
        self.entries: Dict[str, Entry] = {}
        self._parse_central_directory()

    # -- central directory -------------------------------------------------- #
    def _find_eocd(self) -> Tuple[int, int, int]:
        """Return (cd_offset, cd_size, total_entries)."""
        size = self.src.size
        tail_len = min(size, EOCD_MIN + EOCD_MAX_COMMENT)
        tail = self.src.read(size - tail_len, tail_len)
        idx = tail.rfind(struct.pack("<I", EOCD_SIG))
        if idx < 0:
            raise ValueError("EOCD signature not found; not a zip?")
        eocd = tail[idx:idx + EOCD_MIN]
        if len(eocd) < EOCD_MIN:
            raise ValueError("truncated EOCD")
        (_, _, _, _, total, cd_size, cd_off,
         comment_len) = struct.unpack("<IHHHHIIH", eocd)
        # Zip64?
        if cd_off == 0xFFFFFFFF or total == 0xFFFF or cd_size == 0xFFFFFFFF:
            return self._find_eocd64(tail, idx)
        return cd_off, cd_size, total

    def _find_eocd64(self, tail: bytes, eocd_idx: int) -> Tuple[int, int, int]:
        loc_idx = tail.rfind(struct.pack("<I", EOCD64_LOC_SIG))
        if loc_idx < 0:
            raise ValueError("zip64 EOCD locator not found")
        # locator: sig(4) disk(4) eocd64_off(8) disks(4)
        eocd64_off = struct.unpack("<IQI", tail[loc_idx + 4:loc_idx + 20])[1]
        hdr = self.src.read(eocd64_off, 56)
        sig, sz, vmade, vneed, disk, cd_disk, n_disk, n_total, cd_size, cd_off = \
            struct.unpack("<IQHHIIQQQQ", hdr[:56])
        if sig != EOCD64_SIG:
            raise ValueError("bad zip64 EOCD signature")
        return cd_off, cd_size, n_total

    def _parse_central_directory(self) -> None:
        cd_off, cd_size, total = self._find_eocd()
        cd = self.src.read(cd_off, cd_size)
        buf = io.BytesIO(cd)
        count = 0
        while buf.tell() + 46 <= len(cd):
            sig = buf.read(4)
            if sig != struct.pack("<I", CD_SIG):
                break
            hdr = buf.read(42)
            (vmade, vneed, flags, method, mtime, mdate, crc, csize, usize,
             nlen, elen, clen, dstart, iattr, eattr,
             loff) = struct.unpack("<HHHHHHIIIHHHHHII", hdr)
            name = buf.read(nlen).decode("utf-8", "replace")
            extra = buf.read(elen)
            buf.read(clen)  # comment
            csize, usize, loff = self._apply_zip64(extra, csize, usize, loff)
            self.entries[name] = Entry(name, method, csize, usize, crc, loff)
            count += 1
        if count != total and total != 0xFFFF:
            # Not fatal (some zips pad), but surface it.
            sys.stderr.write(f"warning: parsed {count} entries, EOCD said {total}\n")

    @staticmethod
    def _apply_zip64(extra: bytes, csize: int, usize: int,
                     loff: int) -> Tuple[int, int, int]:
        if 0xFFFFFFFF not in (csize, usize, loff):
            return csize, usize, loff
        i = 0
        vals: List[int] = []
        while i + 4 <= len(extra):
            tag, sz = struct.unpack("<HH", extra[i:i + 4])
            if tag == 0x0001:
                data = extra[i + 4:i + 4 + sz]
                vals = [struct.unpack("<Q", data[j:j + 8])[0]
                        for j in range(0, len(data) - 7, 8)]
                break
            i += 4 + sz
        # zip64 extra order mirrors which fields were 0xFFFFFFFF.
        it = iter(vals)
        if usize == 0xFFFFFFFF:
            usize = next(it, usize)
        if csize == 0xFFFFFFFF:
            csize = next(it, csize)
        if loff == 0xFFFFFFFF:
            loff = next(it, loff)
        return csize, usize, loff

    # -- extraction --------------------------------------------------------- #
    def names(self) -> List[str]:
        return list(self.entries.keys())

    def read_entry(self, name: str) -> bytes:
        e = self.entries.get(name)
        if e is None:
            raise KeyError(f"no such entry: {name}")
        lfh = self.src.read(e.local_off, 30)
        sig = struct.unpack("<I", lfh[:4])[0]
        if sig != LFH_SIG:
            raise ValueError(f"bad local header for {name}")
        nlen, elen = struct.unpack("<HH", lfh[26:30])
        data_off = e.local_off + 30 + nlen + elen
        comp = self.src.read(data_off, e.comp_size)
        if e.method == 0:
            raw = comp
        elif e.method == 8:
            raw = zlib.decompress(comp, -15)
        else:
            raise ValueError(f"unsupported compression method {e.method} for {name}")
        if len(raw) != e.uncomp_size:
            raise ValueError(f"size mismatch for {name}: {len(raw)} != {e.uncomp_size}")
        return raw

    def extract(self, name: str, dest: str) -> int:
        data = self.read_entry(name)
        os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
        return len(data)

    def find_scatter(self) -> Optional[str]:
        for n in self.names():
            low = n.lower()
            if low.endswith("_scatter.txt") or "scatter" in low and low.endswith(".txt"):
                return n
        return None

    def close(self) -> None:
        self.src.close()


def human(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024  # type: ignore[assignment]
    return f"{n}B"


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["size", "list", "extract", "scatter"])
    ap.add_argument("zip", help="zip URL or local path")
    ap.add_argument("arg1", nargs="?", help="entry name (extract) or dest (scatter)")
    ap.add_argument("arg2", nargs="?", help="dest path (extract)")
    args = ap.parse_args(argv)

    z = RemoteZip(args.zip)
    try:
        if args.cmd == "size":
            print(f"{z.src.size} bytes ({human(z.src.size)}), {len(z.entries)} entries")
            return 0
        if args.cmd == "list":
            print(f"{'UNCOMP':>12} {'COMP':>12}  M  NAME")
            for name in sorted(z.names()):
                e = z.entries[name]
                print(f"{e.uncomp_size:>12} {e.comp_size:>12}  {e.method}  {name}")
            return 0
        if args.cmd == "scatter":
            sc = z.find_scatter()
            if not sc:
                print("no scatter file found", file=sys.stderr)
                return 1
            data = z.read_entry(sc)
            if args.arg1:
                with open(args.arg1, "wb") as f:
                    f.write(data)
                print(f"wrote {sc} ({len(data)} B) -> {args.arg1}")
            else:
                print(f"# ---- {sc} ({len(data)} B) ----")
                sys.stdout.write(data.decode("utf-8", "replace"))
            return 0
        if args.cmd == "extract":
            if not args.arg1 or not args.arg2:
                print("extract needs <entry> <dest>", file=sys.stderr)
                return 2
            n = z.extract(args.arg1, args.arg2)
            print(f"extracted {args.arg1} ({n} B) -> {args.arg2}")
            return 0
    finally:
        z.close()
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
