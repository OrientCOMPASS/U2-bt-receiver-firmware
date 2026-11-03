#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gh_release.py — 用 GitHub REST API 打 Release（沙箱里没有 gh，也没有 curl）

凭据只从工作区根的 CREDENTIALS.txt 读（**永不**进仓库/日志/Release 正文）。
需要 GITHUB_PAT / GITHUB_USER / GITHUB_REPO 三项。

用法：
  python3 tools/dev/gh_release.py create \\
      --tag p1-sink-sbc-baseline-20261104 \\
      --name "P1 …" \\
      --notes-file /tmp/notes.md \\
      --asset /tmp/bluetooth.default.so \\
      --asset /tmp/btsnoop.log
  python3 tools/dev/gh_release.py list
  python3 tools/dev/gh_release.py show <tag>

规矩：
  * 打 Release 前必须征得用户确认（任务书要求）。
  * tag 命名 pN-topic-date（R6）。
  * 附件里绝不放凭据、绝不放原厂固件二进制。
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
CRED = os.environ.get("RC_CRED", os.path.join(WS, "CREDENTIALS.txt"))
API = "https://api.github.com"
UPLOAD = "https://uploads.github.com"


def creds():
    if not os.path.exists(CRED):
        raise SystemExit(f"缺少凭据文件 {CRED}（不应进仓库）")
    d = {}
    with open(CRED, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip()
    for k in ("GITHUB_PAT", "GITHUB_REPO"):
        if not d.get(k):
            raise SystemExit(f"CREDENTIALS.txt 缺少 {k}")
    return d


def req(method, url, token, data=None, ctype="application/json", raw=False):
    body = None
    if data is not None:
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
    r = urllib.request.Request(url, data=body, method=method)
    r.add_header("Authorization", "token " + token)
    r.add_header("Accept", "application/vnd.github+json")
    r.add_header("User-Agent", "u2-fw-release-tool")
    if body is not None:
        r.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(r, timeout=180) as resp:
            payload = resp.read()
            return (payload if raw else json.loads(payload or b"{}")), resp.status
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf8", "replace")
        raise SystemExit(f"HTTP {e.code} {method} {url}\n{detail[:2000]}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--tag", required=True)
    c.add_argument("--name", required=True)
    c.add_argument("--notes-file", required=True)
    c.add_argument("--asset", action="append", default=[])
    c.add_argument("--branch", default="main")
    c.add_argument("--prerelease", action="store_true")
    sub.add_parser("list")
    s = sub.add_parser("show")
    s.add_argument("tag")
    a = sub.add_parser("assets")
    a.add_argument("tag")
    args = ap.parse_args()

    d = creds()
    tok, repo = d["GITHUB_PAT"], d["GITHUB_REPO"]

    if args.cmd == "list":
        rels, _ = req("GET", f"{API}/repos/{repo}/releases?per_page=20", tok)
        for r in rels:
            assets = ",".join(f"{a['name']}({a['size']}B)" for a in r.get("assets", []))
            print(f"  {r['tag_name']:42s} {r['name'][:40]:42s} {assets}")
        return 0

    if args.cmd in ("show", "assets"):
        r, _ = req("GET", f"{API}/repos/{repo}/releases/tags/{args.tag}", tok)
        print(json.dumps({k: r[k] for k in ("id", "tag_name", "name", "html_url",
                                            "target_commitish", "created_at")}, ensure_ascii=False, indent=2))
        for a in r.get("assets", []):
            print(f"  asset: {a['name']:40s} {a['size']:>10} B  {a['browser_download_url']}")
        return 0

    # create
    with open(args.notes_file, encoding="utf-8") as fh:
        notes = fh.read()
    for bad in ("GITHUB_PAT", "BRIDGE_PAIR", d["GITHUB_PAT"][:12]):
        if bad in notes:
            raise SystemExit(f"Release 正文疑似含凭据（{bad}），已中止")
    payload = {"tag_name": args.tag, "name": args.name, "body": notes,
               "target_commitish": args.branch, "draft": False,
               "prerelease": args.prerelease}
    rel, status = req("POST", f"{API}/repos/{repo}/releases", tok, payload)
    print(f"created release id={rel['id']} tag={rel['tag_name']} status={status}")
    print(f"  {rel['html_url']}")
    rid = rel["id"]
    for path in args.asset:
        if not os.path.exists(path):
            print(f"  !! 附件不存在，跳过：{path}")
            continue
        name = os.path.basename(path)
        with open(path, "rb") as fh:
            blob = fh.read()
        url = f"{UPLOAD}/repos/{repo}/releases/{rid}/assets?name={urllib.parse.quote(name)}"
        a, st = req("POST", url, tok, blob, ctype="application/octet-stream")
        print(f"  asset {name} ({len(blob)} B) → {a.get('browser_download_url')} (HTTP {st})")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
