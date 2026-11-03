#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
uguu.py — 沙箱 ↔ uguu.se 文件传输（沙箱侧，纯标准库）

背景：桥接通道是 SSH 替代品，只能跑命令、每轮 <=64KB，没有文件传输。
所以大件走"临时文件床"：沙箱 up → 得到 URL → Windows 侧 curl.exe 下载；
Windows 侧 tar 打包 up → 沙箱 down。

实测坑（都已在这里规避）：
  * uguu.se 按**内容**识别类型：zip/apk 一律 415 Filetype not allowed，
    哪怕改扩展名也没用 → 用 tar（application/x-tar 放行）或 base64 成 .txt。
  * ELF(.so) / .txt / .tar 放行。
  * Windows 侧 curl.exe 必须带 --ssl-no-revoke。

用法：
  python3 tools/dev/uguu.py up   <本地文件>            # 打印 URL
  python3 tools/dev/uguu.py down <url> <输出路径>
  python3 tools/dev/uguu.py md5  <本地文件>
"""
import hashlib
import json
import os
import sys
import urllib.request
import uuid


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def upload(path, endpoint="https://uguu.se/upload.php"):
    fn = os.path.basename(path)
    boundary = uuid.uuid4().hex
    with open(path, "rb") as fh:
        data = fh.read()
    head = ("--%s\r\nContent-Disposition: form-data; name=\"files[]\"; filename=\"%s\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n" % (boundary, fn)).encode()
    tail = ("\r\n--%s--\r\n" % boundary).encode()
    req = urllib.request.Request(endpoint, data=head + data + tail, headers={
        "Content-Type": "multipart/form-data; boundary=%s" % boundary,
        "User-Agent": "curl/8.0",
    })
    with urllib.request.urlopen(req, timeout=600) as r:
        j = json.loads(r.read().decode())
    if not j.get("success"):
        raise RuntimeError("上传失败: %s" % j)
    return j["files"][0]["url"]


def download(url, out):
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0"})
    with urllib.request.urlopen(req, timeout=600) as r:
        data = r.read()
    with open(out, "wb") as fh:
        fh.write(data)
    return len(data)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == "up":
        p = sys.argv[2]
        url = upload(p)
        print("URL=%s" % url)
        print("MD5=%s" % md5(p))
        print("SIZE=%d" % os.path.getsize(p))
    elif cmd == "down":
        url, out = sys.argv[2], sys.argv[3]
        n = download(url, out)
        print("OK %s (%d bytes) md5=%s" % (out, n, md5(out)))
    elif cmd == "md5":
        print(md5(sys.argv[2]))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
