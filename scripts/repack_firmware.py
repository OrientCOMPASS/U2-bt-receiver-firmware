#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""repack_firmware.py -- assemble an SP_Flash_Tool-flashable KeenPlus U2 package.

Hybrid repack (the only viable path for an MTK device):

  MTK-private partitions  preloader, lk, logo, secro, trustzone(tee1/tee2),
                          cache, userdata  +  the official scatter
                          -> REUSED from the baseline firmware.  Fetched over
                             HTTP range requests (scripts/mtk_fw_zip.py) so we
                             never download the 2.46 GiB stock system.img.
  built partitions        boot.img, recovery.img, system.img
                          -> produced by the AOSP build (scripts/build.sh) and
                             passed in via --build-out.

The result is a directory (and zip) that SP_Flash_Tool can load directly: the
unmodified official MT6735M_Android_scatter.txt plus every is_download:true
partition image under the exact file_name the scatter references.  The package
is validated against the scatter (completeness + size) before zipping.

This tool cannot build AOSP (that needs a large runner / build host); --dry-run
substitutes small placeholder boot/recovery/system images so the *packaging*
path can be exercised and unit-tested end to end with the real MTK parts.

Usage:
  repack_firmware.py --baseline-url URL --build-out DIR --out u2_fw.zip
  repack_firmware.py --baseline-url URL --dry-run --out u2_fw_dryrun.zip
  repack_firmware.py --private-dir DIR --scatter S --build-out DIR --out pkg.zip
  repack_firmware.py ... --no-zip --out pkgdir/     # leave the dir unzipped
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
import zipfile
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mtk_fw_zip  # noqa: E402
import mtk_scatter  # noqa: E402

# Partitions we BUILD (replace).  Everything else the scatter downloads is a
# reused MTK-private/baseline image.
BUILT = {"boot.img", "recovery.img", "system.img"}
# Small placeholder sizes for --dry-run (well under partition caps).
DRY_SIZES = {"boot.img": 1 << 20, "recovery.img": 1 << 20, "system.img": 2 << 20}


def log(msg: str) -> None:
    print(f"[repack] {msg}")


def find_entry_by_basename(rz: "mtk_fw_zip.RemoteZip", basename: str) -> Optional[str]:
    for name in rz.names():
        if os.path.basename(name) == basename:
            return name
    return None


def fetch_private(rz: "mtk_fw_zip.RemoteZip", file_name: str, dest: str) -> int:
    entry = find_entry_by_basename(rz, file_name)
    if entry is None:
        raise FileNotFoundError(f"baseline zip has no entry for '{file_name}'")
    return rz.extract(entry, dest)


def copy_built(build_out: str, file_name: str, dest: str) -> None:
    src = os.path.join(build_out, file_name)
    if not os.path.isfile(src):
        raise FileNotFoundError(f"built image not found: {src}")
    shutil.copy2(src, dest)


def make_dummy(file_name: str, dest: str) -> None:
    size = DRY_SIZES.get(file_name, 1 << 20)
    with open(dest, "wb") as f:
        f.write(b"\x00" * size)


def assemble(baseline_url: Optional[str], private_dir: Optional[str],
             scatter_path: Optional[str], build_out: Optional[str],
             dry_run: bool, pkgdir: str, pkg_name: str) -> Dict[str, object]:
    """Populate pkgdir with scatter + all required images.  Returns a report."""
    os.makedirs(pkgdir, exist_ok=True)
    report: Dict[str, object] = {"private": [], "built": [], "dry": [], "scatter": None}

    rz: Optional[mtk_fw_zip.RemoteZip] = None
    if baseline_url:
        rz = mtk_fw_zip.RemoteZip(baseline_url)

    # 1. Scatter -- preserve the canonical file name; SP_Flash_Tool matches on it
    #    (e.g. MT6735M_Android_scatter.txt), so we must not rename it.
    if scatter_path:
        scat_name = os.path.basename(scatter_path)
        with open(scatter_path, "rb") as f:
            scat_data = f.read()
    elif rz is not None:
        scat_entry = rz.find_scatter()
        if not scat_entry:
            raise FileNotFoundError("no scatter found in baseline zip")
        scat_name = os.path.basename(scat_entry)
        scat_data = rz.read_entry(scat_entry)
    elif private_dir:
        cand = [f for f in os.listdir(private_dir)
                if f.lower().endswith("_scatter.txt")]
        if not cand:
            raise FileNotFoundError(f"no *_scatter.txt in {private_dir}")
        scat_name = cand[0]
        with open(os.path.join(private_dir, cand[0]), "rb") as f:
            scat_data = f.read()
    else:
        raise ValueError("need --baseline-url, --private-dir, or --scatter")

    scatter_out = os.path.join(pkgdir, scat_name)
    with open(scatter_out, "wb") as f:
        f.write(scat_data)
    report["scatter"] = scat_name
    sc = mtk_scatter.load_scatter(scatter_out)
    log(f"scatter: {scat_name} "
        f"(platform={sc['general'].get('platform')}, "
        f"{len(sc['partitions'])} partitions)")

    # 2. Every required image.
    required = mtk_scatter.required_files(sc)
    for fn in required:
        dest = os.path.join(pkgdir, fn)
        if fn in BUILT:
            if build_out and os.path.isfile(os.path.join(build_out, fn)):
                copy_built(build_out, fn, dest)
                report["built"].append(fn)
                log(f"built   {fn}  <- {build_out}")
            elif dry_run:
                make_dummy(fn, dest)
                report["dry"].append(fn)
                log(f"DUMMY   {fn}  (dry-run placeholder)")
            else:
                raise FileNotFoundError(
                    f"built image '{fn}' missing; run the AOSP build and pass "
                    f"--build-out, or use --dry-run to exercise packaging")
        else:
            # Private / reused baseline image.
            if private_dir and os.path.isfile(os.path.join(private_dir, fn)):
                shutil.copy2(os.path.join(private_dir, fn), dest)
            elif rz is not None:
                fetch_private(rz, fn, dest)
            else:
                raise FileNotFoundError(f"no source for private image '{fn}'")
            report["private"].append(fn)
            log(f"private {fn}  ({os.path.getsize(dest)} B)")

    if rz is not None:
        rz.close()
    return report


def zip_package(pkgdir: str, out_zip: str) -> int:
    base = os.path.basename(os.path.normpath(pkgdir))
    os.makedirs(os.path.dirname(os.path.abspath(out_zip)) or ".", exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(pkgdir):
            for name in sorted(files):
                full = os.path.join(root, name)
                rel = os.path.join(base, os.path.relpath(full, pkgdir))
                z.write(full, rel)
    return os.path.getsize(out_zip)


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline-url", help="baseline firmware zip URL (range-fetched)")
    ap.add_argument("--private-dir", help="dir with pre-fetched private imgs + scatter")
    ap.add_argument("--scatter", help="explicit scatter.txt path")
    ap.add_argument("--build-out", help="dir with built boot/recovery/system.img")
    ap.add_argument("--out", required=True, help="output .zip (or dir with --no-zip)")
    ap.add_argument("--pkg-name", default=None, help="folder name inside the zip")
    ap.add_argument("--dry-run", action="store_true",
                    help="use placeholder boot/recovery/system (test packaging)")
    ap.add_argument("--no-zip", action="store_true", help="leave the package dir unzipped")
    ap.add_argument("--keep-dir", action="store_true", help="keep the dir after zipping")
    args = ap.parse_args(argv)

    if not (args.baseline_url or args.private_dir or args.scatter):
        ap.error("need one of --baseline-url / --private-dir / --scatter")
    if args.dry_run and not (args.baseline_url or args.private_dir):
        ap.error("--dry-run still needs --baseline-url or --private-dir for the "
                 "real MTK-private partitions")

    stamp = time.strftime("%Y%m%d")
    pkg_name = args.pkg_name or f"U2_AOSP81_{stamp}"
    workdir = args.out if args.no_zip else os.path.join(
        os.path.dirname(os.path.abspath(args.out)) or ".", f".{pkg_name}_stage")
    pkgdir = os.path.join(workdir, pkg_name) if not args.no_zip else workdir
    if args.no_zip:
        pkgdir = workdir

    if os.path.isdir(pkgdir):
        shutil.rmtree(pkgdir)

    try:
        assemble(args.baseline_url, args.private_dir, args.scatter,
                 args.build_out, args.dry_run, pkgdir, pkg_name)
    except Exception as e:  # noqa: BLE001
        log(f"ERROR during assembly: {e}")
        return 1

    # Validate against the scatter.
    scat = [f for f in os.listdir(pkgdir) if f.lower().endswith("_scatter.txt")][0]
    sc = mtk_scatter.load_scatter(os.path.join(pkgdir, scat))
    res = mtk_scatter.validate_package(sc, pkgdir)
    log(f"validate: present={len(res['present'])} missing={len(res['missing'])} "
        f"oversized={len(res['oversized'])}")
    if not res["ok"]:
        if res["missing"]:
            log(f"MISSING: {res['missing']}")
        if res["oversized"]:
            log(f"OVERSIZED: {res['oversized']}")
        return 1
    log("package validated against official scatter ✓")

    if args.no_zip:
        log(f"package dir ready: {pkgdir}")
        return 0

    size = zip_package(pkgdir, args.out)
    log(f"wrote {args.out} ({size} bytes, {size/1024/1024:.2f} MiB)")
    if not args.keep_dir:
        shutil.rmtree(workdir, ignore_errors=True)
    else:
        log(f"kept staging dir: {workdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
