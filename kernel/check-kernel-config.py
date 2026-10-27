#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit a kernel .config against the AOSP 8.1 requirement list.

Reads the required options from kernel/u2_8.1_config.fragment (the "=y"/"=m"/
"="value" lines) and checks them against a real .config.  Exits non-zero if any
REQUIRED option is missing or set differently, printing a report.  This keeps the
config checklist machine-verifiable without needing the kernel tree.

Usage:
    check-kernel-config.py <path/to/.config> [--fragment path/to/fragment]
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Dict, List, Tuple

FRAGMENT_DEFAULT = __file__.replace("check-kernel-config.py", "u2_8.1_config.fragment")

_ASSIGN = re.compile(r"^(CONFIG_[A-Z0-9_]+)=(.*)$")


def load_assignments(path: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = _ASSIGN.match(line)
            if m:
                out[m.group(1)] = m.group(2)
    return out


def load_fragment(path: str) -> List[Tuple[str, str]]:
    """Return the list of (CONFIG, value) the fragment requires."""
    reqs: List[Tuple[str, str]] = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            m = _ASSIGN.match(s)
            if m:
                reqs.append((m.group(1), m.group(2)))
    return reqs


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("config", help="path to a kernel .config")
    ap.add_argument("--fragment", default=FRAGMENT_DEFAULT,
                    help="path to the requirement fragment")
    args = ap.parse_args(argv)

    have = load_assignments(args.config)
    reqs = load_fragment(args.fragment)

    missing: List[str] = []
    mismatched: List[str] = []
    ok = 0
    for name, want in reqs:
        if name not in have:
            missing.append(f"{name}={want}")
        elif have[name] != want:
            # =m vs =y is acceptable for optional drivers like DM_VERITY.
            if have[name] in ("m", "y") and want in ("m", "y"):
                ok += 1
            else:
                mismatched.append(f"{name}: want {want}, have {have[name]}")
        else:
            ok += 1

    print(f"Audited {len(reqs)} required options against {args.config}")
    print(f"  OK         : {ok}")
    if missing:
        print(f"  MISSING    : {len(missing)}")
        for m in missing:
            print(f"      - {m}")
    if mismatched:
        print(f"  MISMATCHED : {len(mismatched)}")
        for m in mismatched:
            print(f"      - {m}")

    if missing or mismatched:
        print("\nFAIL: kernel .config does not satisfy the AOSP 8.1 requirement list.")
        return 1
    print("\nPASS: kernel .config satisfies all required options.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
