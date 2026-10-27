#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""publish_release.py -- create/update a GitHub Release and upload firmware.

Used by the gated build workflow to publish the SP_Flash_Tool package produced by
scripts/repack_firmware.py.  Pure standard library (urllib).

The heavy lifting is split into small pure helpers (endpoint building, asset
de-duplication, planning) that are unit-tested without touching the network; the
HTTP calls are thin wrappers around them.

Usage:
  publish_release.py --tag v0.1.0 --name "U2 AOSP 8.1" \
      --files out/U2_AOSP81.zip --body-file notes.md [--prerelease] [--draft]
  publish_release.py --tag v0.1.0 --files fw.zip --dry-run

Auth: --token, or the GH_TOKEN / GITHUB_TOKEN environment variable.  In Actions
the built-in GITHUB_TOKEN (contents: write) is sufficient for the same repo.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple

DEFAULT_REPO = "OrientCOMPASS/U2-bt-receiver-firmware"
API = "https://api.github.com"


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested)
# --------------------------------------------------------------------------- #

def upload_endpoint(upload_url_template: str, name: str) -> str:
    """Turn GitHub's '{?name,label}' upload template into a concrete URL."""
    base = upload_url_template.split("{", 1)[0]
    q = urllib.parse.urlencode({"name": name})
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{q}"


def asset_id_for_name(assets: List[Dict], name: str) -> Optional[int]:
    for a in assets:
        if a.get("name") == name:
            return a.get("id")
    return None


def plan(files: List[str]) -> List[Tuple[str, int]]:
    """Return (path, size) for each file, raising if any is missing/empty."""
    out: List[Tuple[str, int]] = []
    for f in files:
        if not os.path.isfile(f):
            raise FileNotFoundError(f"asset not found: {f}")
        sz = os.path.getsize(f)
        if sz == 0:
            raise ValueError(f"asset is empty: {f}")
        out.append((f, sz))
    return out


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

def _req(url: str, token: str, method: str = "GET", data: Optional[bytes] = None,
         ctype: Optional[str] = None, accept: str = "application/vnd.github+json"):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "u2-publish-release",
    }
    if ctype:
        headers["Content-Type"] = ctype
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            body = resp.read()
            return resp.getcode(), body
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _json(body: bytes):
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return {}


def get_or_create_release(repo: str, tag: str, name: str, body: str,
                          draft: bool, prerelease: bool, token: str) -> Dict:
    code, resp = _req(f"{API}/repos/{repo}/releases/tags/{urllib.parse.quote(tag)}",
                      token)
    if code == 200:
        rel = _json(resp)
        print(f"[publish] release for tag '{tag}' exists (id={rel.get('id')})")
        return rel
    payload = json.dumps({
        "tag_name": tag, "name": name or tag, "body": body,
        "draft": draft, "prerelease": prerelease,
    }).encode()
    code, resp = _req(f"{API}/repos/{repo}/releases", token, "POST",
                      payload, "application/json")
    if code not in (200, 201):
        raise RuntimeError(f"create release failed HTTP {code}: {resp[:400]!r}")
    rel = _json(resp)
    print(f"[publish] created release '{tag}' (id={rel.get('id')})")
    return rel


def upload_asset(repo: str, rel: Dict, path: str, token: str) -> None:
    name = os.path.basename(path)
    rid = rel["id"]
    upload_tmpl = rel.get("upload_url") or \
        f"https://uploads.github.com/repos/{repo}/releases/{rid}/assets{{?name,label}}"
    # Delete a pre-existing asset of the same name (re-runs).
    code, resp = _req(f"{API}/repos/{repo}/releases/{rid}/assets", token)
    if code == 200:
        aid = asset_id_for_name(_json(resp), name)
        if aid:
            dcode, _ = _req(f"{API}/repos/{repo}/releases/assets/{aid}",
                            token, "DELETE")
            print(f"[publish] deleted stale asset '{name}' (id={aid}) HTTP {dcode}")
    with open(path, "rb") as f:
        data = f.read()
    url = upload_endpoint(upload_tmpl, name)
    code, resp = _req(url, token, "POST", data, "application/octet-stream")
    if code not in (200, 201):
        raise RuntimeError(f"upload '{name}' failed HTTP {code}: {resp[:400]!r}")
    print(f"[publish] uploaded '{name}' ({len(data)} bytes)")


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--name", default="")
    ap.add_argument("--body", default="")
    ap.add_argument("--body-file", default="")
    ap.add_argument("--files", nargs="+", required=True)
    ap.add_argument("--token", default="")
    ap.add_argument("--draft", action="store_true")
    ap.add_argument("--prerelease", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    body = args.body
    if args.body_file and os.path.isfile(args.body_file):
        with open(args.body_file, "r", encoding="utf-8") as f:
            body = f.read()

    try:
        planned = plan(args.files)
    except (FileNotFoundError, ValueError) as e:
        print(f"[publish] ERROR: {e}", file=sys.stderr)
        return 1

    print(f"[publish] repo={args.repo} tag={args.tag} draft={args.draft} "
          f"prerelease={args.prerelease}")
    for path, sz in planned:
        print(f"[publish]   asset {os.path.basename(path)} ({sz} bytes, "
              f"{sz/1024/1024:.2f} MiB)")

    if args.dry_run:
        print("[publish] dry-run: no API calls made")
        return 0

    token = args.token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("[publish] ERROR: no token (--token or GH_TOKEN/GITHUB_TOKEN)",
              file=sys.stderr)
        return 2

    try:
        rel = get_or_create_release(args.repo, args.tag, args.name or args.tag,
                                    body, args.draft, args.prerelease, token)
        for path, _ in planned:
            upload_asset(args.repo, rel, path, token)
    except Exception as e:  # noqa: BLE001
        print(f"[publish] ERROR: {e}", file=sys.stderr)
        return 1

    print(f"[publish] done: {rel.get('html_url')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
