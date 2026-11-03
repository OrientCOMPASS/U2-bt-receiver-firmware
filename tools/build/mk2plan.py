#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mk2plan.py — 把 AOSP system/bt (android-7.0.0_r36) 的 Android.mk 解析成构建计划 JSON。

为什么不直接用 AOSP 的 soong/make：
  * 沙箱内存 ~1GB，跑不动完整 AOSP 构建；
  * 我们只需要一个产物：/system/lib/hw/bluetooth.default.so；
  * Android.mk 的模块定义足够规整，用 200 行 mini-make 解析即可 1:1 复刻源文件清单、
    include 路径与编译开关 —— 保证与官方构建"源码等价"，而不是自己拍脑袋列文件。

解析范围（make 语义子集）：
  VAR := v / VAR += v / VAR = v      （含行尾反斜杠续行）
  $(LOCAL_PATH) $(call my-dir)       （模块目录）
  $(VAR) 递归展开                     （btifCommonSrc / bluetooth_CFLAGS 之类）
  include $(CLEAR_VARS)              （重置 LOCAL_*）
  include $(BUILD_STATIC_LIBRARY) / $(BUILD_SHARED_LIBRARY) / $(BUILD_HOST_*)
  ifeq / ifneq / else / endif        （按 KNOWN_VARS 求值，未知条件取 else 分支）
  $(call all-proto-files-under,dir)  （展开为 dir 下的 *.proto）

用法：
  python3 build/mk2plan.py --src src --out build/plan.json [--only bluetooth.default]
"""
import argparse
import json
import os
import re
import sys

CLEAR_VARS = "__CLEAR_VARS__"
BUILD_RULES = {
    "BUILD_STATIC_LIBRARY": "static",
    "BUILD_SHARED_LIBRARY": "shared",
    "BUILD_HOST_STATIC_LIBRARY": "host_static",
    "BUILD_HOST_SHARED_LIBRARY": "host_shared",
    "BUILD_EXECUTABLE": "executable",
    "BUILD_HOST_EXECUTABLE": "host_executable",
    "BUILD_MULTI_PREBUILT": "prebuilt",
}
LOCAL_VARS = None  # 填充于 reset_local()


def reset_local():
    return {
        "LOCAL_PATH": "",
        "LOCAL_MODULE": "",
        "LOCAL_MODULE_CLASS": "",
        "LOCAL_MODULE_RELATIVE_PATH": "",
        "LOCAL_SRC_FILES": "",
        "LOCAL_GENERATED_SOURCES": "",
        "LOCAL_C_INCLUDES": "",
        "LOCAL_CFLAGS": "",
        "LOCAL_CONLYFLAGS": "",
        "LOCAL_CPPFLAGS": "",
        "LOCAL_SHARED_LIBRARIES": "",
        "LOCAL_STATIC_LIBRARIES": "",
        "LOCAL_WHOLE_STATIC_LIBRARIES": "",
        "LOCAL_LDLIBS": "",
        "LOCAL_IS_HOST_MODULE": "",
        "LOCAL_MODULE_TAGS": "",
        "LOCAL_REQUIRED_MODULES": "",
        "LOCAL_EXPORT_C_INCLUDE_DIRS": "",
        "LOCAL_ADDITIONAL_DEPENDENCIES": "",
        "LOCAL_CPP_EXTENSION": "",
        "LOCAL_MULTILIB": "",
        "LOCAL_WHOLE_STATIC_LIBRARIES_EXTRA": "",
    }


def split_words(text):
    return [w for w in re.split(r"\s+", text.strip()) if w]


class MiniMake:
    def __init__(self, src_root, aosp_root_map):
        self.src_root = os.path.abspath(src_root)
        # AOSP 相对路径 → 本地目录（例如 external/tinyxml2 → <repo>/external/tinyxml2）
        self.aosp_map = aosp_root_map
        self.globals = {}
        self.modules = []

    # ---------- 变量展开 ----------
    def expand(self, text, local, depth=0):
        if depth > 24:
            return text
        out = text
        out = out.replace("$(call my-dir)", local.get("LOCAL_PATH", ""))
        out = re.sub(r"\$\(call all-proto-files-under,\s*([^)]*)\)",
                     lambda m: self._all_files(local, m.group(1), (".proto",)), out)
        out = re.sub(r"\$\(call all-subdir-makefiles\)", "", out)
        out = re.sub(r"\$\(call all-c-files-under,\s*([^)]*)\)",
                     lambda m: self._all_files(local, m.group(1), (".c",)), out)
        out = re.sub(r"\$\(call all-cpp-files-under,\s*([^)]*)\)",
                     lambda m: self._all_files(local, m.group(1), (".cpp", ".cc")), out)

        def sub(m):
            name = m.group(1)
            if name in local:
                return local[name]
            if name in self.globals:
                return self.globals[name]
            return self.known(name)

        prev = None
        while prev != out:
            prev = out
            out = re.sub(r"\$\(([A-Za-z_][A-Za-z0-9_.\-]*)\)", sub, out)
        return out

    KNOWN = {
        "TARGET_BUILD_VARIANT": "user",
        "TARGET_ARCH": "arm",
        "TARGET_ARCH_VARIANT": "armv7-a",
        "TARGET_PRODUCT": "full_tek6735m_65_d_n",
        "HOST_OS": "linux",
        "HOST_ARCH": "x86",
        "generated_sources_dir": "GEN",
        "TOP_DIR": "",
    }

    def known(self, name):
        return self.KNOWN.get(name, "")

    def _all_files(self, local, subdir, exts):
        base = os.path.join(local.get("LOCAL_PATH", ""), subdir.strip())
        base = self._to_local(base)
        found = []
        for root, _dirs, files in os.walk(base):
            for f in sorted(files):
                if f.endswith(exts):
                    found.append(os.path.join(root, f))
        return " ".join(found)

    def _to_local(self, path):
        """把 mk 里的 AOSP 相对路径映射到本地目录。"""
        p = path.strip()
        if not p:
            return p
        if p.startswith("/"):
            return p
        # 已是本仓库内路径（src/... 或 external/...）
        for prefix in ("src", "external", "build"):
            if p.startswith(prefix + os.sep) or p == prefix:
                return p
        if os.path.exists(p):
            return p
        for aosp_rel, local_dir in self.aosp_map.items():
            if p == aosp_rel or p.startswith(aosp_rel + "/"):
                mapped = os.path.join(local_dir, p[len(aosp_rel):].lstrip("/"))
                return mapped
        return p

    # ---------- 条件求值 ----------
    def eval_cond(self, kind, lhs, rhs, local):
        lhs = self.expand(lhs, local).strip()
        rhs = self.expand(rhs, local).strip()
        if kind == "ifeq":
            return lhs == rhs
        if kind == "ifneq":
            return lhs != rhs
        if kind == "ifdef":
            return bool(lhs)
        if kind == "ifndef":
            return not lhs
        return False

    # ---------- 解析单个 mk ----------
    def parse_file(self, path, stop_at_subdirs=False):
        with open(path, "r", errors="replace") as fh:
            raw = fh.read()
        lines = self._join_continuations(raw)
        if stop_at_subdirs:
            for i, ln in enumerate(lines):
                if "all-subdir-makefiles" in ln:
                    lines = lines[:i]
                    break
        local = reset_local()
        local["LOCAL_PATH"] = os.path.dirname(path)
        cond_stack = []  # (active, taken)

        for line in lines:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            active = all(c[0] for c in cond_stack)

            m = re.match(r"^(ifeq|ifneq|ifdef|ifndef)\s*\((.*)\)\s*$", s) or \
                re.match(r"^(ifeq|ifneq)\s+(.*)$", s) or \
                re.match(r"^(ifdef|ifndef)\s+(.*)$", s)
            if m:
                kind = m.group(1)
                args = m.group(2)
                if kind in ("ifeq", "ifneq"):
                    args = args.strip()
                    if args.startswith("(") and args.endswith(")"):
                        args = args[1:-1]
                    parts = [p.strip() for p in re.split(r"(?<!\\),", args)]
                    lhs, rhs = (parts + [""])[:2]
                else:
                    lhs, rhs = args.strip(), ""
                if not active:
                    cond_stack.append((False, False))
                else:
                    val = self.eval_cond(kind, lhs, rhs, local)
                    cond_stack.append((val, val))
                continue
            if s in ("else", "endif"):
                if s == "else" and cond_stack:
                    was_active, taken = cond_stack[-1]
                    parent_active = all(c[0] for c in cond_stack[:-1])
                    cond_stack[-1] = (parent_active and not taken, taken)
                elif s == "endif" and cond_stack:
                    cond_stack.pop()
                continue
            if not active:
                continue

            m = re.match(r"^include\s+\$\(([A-Z_]+)\)\s*$", s)
            if m:
                rule = m.group(1)
                if rule == "CLEAR_VARS":
                    keep_path = local["LOCAL_PATH"]
                    local = reset_local()
                    local["LOCAL_PATH"] = keep_path
                elif rule in BUILD_RULES:
                    self.emit(local, BUILD_RULES[rule], path)
                continue

            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(\+?=|:=)\s*(.*)$", s)
            if m:
                name, op, val = m.group(1), m.group(2), m.group(3)
                val = self.expand(val, local)
                target = local if (name.startswith("LOCAL_") or name == "LOCAL_PATH") else self.globals
                if name not in target and name.startswith("LOCAL_"):
                    target[name] = ""
                if op == "+=":
                    target[name] = (target.get(name, "") + " " + val).strip()
                else:
                    target[name] = val
                continue
            # 其它（宏定义、$(call ...) 语句）忽略

    def emit(self, local, kind, mkfile):
        name = local.get("LOCAL_MODULE", "").strip()
        if not name:
            return
        mod = {
            "name": name,
            "kind": kind,
            "mk": os.path.relpath(mkfile, os.path.dirname(self.src_root)),
            "path": local.get("LOCAL_PATH", ""),
            "srcs": split_words(self.expand(local.get("LOCAL_SRC_FILES", ""), local)),
            "gen": split_words(self.expand(local.get("LOCAL_GENERATED_SOURCES", ""), local)),
            "includes": [os.path.normpath(x) if x != "." else os.path.normpath(local.get("LOCAL_PATH", "."))
                         for x in split_words(self.expand(local.get("LOCAL_C_INCLUDES", ""), local))],
            "cflags": split_words(self.expand(local.get("LOCAL_CFLAGS", ""), local)),
            "conlyflags": split_words(self.expand(local.get("LOCAL_CONLYFLAGS", ""), local)),
            "cppflags": split_words(self.expand(local.get("LOCAL_CPPFLAGS", ""), local)),
            "shared": split_words(self.expand(local.get("LOCAL_SHARED_LIBRARIES", ""), local)),
            "static": split_words(self.expand(local.get("LOCAL_STATIC_LIBRARIES", ""), local)),
            "whole_static": split_words(self.expand(local.get("LOCAL_WHOLE_STATIC_LIBRARIES", ""), local)),
            "ldlibs": self.expand(local.get("LOCAL_LDLIBS", ""), local).strip(),
            "relative_path": local.get("LOCAL_MODULE_RELATIVE_PATH", "").strip(),
            "is_host": bool(local.get("LOCAL_IS_HOST_MODULE", "").strip()),
        }
        self.modules.append(mod)

    @staticmethod
    def _join_continuations(raw):
        out, buf = [], ""
        for line in raw.splitlines():
            if line.rstrip().endswith("\\"):
                buf += line.rstrip()[:-1] + " "
                continue
            out.append(buf + line)
            buf = ""
        if buf:
            out.append(buf)
        return out

    def run(self):
        # 顶层 Android.mk 定义 bluetooth_CFLAGS / bluetooth_C_INCLUDES，必须先解析
        top = os.path.join(self.src_root, "Android.mk")
        self.parse_file(top, stop_at_subdirs=True)
        for root, dirs, files in os.walk(self.src_root):
            dirs[:] = [d for d in dirs if d not in (".git", "test", "tests")]
            for f in sorted(files):
                if f == "Android.mk" and os.path.join(root, f) != top:
                    self.parse_file(os.path.join(root, f))
        return self.modules


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="src", help="system/bt 源码根（本仓库 src/）")
    ap.add_argument("--out", default="bld/plan.json")
    ap.add_argument("--external", default="external", help="本仓库 external/ 目录")
    ap.add_argument("--aosp", default="/tmp/build", help="额外 AOSP 头文件树根目录")
    ap.add_argument("--buildcfg", default=None,
                    help="bdroid_buildcfg.h 所在目录（等价于 BOARD_BLUETOOTH_BDROID_BUILDCFG_INCLUDE_DIR）")
    args = ap.parse_args()

    aosp_map = {
        "external/tinyxml2": os.path.join(args.external, "tinyxml2"),
        "external/zlib": os.path.join(args.aosp, "external_zlib"),
        "external/protobuf": os.path.join(args.aosp, "external_protobuf"),
        "external/libchrome": os.path.join(args.aosp, "external_libchrome"),
        "external/fdk-aac": os.path.join(args.external, "fdk-aac"),
        "external/libldac": os.path.join(args.external, "libldac"),
        "system/core": os.path.join(args.aosp, "system_core"),
        "system/media": os.path.join(args.aosp, "system_media"),
        "frameworks/av": os.path.join(args.aosp, "frameworks_av"),
        "frameworks/native": os.path.join(args.aosp, "frameworks_native"),
        "hardware/libhardware": os.path.join(args.aosp, "hardware_libhardware"),
        "bionic": os.path.join(args.aosp, "bionic"),
    }
    if args.buildcfg:
        MiniMake.KNOWN["BOARD_BLUETOOTH_BDROID_BUILDCFG_INCLUDE_DIR"] = os.path.abspath(args.buildcfg)
    mm = MiniMake(args.src, aosp_map)
    mods = mm.run()
    with open(args.out, "w") as fh:
        json.dump({"src_root": args.src, "aosp_map": aosp_map, "modules": mods}, fh, indent=1)
    by_name = {}
    for m in mods:
        by_name.setdefault(m["name"], []).append(m)
    print("解析出 %d 个模块定义（%d 个唯一模块名）" % (len(mods), len(by_name)))
    for n in sorted(by_name):
        kinds = ",".join(sorted({x["kind"] for x in by_name[n]}))
        nsrc = sum(len(x["srcs"]) for x in by_name[n])
        print("  %-28s %-24s srcs=%d" % (n, kinds, nsrc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
