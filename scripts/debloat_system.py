#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""debloat_system.py -- remove listed app directories from a mounted /system.

The removal list (debloat/remove-list.txt) holds one system-relative path per
line, e.g. `app/Foo` or `priv-app/Bar`; '#' comments and blank lines are
ignored.  Safety rails (all enforced, any violation aborts before deleting
anything):

  * every path must match ^(app|priv-app|vendor/app|vendor/priv-app)/[^/]+$
    -- top-level app directories only, no nesting, no '..', no absolute paths
  * every path must exist as a directory on the mount
  * nothing outside the listed directories is ever touched

After removal it greps /system/etc/permissions/*.xml,
/system/etc/preferred-apps/*.xml and /system/etc/sysconfig/*.xml for the
removed package names and reports dangling references (left in place on
purpose: PackageManagerService skips unknown packages with a warning; they
cannot break boot).

Emits a JSON report (--json): planned/removed entries, freed bytes, packages,
dangling refs.  Unit-tested in scripts/tests/test_debloat_system.py.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import sys
from typing import Any, Dict, List, Optional, Set

SAFE_PATH = re.compile(r"^(app|priv-app|vendor/app|vendor/priv-app)/[^/]+$")


def load_list(path: str) -> List[str]:
    out: List[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s.startswith("/") or "\\" in s:
                raise ValueError(f"{path}:{ln}: path must be system-relative: {s!r}")
            out.append(s)
    return out


def dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for fn in files:
            try:
                total += os.lstat(os.path.join(root, fn)).st_size
            except OSError:
                pass
    return total


def validate(mount: str, entries: List[str]) -> None:
    seen: Set[str] = set()
    for e in entries:
        if not SAFE_PATH.match(e):
            raise ValueError(f"refusing unsafe removal path: {e!r}")
        if e in seen:
            raise ValueError(f"duplicate entry in removal list: {e!r}")
        seen.add(e)
        p = os.path.join(mount, e)
        if not os.path.isdir(p):
            raise FileNotFoundError(f"removal target not a directory: {e!r}")
        if os.path.islink(p):
            raise ValueError(f"removal target is a symlink: {e!r}")


def packages_of(mount: str, entries: List[str]) -> List[Dict[str, str]]:
    """Best-effort package name of each entry (first APK's manifest)."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import fw_inventory  # noqa: E402
    out = []
    for e in entries:
        p = os.path.join(mount, e)
        pkg = None
        apks = sorted(f for f in os.listdir(p)
                      if f.endswith(".apk")) if os.path.isdir(p) else []
        if apks:
            pkg = fw_inventory.manifest_apk(os.path.join(p, apks[0])).get("package")
        out.append({"path": e, "package": pkg or os.path.basename(e)})
    return out


def dangling_refs(mount: str, pkgs: Set[str]) -> List[Dict[str, str]]:
    hits = []
    pats = [os.path.join(mount, "etc/permissions/*.xml"),
            os.path.join(mount, "etc/preferred-apps/*.xml"),
            os.path.join(mount, "etc/sysconfig/*.xml")]
    for pat in pats:
        for xf in sorted(glob.glob(pat)):
            try:
                with open(xf, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            for pkg in sorted(pkgs):
                if pkg and pkg in text:
                    hits.append({"file": os.path.relpath(xf, mount), "package": pkg})
    return hits


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mount", required=True, help="mount point of system.img")
    ap.add_argument("--list", required=True, help="removal list file")
    ap.add_argument("--json", help="write the JSON report here (and stdout)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    mount = os.path.abspath(args.mount)
    if not os.path.isdir(mount):
        print(f"[debloat] ERROR: no such mount dir: {mount}", file=sys.stderr)
        return 2
    try:
        entries = load_list(args.list)
    except ValueError as e:
        print(f"[debloat] ERROR: {e}", file=sys.stderr)
        return 2
    if not entries:
        print("[debloat] ERROR: removal list is empty -- nothing to do "
              "(curate debloat/remove-list.txt first)", file=sys.stderr)
        return 2
    try:
        validate(mount, entries)
    except (ValueError, FileNotFoundError) as e:
        print(f"[debloat] ERROR: {e}", file=sys.stderr)
        return 2

    pkgs = packages_of(mount, entries)
    sizes = {e: dir_size(os.path.join(mount, e)) for e in entries}
    freed = sum(sizes.values())
    report: Dict[str, Any] = {
        "mount": mount,
        "planned": [{"path": e, "package": p["package"], "bytes": sizes[e]}
                    for e, p in zip(entries, pkgs)],
        "freed_bytes": freed,
        "dry_run": args.dry_run,
    }
    for rec in report["planned"]:
        print(f"[debloat] {'DRY ' if args.dry_run else ''}remove "
              f"{rec['path']}  ({rec['package']}, {rec['bytes']} B)")
    if not args.dry_run:
        for e in entries:
            shutil.rmtree(os.path.join(mount, e))
        missing = [e for e in entries if os.path.exists(os.path.join(mount, e))]
        if missing:
            print(f"[debloat] ERROR: still present after removal: {missing}",
                  file=sys.stderr)
            return 1
    report["dangling_refs"] = dangling_refs(
        mount, {p["package"] for p in pkgs})
    for h in report["dangling_refs"]:
        print(f"[debloat] note: {h['file']} still mentions {h['package']} "
              "(left in place; PMS ignores unknown packages)")
    text = json.dumps(report, indent=1, ensure_ascii=False)
    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)) or ".",
                    exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
