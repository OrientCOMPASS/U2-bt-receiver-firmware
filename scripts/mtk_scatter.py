#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mtk_scatter.py -- parse a MediaTek scatter file and validate a flash package.

The scatter (e.g. MT6735M_Android_scatter.txt) is the layout SP_Flash_Tool reads.
For the hybrid repack we REUSE the official scatter verbatim and supply images
under the exact `file_name` each downloaded partition references.  This tool:

  * parses the scatter into structured records (general cfg + partitions),
  * lists the unique image files a complete package must contain
    (every is_download:true partition whose file_name != NONE),
  * validates a package directory against the scatter (what's missing),
  * can (re)write the scatter unchanged -- we do not modify the official layout.

Pure standard library (the format is regular enough not to need PyYAML).

Usage:
    mtk_scatter.py parse   <scatter.txt>            # dump parsed structure
    mtk_scatter.py files   <scatter.txt>            # list required image files
    mtk_scatter.py validate <scatter.txt> <pkgdir>  # check completeness
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Dict, List, Optional

_KEY = re.compile(r"^\s*([A-Za-z0-9_]+):\s*(.*?)\s*$")


class Partition(dict):
    """A partition record; dict of raw string fields plus typed helpers."""

    @property
    def name(self) -> str:
        return self.get("partition_name", "")

    @property
    def file_name(self) -> str:
        return self.get("file_name", "")

    @property
    def is_download(self) -> bool:
        return self.get("is_download", "").strip().lower() == "true"

    def size_bytes(self) -> int:
        return _to_int(self.get("partition_size", "0"))

    def needs_file(self) -> bool:
        return self.is_download and self.file_name not in ("", "NONE")


def _to_int(v: str) -> int:
    v = (v or "0").strip()
    try:
        return int(v, 16) if v.lower().startswith("0x") else int(v)
    except ValueError:
        return 0


def parse_scatter(text: str) -> Dict[str, object]:
    """Parse scatter text -> {'general': {...}, 'partitions': [Partition, ...]}."""
    general: Dict[str, str] = {}
    partitions: List[Partition] = []
    cur: Optional[Partition] = None
    in_general = False

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        stripped = line.strip()

        if stripped.startswith("- general:"):
            in_general = True
            cur = None
            continue
        if stripped.startswith("- partition_index:"):
            in_general = False
            cur = Partition()
            partitions.append(cur)
            # the "- partition_index: SYS0" line itself carries the first key
            m = _KEY.match(stripped.lstrip("- "))
            if m:
                cur[m.group(1)] = m.group(2)
            continue

        m = _KEY.match(line)
        if not m and in_general and stripped.startswith("- "):
            # e.g. "- config_version: V1.1.2" under general/info
            m = _KEY.match(stripped[2:])
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        if in_general:
            general[key] = val
        elif cur is not None:
            cur[key] = val

    return {"general": general, "partitions": partitions}


def load_scatter(path: str) -> Dict[str, object]:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return parse_scatter(f.read())


def required_files(scatter: Dict[str, object]) -> List[str]:
    """Unique image file_names a complete package must contain."""
    seen: List[str] = []
    for p in scatter["partitions"]:  # type: ignore[index]
        if p.needs_file() and p.file_name not in seen:
            seen.append(p.file_name)
    return seen


def validate_package(scatter: Dict[str, object], pkgdir: str) -> Dict[str, object]:
    """Check every required image exists (and fits its partition)."""
    missing: List[str] = []
    oversized: List[str] = []
    present: List[str] = []
    for p in scatter["partitions"]:  # type: ignore[index]
        if not p.needs_file():
            continue
        fn = p.file_name
        path = os.path.join(pkgdir, fn)
        if not os.path.isfile(path):
            missing.append(fn)
            continue
        if fn not in present:
            present.append(fn)
        sz = os.path.getsize(path)
        cap = p.size_bytes()
        # A raw image larger than the partition will not flash; ext4 images that
        # are smaller are fine (fs resize / free space).
        if cap and sz > cap:
            oversized.append(f"{fn}: {sz} > partition {cap}")
    return {"present": present, "missing": missing, "oversized": oversized,
            "ok": not missing and not oversized}


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["parse", "files", "validate"])
    ap.add_argument("scatter", help="path to *_scatter.txt")
    ap.add_argument("pkgdir", nargs="?", help="package dir (validate)")
    args = ap.parse_args(argv)

    sc = load_scatter(args.scatter)
    if args.cmd == "parse":
        g = sc["general"]  # type: ignore[index]
        print(f"platform={g.get('platform')} project={g.get('project')} "
              f"storage={g.get('storage')} block_size={g.get('block_size')} "
              f"config={g.get('config_version')}")
        print(f"{'NAME':<12}{'FILE':<34}{'DL':<6}{'SIZE':<12}{'REGION'}")
        for p in sc["partitions"]:  # type: ignore[index]
            sz = p.size_bytes()
            szs = f"{sz/1024/1024:.1f}MiB" if sz else "0"
            print(f"{p.name:<12}{p.file_name:<34}"
                  f"{str(p.is_download).lower():<6}{szs:<12}{p.get('region','')}")
        return 0
    if args.cmd == "files":
        for fn in required_files(sc):
            print(fn)
        return 0
    if args.cmd == "validate":
        if not args.pkgdir:
            print("validate needs <pkgdir>", file=sys.stderr)
            return 2
        res = validate_package(sc, args.pkgdir)
        print(f"present : {len(res['present'])} -> {', '.join(res['present'])}")
        if res["missing"]:
            print(f"MISSING : {len(res['missing'])} -> {', '.join(res['missing'])}")
        if res["oversized"]:
            print(f"OVERSIZED: {len(res['oversized'])}")
            for o in res["oversized"]:
                print(f"    {o}")
        if res["ok"]:
            print("package OK: complete and within partition sizes")
            return 0
        print("package INCOMPLETE")
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
