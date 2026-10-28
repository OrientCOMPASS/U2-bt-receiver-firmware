#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""validate_repo.py -- structural / lint gate for the KeenPlus U2 repo.

Runs entirely from the Python standard library plus `bash -n` (for shell syntax)
so it works in a bare CI container without a full AOSP tree.  It does NOT build
anything and does NOT apply patches to source (that is scripts/apply-patches.sh
--verify's job); it catches the classes of mistake that would otherwise only
surface much later:

  * XML files that are not well-formed (overlay config.xml, a2dp policy xml)
  * shell scripts with syntax errors (bash -n) or CRLF line endings
  * Python files that do not compile
  * patch files missing the git format-patch mbox header
  * SELinux *_contexts files with malformed lines
  * fstab lines with the wrong number of fields
  * .te files with unbalanced braces / stray tabs
  * leftover merge-conflict markers anywhere
  * trailing whitespace / tabs where they should not be

Usage:
    validate_repo.py [--strict] [--quiet]
        --strict  treat warnings as failures (non-zero exit)
        --quiet   only print the summary and any failures

Exit code 0 = clean (or warnings only, unless --strict); 1 = failures.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from typing import Callable, List, Tuple

try:
    import yaml as _yaml  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    _yaml = None

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)

# Directories never to walk.
SKIP_DIRS = {".git", "__pycache__", ".work", "out", ".cache"}

CONFLICT_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")


class Report:
    def __init__(self) -> None:
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.checks_run = 0

    def err(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def tick(self) -> None:
        self.checks_run += 1


def walk_files() -> List[str]:
    """All tracked-candidate files (relative to REPO_ROOT), skipping VCS/build."""
    out: List[str] = []
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name.endswith(".pyc"):
                continue
            full = os.path.join(root, name)
            out.append(os.path.relpath(full, REPO_ROOT))
    return sorted(out)


def _read(rel: str) -> Tuple[bytes, str]:
    full = os.path.join(REPO_ROOT, rel)
    with open(full, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8", errors="replace")
    return raw, text


# --------------------------------------------------------------------------- #
# Individual checks
# --------------------------------------------------------------------------- #

def check_conflict_markers(rel: str, rep: Report) -> None:
    _, text = _read(rel)
    for i, line in enumerate(text.splitlines(), 1):
        for m in CONFLICT_MARKERS:
            # '=======' appears in some docs as a rule; only flag the 7-char
            # git markers at line start to avoid false positives.
            if line.startswith(m):
                rep.err(f"{rel}:{i}: leftover conflict marker '{m}'")


def check_crlf(rel: str, rep: Report) -> None:
    raw, _ = _read(rel)
    if b"\r\n" in raw:
        rep.err(f"{rel}: contains CRLF line endings (must be LF)")


def check_xml(rel: str, rep: Report) -> None:
    full = os.path.join(REPO_ROOT, rel)
    try:
        ET.parse(full)
        rep.tick()
    except ET.ParseError as e:
        rep.err(f"{rel}: XML not well-formed: {e}")


def check_yaml(rel: str, rep: Report) -> None:
    if _yaml is None:
        rep.warn("PyYAML not installed; skipped YAML validation for " + rel)
        return
    full = os.path.join(REPO_ROOT, rel)
    try:
        with open(full, "r", encoding="utf-8") as f:
            docs = list(_yaml.safe_load_all(f))
        rep.tick()
        # GitHub Actions workflows must define jobs with runs-on + steps.
        if rel.startswith(".github/workflows/"):
            for d in docs:
                if not isinstance(d, dict):
                    continue
                jobs = d.get("jobs")
                if jobs is None:
                    rep.err(f"{rel}: workflow has no 'jobs:'")
                    continue
                for jn, jv in jobs.items():
                    if not isinstance(jv, dict) or "runs-on" not in jv \
                            or "steps" not in jv:
                        rep.err(f"{rel}: job '{jn}' missing runs-on/steps")
    except _yaml.YAMLError as e:  # type: ignore[attr-defined]
        rep.err(f"{rel}: YAML parse error: {e}")


def check_python_compiles(rel: str, rep: Report) -> None:
    full = os.path.join(REPO_ROOT, rel)
    try:
        with open(full, "rb") as f:
            src = f.read()
        compile(src, full, "exec")
        rep.tick()
    except SyntaxError as e:
        rep.err(f"{rel}: Python syntax error: {e}")


def check_shell_syntax(rel: str, rep: Report) -> None:
    full = os.path.join(REPO_ROOT, rel)
    try:
        proc = subprocess.run(["bash", "-n", full],
                              capture_output=True, text=True, timeout=30)
        rep.tick()
        if proc.returncode != 0:
            rep.err(f"{rel}: bash -n failed:\n{proc.stderr.strip()}")
    except FileNotFoundError:
        rep.warn("bash not available; skipped shell syntax check")
    except subprocess.TimeoutExpired:
        rep.err(f"{rel}: bash -n timed out")


def check_shell_shebang(rel: str, rep: Report) -> None:
    _, text = _read(rel)
    first = text.splitlines()[0] if text else ""
    if not first.startswith("#!"):
        rep.warn(f"{rel}: executable shell script missing shebang")


def check_patch_format(rel: str, rep: Report) -> None:
    _, text = _read(rel)
    lines = text.splitlines()
    if not lines or not lines[0].startswith("From "):
        rep.err(f"{rel}: patch does not start with an mbox 'From ' line "
                "(not a git format-patch file)")
        return
    has_subject = any(l.startswith("Subject:") for l in lines[:40])
    has_diff = any(l.startswith("diff --git") or l.startswith("--- ") for l in lines)
    if not has_subject:
        rep.err(f"{rel}: patch has no Subject: header")
    if not has_diff:
        rep.err(f"{rel}: patch has no diff body")
    if has_subject and has_diff:
        rep.tick()


def check_te_balance(rel: str, rep: Report) -> None:
    _, text = _read(rel)
    depth = 0
    for i, line in enumerate(text.splitlines(), 1):
        code = line.split("#", 1)[0]
        if "\t" in code:
            rep.warn(f"{rel}:{i}: tab character in .te rule (use spaces)")
        depth += code.count("{") - code.count("}")
        if depth < 0:
            rep.err(f"{rel}:{i}: unbalanced '}}' (depth went negative)")
            depth = 0
    if depth != 0:
        rep.err(f"{rel}: unbalanced braces (final depth {depth})")
    else:
        rep.tick()


_CTX_LINE = re.compile(r"^\S+\s+u:object_r:[a-z0-9_]+:s0\s*$")
_GENFS_LINE = re.compile(r"^genfscon\s+\S+\s+\S+\s+u:object_r:[a-z0-9_]+:s0\s*$")
_PROP_LINE = re.compile(r"^\S+\s+u:object_r:[a-z0-9_]+:s0\s*$")


def check_contexts(rel: str, rep: Report) -> None:
    _, text = _read(rel)
    base = os.path.basename(rel)
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if base == "genfs_contexts":
            ok = bool(_GENFS_LINE.match(s))
        elif base in ("file_contexts", "service_contexts"):
            ok = bool(_CTX_LINE.match(s))
        elif base == "property_contexts":
            ok = bool(_PROP_LINE.match(s))
        else:
            ok = True
        if not ok:
            rep.err(f"{rel}:{i}: malformed context line: {s!r}")
        else:
            rep.tick()


def check_fstab(rel: str, rep: Report) -> None:
    _, text = _read(rel)
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        fields = s.split()
        # <src> <mnt_point> <type> <mnt_flags> [<fs_mgr_flags>]
        if len(fields) < 4:
            rep.err(f"{rel}:{i}: fstab entry has {len(fields)} fields (need >=4): {s!r}")
        else:
            rep.tick()


def check_makefile_parens(rel: str, rep: Report) -> None:
    _, text = _read(rel)
    joined = "\n".join(l.split("#", 1)[0] for l in text.splitlines())
    opens = joined.count("$(") + joined.count("${")
    closes = joined.count(")") + joined.count("}")
    # Not exact (some } belong to ${}), so only flag gross imbalance.
    if abs(opens - closes) > 2:
        rep.warn(f"{rel}: possible unbalanced make variables "
                 f"(opens~{opens}, closes~{closes})")
    else:
        rep.tick()


def check_trailing_ws(rel: str, rep: Report) -> None:
    _, text = _read(rel)
    for i, line in enumerate(text.splitlines(), 1):
        if line != line.rstrip():
            # Only a warning; many generated files tolerate it.
            rep.warn(f"{rel}:{i}: trailing whitespace")
            break  # one warning per file is enough


# --------------------------------------------------------------------------- #
# Dispatcher: map extension / basename -> checks
# --------------------------------------------------------------------------- #

UNIVERSAL: List[Callable[[str, Report], None]] = [
    check_conflict_markers,
    check_crlf,
]

BY_EXT: dict = {
    ".xml": [check_xml],
    ".yml": [check_yaml],
    ".yaml": [check_yaml],
    ".py": [check_python_compiles],
    ".sh": [check_shell_syntax, check_shell_shebang],
    ".patch": [check_patch_format],
    ".te": [check_te_balance],
    ".mk": [check_makefile_parens],
}

BY_BASENAME: dict = {
    "file_contexts": [check_contexts],
    "property_contexts": [check_contexts],
    "service_contexts": [check_contexts],
    "genfs_contexts": [check_contexts],
    "fstab.mt6735": [check_fstab],
}


def _is_binary(rel: str) -> bool:
    """True for binary blobs (e.g. device/keenplus/u2/prebuilt/kernel).

    Text-lint checks (CRLF, conflict markers, trailing whitespace) are
    meaningless -- and produce false positives -- on binaries such as the
    committed prebuilt MTK kernel (gzip Image + appended DTB).
    """
    raw, _ = _read(rel)
    return b"\x00" in raw[:8192]


def validate(files: List[str], rep: Report) -> None:
    for rel in files:
        if _is_binary(rel):
            print(f"validate_repo: {rel}: binary file, text checks skipped")
            continue
        base = os.path.basename(rel)
        ext = os.path.splitext(rel)[1]
        for chk in UNIVERSAL:
            chk(rel, rep)
        for chk in BY_EXT.get(ext, []):
            chk(rel, rep)
        for chk in BY_BASENAME.get(base, []):
            chk(rel, rep)
        # Trailing-whitespace is a repo-wide warning (skip patches: context lines
        # legitimately carry leading/trailing spaces from the diff).
        if ext != ".patch":
            check_trailing_ws(rel, rep)


# --------------------------------------------------------------------------- #
# Required-file presence
# --------------------------------------------------------------------------- #

REQUIRED = [
    "README.md",
    ".gitignore",
    "docs/DEVELOPMENT_PLAN.md",
    "docs/FIRMWARE_BUILD.md",
    "docs/INCREMENTAL_BUILD.md",
    "docs/NOUGAT_BT_SWAP.md",
    "scripts/apply-patches.sh",
    "scripts/build.sh",
    "scripts/bt_standalone_build.sh",
    "scripts/measure.sh",
    "scripts/make_logo.py",
    "scripts/mtk_sparse2raw.py",
    "scripts/mtk_scatter.py",
    "scripts/mtk_fw_zip.py",
    "scripts/repack_firmware.py",
    "scripts/publish_release.py",
    "scripts/gen-vendor-mk.py",
    "scripts/validate_repo.py",
    "scripts/baseline_probe.py",
    "kernel/u2_8.1_config.fragment",
    "kernel/check-kernel-config.py",
    "device/keenplus/u2/BoardConfig.mk",
    "device/keenplus/u2/device.mk",
    "device/keenplus/u2/u2.mk",
    "device/keenplus/u2/AndroidProducts.mk",
    "device/keenplus/u2/extract-files.sh",
    "device/keenplus/u2/proprietary-files.txt",
    "device/keenplus/u2/rootdir/fstab.mt6735",
    "device/keenplus/u2/rootdir/enableswap.sh",
    "device/keenplus/u2/rootdir/init.zygote32.rc",
    "device/keenplus/u2/overlay/packages/apps/Bluetooth/res/values/config.xml",
    "device/keenplus/u2/audio/a2dp_audio_policy_configuration.xml",
    "device/keenplus/u2/sepolicy/file_contexts",
    "device/keenplus/u2/sepolicy/property_contexts",
    "device/keenplus/u2/sepolicy/u2_btaudio.te",
    "vendor/keenplus/u2/Android.mk",
    "vendor/keenplus/u2/blobs.mk",
    "vendor/keenplus/u2/README.md",
    "patches/README.md",
    ".github/workflows/ci.yml",
    ".github/workflows/build.yml",
    ".github/workflows/bt-compile.yml",
    ".github/workflows/baseline-probe.yml",
]


def check_required(rep: Report) -> None:
    for rel in REQUIRED:
        if not os.path.isfile(os.path.join(REPO_ROOT, rel)):
            rep.err(f"missing required file: {rel}")
        else:
            rep.tick()


# --------------------------------------------------------------------------- #

def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strict", action="store_true",
                    help="treat warnings as failures")
    ap.add_argument("--quiet", action="store_true",
                    help="only print the summary and failures")
    args = ap.parse_args(argv)

    files = walk_files()
    rep = Report()
    check_required(rep)
    validate(files, rep)

    n_files = len(files)
    if not args.quiet:
        print(f"validate_repo: scanned {n_files} files, ran {rep.checks_run} checks")

    # De-duplicate warnings for readability.
    seen = set()
    uniq_warn = []
    for w in rep.warnings:
        if w not in seen:
            seen.add(w)
            uniq_warn.append(w)

    if rep.errors:
        print(f"\nFAILURES ({len(rep.errors)}):")
        for e in rep.errors:
            print(f"  ERROR: {e}")
    if uniq_warn and (not args.quiet or args.strict):
        print(f"\nWARNINGS ({len(uniq_warn)}):")
        for w in uniq_warn[:80]:
            print(f"  warn: {w}")
        if len(uniq_warn) > 80:
            print(f"  ... and {len(uniq_warn) - 80} more")

    failed = bool(rep.errors) or (args.strict and bool(uniq_warn))
    print()
    if failed:
        print(f"validate_repo: FAIL ({len(rep.errors)} errors, {len(uniq_warn)} warnings"
              f"{', strict' if args.strict else ''})")
        return 1
    print(f"validate_repo: PASS ({len(uniq_warn)} warnings"
          f"{', ignored' if uniq_warn else ''})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
