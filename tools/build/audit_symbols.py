#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audit_symbols.py — 链接期符号审计：确保产物的每个 UND 符号都能在目标机上解析。

为什么必须做：我们不是用 AOSP 整树构建，而是直接链接从设备 pull 下来的真实 .so。
一旦引用了设备上不存在的符号（例如 NDK 新版 libc 才有、或 libchrome 版本不符），
编译链接都能过，但设备 dlopen(bluetooth.default.so) 会直接失败 → BT 起不来，
现象极具迷惑性。这一步在刷入前把它挡下来。

解析范围：产物的 DT_NEEDED 列表，并递归展开这些库自己的 NEEDED（模拟 linker 的
传递解析）。搜索目录 = 设备库目录 DEVLIBS + NDK 的 API24 stub 目录。

用法：
  python3 tools/build/audit_symbols.py build/out/bluetooth.default.so \
      --devlibs /tmp/devlibs --ndk /tmp/ndk/android-ndk-r21e
退出码：0 = 全部可解析；1 = 存在无法解析的强符号引用。
"""
import argparse
import os
import re
import subprocess
import sys


def readelf(args, path):
    for cand in ("readelf", "llvm-readelf"):
        try:
            r = subprocess.run([cand] + args + [path], capture_output=True, text=True)
            if r.returncode == 0:
                return r.stdout
        except FileNotFoundError:
            continue
    raise RuntimeError("需要 readelf 或 llvm-readelf")


def needed(path):
    out = readelf(["-d", "-W"], path)
    return re.findall(r"\(NEEDED\)\s+Shared library: \[(.+?)\]", out)


def soname(path):
    out = readelf(["-d", "-W"], path)
    m = re.search(r"\(SONAME\)\s+Library soname: \[(.+?)\]", out)
    return m.group(1) if m else os.path.basename(path)


def dynsyms(path):
    """返回 (defined:set, und_strong:set, und_weak:set)"""
    out = readelf(["--dyn-syms", "-W"], path)
    defined, und_strong, und_weak = set(), set(), set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 8 or not parts[0].rstrip(":").isdigit():
            continue
        ndx = parts[6]
        bind = parts[4]
        # 去掉 bionic 的符号版本后缀（__cxa_atexit@LIBC → __cxa_atexit）
        name = parts[7].split("@")[0]
        if name.startswith("_GLOBAL_OFFSET_TABLE") or name in ("", "*ABS*", "*UND*"):
            continue
        if ndx == "UND":
            (und_weak if bind == "WEAK" else und_strong).add(name)
        else:
            defined.add(name)
    return defined, und_strong, und_weak


def find_lib(name, search_dirs, cache):
    if name in cache:
        return cache[name]
    for d in search_dirs:
        p = os.path.join(d, name)
        if os.path.exists(p):
            cache[name] = p
            return p
    cache[name] = None
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("so")
    ap.add_argument("--devlibs", required=True)
    ap.add_argument("--ndk", required=True)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    stub = os.path.join(args.ndk, "toolchains", "llvm", "prebuilt", "linux-x86_64",
                        "sysroot", "usr", "lib", "arm-linux-androideabi", "24")
    search = [args.devlibs, stub]
    cache = {}

    # 传递闭包收集所有可解析符号
    resolved = set()
    queue = list(needed(args.so))
    visited = set()
    missing_libs = []
    while queue:
        lib = queue.pop(0)
        if lib in visited:
            continue
        visited.add(lib)
        p = find_lib(lib, search, cache)
        if not p:
            missing_libs.append(lib)
            continue
        defined, _us, _uw = dynsyms(p)
        resolved |= defined
        queue.extend(needed(p))

    _def, und_strong, und_weak = dynsyms(args.so)
    bad = sorted(s for s in und_strong if s not in resolved)
    weak_missing = sorted(s for s in und_weak if s not in resolved)

    print("符号审计：%s" % os.path.basename(args.so))
    print("  DT_NEEDED: %s" % ", ".join(needed(args.so)))
    print("  递归解析库数: %d，可提供符号 %d" % (len(visited), len(resolved)))
    print("  UND 强符号: %d，其中无法解析: %d" % (len(und_strong), len(bad)))
    if missing_libs:
        print("  !! 传递依赖里本地缺 %d 个库（设备上有，只是没 pull 下来）: %s"
              % (len(missing_libs), ", ".join(missing_libs)))
    if weak_missing:
        print("  弱符号未解析（可接受，运行时为 0）: %d 个%s" % (
            len(weak_missing), " e.g. " + ", ".join(weak_missing[:5]) if args.verbose else ""))
    if bad:
        print("  !! 无法解析的强符号（刷入必炸）:")
        for s in bad[:40]:
            print("     %s" % s)
        if len(bad) > 40:
            print("     … 另有 %d 个" % (len(bad) - 40))
        return 1
    print("  ✓ 全部 UND 强符号均可在目标机解析")
    return 0


if __name__ == "__main__":
    sys.exit(main())
