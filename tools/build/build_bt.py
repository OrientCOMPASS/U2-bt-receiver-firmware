#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_bt.py — U2 蓝牙接收器固件独立构建驱动（不依赖 AOSP soong/make）

产物：/system/lib/hw/bluetooth.default.so（ARM 32, Android 7.0 / API 24）

设计要点
--------
1. 源码 = 本仓库 src/（vendored AOSP platform/system/bt @ android-7.0.0_r36），
   构建前用 build/UPSTREAM.json 核对上游 tag 与 commit（只核对、只打印，
   绝不自动 checkout / 回退文件 —— 那会冲掉手改内容，属明令禁止的"自愈"）。
2. 文件清单/include/flags 来自 tools/build/mk2plan.py 对 Android.mk 的解析（plan.json），
   保证与官方构建"源码等价"，而不是自己列文件。
3. 工具链 = Android NDK r21e clang（--target=armv7a-none-linux-androideabi24），
   C++ 标准库用 AOSP external/libcxx(r36) 头 + 设备上的 /system/lib/libc++.so
   （设备 libc++ 是 std::__1 ABI，NDK 的 __ndk1 ABI 不能用）。
4. 链接对象 = 从设备 pull 下来的真实 .so（build/env 里的 DEVLIBS），
   这样 DT_NEEDED 与符号集与目标机 100% 一致。
5. 链接后做符号审计（audit_symbols.py）：所有 UND 必须在依赖库中可解析，
   否则 dlopen 到设备上必炸。
6. 产物 llvm-strip --strip-unneeded；同时保留未 strip 版供 addr2line 符号化。

用法
----
  python3 tools/build/build_bt.py --jobs 2                 # 调试构建（-O0 -g）
  python3 tools/build/build_bt.py --jobs 2 --opt 2         # 性能构建（-O2）
  python3 tools/build/build_bt.py --file btif/src/btif_av.c --verbose
  python3 tools/build/build_bt.py --link-only
环境变量（可覆盖 bld/env.json）：NDK_ROOT / AOSP_ROOT / DEVLIBS / PROTOC
"""
import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))

DEFAULT_ENV = {
    "NDK_ROOT": "/tmp/ndk/android-ndk-r21e",
    "AOSP_ROOT": "/tmp/build",          # 克隆的 AOSP 头文件树（bionic/system_core/...）
    "DEVLIBS": "/tmp/devlibs",          # 从设备 pull 下来的 .so
    "PROTOC": "/tmp/protoc_build/protobuf-2.6.1/src/protoc",
    "PROTOBUF_SRC": "",                 # 留空则用 AOSP_ROOT/external_protobuf/src
}

# bluetooth.default.so 需要的模块（来自 main/Android.mk 的 whole/static 列表）
WHOLE_STATIC = ["libbt-bta", "libbtdevice", "libbtif", "libbt-hci", "libbt-protos",
                "libbt-stack", "libbt-utils", "libbtcore", "libosi"]
STATIC_EXTRA = ["libbt-qcom_sbc_decoder"]

# 额外静态编入的 AOSP 源文件（路径相对 AOSP_ROOT）。
# hardware.c：btcore/src/hal_util.c 调用 hw_get_module()，但原厂 .so 的 DT_NEEDED 里
# 并没有 libhardware.so，且 hw_get_module 既非 UND 也未导出 → 原厂是把
# hardware/libhardware/lib/hardware.c 直接静态编入并被 -fvisibility=hidden 内部化。
# 我们照做，保证 DT_NEEDED 与原厂一致（不新增运行时依赖）。
EXTRA_AOSP_SOURCES = ["hardware_libhardware/hardware.c"]

# 链接用的设备库（DT_NEEDED 与原厂一致；libcustom_prop/libstagefright 是 MTK 追加的，
# 干净 AOSP 源码不需要，故不链接）
LINK_DEVICE_LIBS = ["libcutils", "libdl", "liblog", "libz", "libpower",
                    "libprotobuf-cpp-full", "libmedia", "libutils", "libchrome",
                    "libc++", "libc", "libm"]


def load_env():
    env = dict(DEFAULT_ENV)
    cfg = os.path.join(REPO, "bld", "env.json")
    if os.path.exists(cfg):
        with open(cfg) as fh:
            env.update(json.load(fh))
    for k in env:
        v = os.environ.get(k)
        if v:
            env[k] = v
    # /tmp 每轮会话会被清空：设备库快照优先用工作区里持久化的那份
    ws = os.path.dirname(REPO)
    for key, cands in (("DEVLIBS", [os.path.join(ws, "devlibs"), "/tmp/devlibs"]),
                       ("NDK_ROOT", ["/tmp/ndk/android-ndk-r21e", "/opt/android-ndk-r21e"]),
                       ("AOSP_ROOT", ["/tmp/build"])):
        if os.path.exists(env[key]):
            continue
        for c in cands:
            if os.path.exists(c):
                env[key] = c
                break
    if not env["PROTOBUF_SRC"]:
        env["PROTOBUF_SRC"] = os.path.join(env["AOSP_ROOT"], "external_protobuf", "src")
    return env


def aosp(env, *parts):
    return os.path.join(env["AOSP_ROOT"], *parts)


def global_includes(env):
    """AOSP 平台头文件搜索路径（顺序即优先级）。"""
    inc = [
        os.path.join(REPO, "bld", "out", "gen", "proto"),                  # 生成的 .pb.cc 自引用
        os.path.join(REPO, "bld", "out", "gen", "proto", "system", "bt"),  # 生成的 .pb.h
        os.path.join(REPO, "bld", "bdroid_buildcfg"),               # 我们的 bdroid_buildcfg.h
        os.path.join(REPO, "external", "tinyxml2"),
        aosp(env, "system_core", "include"),
        aosp(env, "system_core", "libcutils", "include"),
        aosp(env, "system_core", "liblog", "include"),
        aosp(env, "system_core", "libutils", "include"),
        aosp(env, "hardware_libhardware", "include"),
        aosp(env, "frameworks_av", "include"),
        aosp(env, "frameworks_av", "media", "libmedia", "include"),
        aosp(env, "frameworks_native", "include"),
        aosp(env, "system_media", "audio", "include"),
        aosp(env, "external_libchrome"),
        env["PROTOBUF_SRC"],
        aosp(env, "external_zlib"),
        aosp(env, "libnativehelper", "include"),
        aosp(env, "external_libcxx", "include"),
        aosp(env, "external_libcxxabi", "include"),
        os.path.join(REPO, "bld", "shim_include"),   # 必须最后：只补 NDK 缺的 linux/binder.h
    ]
    return [p for p in inc if p]


# clang 9 编译 3.8 时代代码需要放宽的告警（不改变语义）
WARN_RELAX = [
    "-Wno-unused-parameter", "-Wno-gnu-variable-sized-type-not-at-end",
    "-Wno-typedef-redefinition", "-Wno-sign-compare", "-Wno-missing-field-initializers",
    "-Wno-unused-variable", "-Wno-unused-function", "-Wno-unused-but-set-variable",
    "-Wno-deprecated-declarations", "-Wno-pointer-sign", "-Wno-int-in-bool-context",
    "-Wno-misleading-indentation", "-Wno-unknown-warning-option", "-Wno-cast-function-type",
    "-Wno-enum-compare-switch", "-Wno-shift-negative-value", "-Wno-implicit-fallthrough",
    "-Wno-#warnings", "-Wno-dangling-else", "-Wno-format-insufficient-args",
    "-Wno-sometimes-uninitialized", "-Wno-unknown-pragmas", "-Wno-comment",
]


def base_flags(env, args):
    tc = os.path.join(env["NDK_ROOT"], "toolchains", "llvm", "prebuilt", "linux-x86_64", "bin")
    sysroot = os.path.join(env["NDK_ROOT"], "toolchains", "llvm", "prebuilt", "linux-x86_64", "sysroot")
    f = [
        "--target=armv7a-none-linux-androideabi24",
        "--sysroot=" + sysroot,
        "-march=armv7-a", "-mfloat-abi=softfp", "-mfpu=neon", "-mthumb",
        "-fPIC", "-funwind-tables", "-fno-strict-aliasing",
        "-fvisibility=hidden", "-fvisibility-inlines-hidden",
        "-fno-exceptions", "-fno-rtti",
        "-nostdinc++",                       # 用 AOSP libc++ 头，不用 NDK 的 __ndk1
        "-D__ANDROID_API__=24", "-DANDROID", "-DNDEBUG" if args.opt >= 2 else "-UNDEBUG",
        "-DLOG_NDEBUG=1", "-DBUILDCFG", "-DHAS_BDROID_BUILDCFG",
        # protobuf 2.6.1 头文件里的 dynamic_cast_if_available()：本构建全程 -fno-rtti
        # （与原厂 .so 一致：动态符号表里 0 个 _ZTI*），所以走官方支持的 NO_RTTI 分支，
        # MergeFrom(const Message&) 退化为反射实现（语义等价，仅指标上报路径，非热路径）。
        "-DGOOGLE_PROTOBUF_NO_RTTI",
        '-DEXPORT_SYMBOL=__attribute__((visibility("default")))',
        "-O%d" % args.opt,
    ]
    if args.opt == 0:
        f += ["-g", "-fno-omit-frame-pointer"]
    f += WARN_RELAX
    if args.werror:
        f.append("-Werror")
    return tc, f


def map_include(env, p):
    """把 mk 里的 AOSP 相对 include 路径映射到本地目录。"""
    p = p.strip()
    if not p or p == "GEN":
        return None
    if p.startswith("/") or p.startswith(REPO):
        return p
    rel = os.path.relpath(p, REPO) if os.path.exists(p) else p
    if os.path.exists(rel):
        return rel
    # external/xxx、system/core 之类 AOSP 相对路径
    mapping = {
        "external/tinyxml2": os.path.join(REPO, "external", "tinyxml2"),
        "external/zlib": aosp(env, "external_zlib"),
        "external/libchrome": aosp(env, "external_libchrome"),
        "external/protobuf": aosp(env, "external_protobuf"),
        "system/core": aosp(env, "system_core"),
        "system/media": aosp(env, "system_media"),
        "frameworks/av": aosp(env, "frameworks_av"),
        "frameworks/native": aosp(env, "frameworks_native"),
        "hardware/libhardware": aosp(env, "hardware_libhardware"),
    }
    for k, v in mapping.items():
        if p == k or p.startswith(k + "/"):
            return os.path.join(v, p[len(k):].lstrip("/"))
    return p


def collect_sources(plan, env, args):
    """返回 [(module, abspath_or_relpath, lang)]，去重。"""
    mods = {m["name"]: m for m in plan["modules"] if not m["is_host"]}
    picked, seen = [], set()

    def add(mod_name, srcs):
        m = mods.get(mod_name)
        if not m:
            print("  !! plan.json 中找不到模块 %s" % mod_name)
            return
        for s in srcs:
            ap = s if os.path.isabs(s) else os.path.join(REPO, s)
            ap = os.path.normpath(ap)
            if not os.path.exists(ap):
                # mk 里的路径相对模块目录
                ap2 = os.path.normpath(os.path.join(REPO, m["path"], s))
                if os.path.exists(ap2):
                    ap = ap2
            if ap in seen:
                continue
            seen.add(ap)
            picked.append((mod_name, ap, m))

    add("bluetooth.default", mods["bluetooth.default"]["srcs"])
    for name in WHOLE_STATIC:
        m = mods.get(name)
        srcs = m["srcs"] if m else []
        if name == "libbt-protos":
            srcs = []  # proto 单独生成，见 gen_proto()
        add(name, srcs)
    for name in STATIC_EXTRA:
        add(name, mods[name]["srcs"])

    # tinyxml2（external，静态编入；原厂 .so 里也是静态的）
    tx = os.path.join(REPO, "external", "tinyxml2", "tinyxml2.cpp")
    if os.path.exists(tx) and tx not in seen:
        seen.add(tx)
        picked.append(("tinyxml2", tx, {"includes": [], "cflags": [], "cppflags": [], "conlyflags": []}))

    # AOSP 侧需要静态编入的零散源文件
    for rel in EXTRA_AOSP_SOURCES:
        ap = os.path.join(env["AOSP_ROOT"], rel)
        if os.path.exists(ap) and ap not in seen:
            seen.add(ap)
            picked.append(("aosp_extra", ap,
                           {"includes": [aosp(env, "hardware_libhardware", "include")],
                            "cflags": [], "cppflags": [], "conlyflags": [], "path": ""}))

    # 生成的 protobuf C++
    pb = os.path.join(REPO, "bld", "out", "gen", "proto", "system", "bt", "osi", "src", "protos", "bluetooth.pb.cc")
    if os.path.exists(pb):
        picked.append(("libbt-protos", pb, {"includes": [], "cflags": [], "cppflags": [], "conlyflags": []}))
    return picked


def includes_for(env, plan, mod, args):
    inc = list(global_includes(env))
    inc.append(os.path.join(REPO, "src"))
    for p in mod.get("includes", []):
        if p == ".":
            p = mod.get("path", "") or os.path.join(REPO, "src")
        mp = map_include(env, p)
        if mp:
            ap = mp if os.path.isabs(mp) else os.path.join(REPO, mp)
            if ap not in inc:
                inc.append(ap)
    return inc


def obj_path(src):
    rel = os.path.relpath(src, REPO)
    return os.path.join(REPO, "bld", "out", "obj", rel + ".o")


def compile_one(src, mod, env, plan, args, tc, flags, verbose=False):
    inc = includes_for(env, plan, mod, args)
    is_c = src.endswith(".c")
    cc = os.path.join(tc, "clang" if is_c else "clang++")
    extra = list(mod.get("conlyflags", [])) if is_c else list(mod.get("cppflags", []))
    extra += list(mod.get("cflags", []))
    if not is_c:
        extra = [e for e in extra]
        if not any(e.startswith("-std=") for e in extra):
            extra.append("-std=gnu++14")
    else:
        if not any(e.startswith("-std=") for e in extra):
            extra.append("-std=c99")
    # AOSP 的 -Werror / 全局 -D 由本脚本统一控制；剔除 mk 里带进来的重复或转义损坏项
    DROP_EXACT = ("-Werror", "-Wall", "-Wextra", "-UNDEBUG", "-DLOG_NDEBUG=1",
                  "-DHAS_BDROID_BUILDCFG", "-DHAS_NO_BDROID_BUILDCFG")
    DROP_PREFIX = ("-DEXPORT_SYMBOL=", "-D__ANDROID_API__", "-fvisibility=hidden")
    extra = [e for e in extra if e not in DROP_EXACT
             and not any(e.startswith(p) for p in DROP_PREFIX)]
    out = obj_path(src)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cmd = [cc] + flags + extra + ["-I" + i for i in inc] + ["-c", src, "-o", out]
    if verbose:
        print(" ".join(shlex.quote(c) for c in cmd))
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r, cmd, time.time() - t0, out


def needs_rebuild(src, out, force):
    if force or not os.path.exists(out):
        return True
    return os.path.getmtime(src) > os.path.getmtime(out)


def gen_proto(env, args):
    """用与设备 protobuf 运行时同代（2.6.1）的 protoc 生成 bluetooth.pb.{h,cc}。"""
    proto = os.path.join(REPO, "src", "osi", "src", "protos", "bluetooth.proto")
    if not os.path.exists(proto):
        print("  !! 找不到 %s" % proto)
        return False
    if not os.path.exists(env["PROTOC"]):
        print("  !! 找不到 protoc（%s）；先构建 host protoc 2.6.1" % env["PROTOC"])
        return False
    top = os.path.join(REPO, "bld", "out", "aosp_top")
    os.makedirs(os.path.join(top, "system"), exist_ok=True)
    link = os.path.join(top, "system", "bt")
    if os.path.islink(link) or os.path.exists(link):
        os.remove(link) if os.path.islink(link) else None
    if not os.path.exists(link):
        os.symlink(os.path.join(REPO, "src"), link)
    # protoc 以 --proto_path 为根保留相对路径：输出 build/gen/proto/system/bt/osi/...
    # 于是 include 根 = build/gen/proto/system/bt，与 AOSP 的 generated_sources_dir 布局一致
    outdir = os.path.join(REPO, "bld", "out", "gen", "proto")
    os.makedirs(outdir, exist_ok=True)
    cmd = [env["PROTOC"], "--proto_path=" + top, "--cpp_out=" + outdir,
           os.path.join(top, "system", "bt", "osi", "src", "protos", "bluetooth.proto")]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("  !! protoc 失败:\n%s\n%s" % (r.stdout, r.stderr))
        return False
    print("  proto 生成 OK → %s" % os.path.relpath(outdir, REPO))
    return True


def link(objs, env, args, tc):
    outdir = os.path.join(REPO, "bld", "out")
    os.makedirs(outdir, exist_ok=True)
    so = os.path.join(outdir, "bluetooth.default.so")
    cxx = os.path.join(tc, "clang++")
    devlibs = env["DEVLIBS"]
    # LINK_DEVICE_LIBS 里写的是设备上的真实文件名（libcutils.so 等），
    # 链接参数要去掉 lib 前缀与 .so 后缀 → -lcutils
    def _lname(n):
        n = n[:-3] if n.endswith(".so") else n
        n = n[3:] if n.startswith("lib") else n
        return "-l" + n
    libs = [_lname(n) for n in LINK_DEVICE_LIBS]
    cmd = [cxx, "--target=armv7a-none-linux-androideabi24",
           "--sysroot=" + os.path.join(env["NDK_ROOT"], "toolchains", "llvm", "prebuilt", "linux-x86_64", "sysroot"),
           "-shared", "-Wl,-soname,bluetooth.default.so",
           "-Wl,-Bsymbolic,-Bsymbolic-functions",
           "-nostdlib++",
           "-L" + devlibs,
           "-o", so] + objs + libs + [
           "-Wl,--build-id=sha1",
           "-Wl,-z,noexecstack", "-Wl,-z,relro",
           ]
    if not args.allow_undefined:
        cmd.insert(6, "-Wl,--no-undefined")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("链接失败:\n%s" % (r.stderr[-8000:]))
        print("CMD: %s" % " ".join(shlex.quote(c) for c in cmd))
        return None, cmd
    return so, cmd


def strip_and_report(so, env, args):
    tc_bin = os.path.join(env["NDK_ROOT"], "toolchains", "llvm", "prebuilt", "linux-x86_64", "bin")
    unstripped = so + ".unstripped"
    if not os.path.exists(unstripped) or os.path.getmtime(so) > os.path.getmtime(unstripped):
        import shutil
        shutil.copy2(so, unstripped)
    if args.no_strip:
        return so
    strip = os.path.join(tc_bin, "llvm-strip")
    r = subprocess.run([strip, "--strip-unneeded", "-o", so, unstripped], capture_output=True, text=True)
    if r.returncode != 0:
        print("strip 失败: %s" % r.stderr)
        return unstripped
    return so


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_upstream(env, args):
    f = os.path.join(REPO, "bld", "UPSTREAM.json")
    if not os.path.exists(f):
        print("!! 缺少 build/UPSTREAM.json（上游 tag/commit 记录），拒绝构建")
        return False
    with open(f) as fh:
        up = json.load(fh)
    print("上游基线：%s @ %s (%s)" % (up.get("repo"), up.get("tag"), up.get("commit", "")[:12]))
    print("          本地 src/ 快照于 %s" % up.get("imported_at", "?"))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=2, help="并发编译数（沙箱 1GB 内存 → 2）")
    ap.add_argument("--opt", type=int, default=0, help="优化等级：调试期 0，稳定性阶段 2")
    ap.add_argument("--werror", action="store_true")
    ap.add_argument("--no-strip", action="store_true")
    ap.add_argument("--force", action="store_true", help="忽略 mtime 全量重编")
    ap.add_argument("--link-only", action="store_true")
    ap.add_argument("--no-proto", action="store_true")
    ap.add_argument("--file", default=None, help="只编译单个文件（调试用，src 相对路径）")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--allow-undefined", action="store_true", help="链接时不强制 --no-undefined")
    ap.add_argument("--plan", default=os.path.join(REPO, "bld", "plan.json"))
    ap.add_argument("--skip-audit", action="store_true")
    args = ap.parse_args()

    env = load_env()
    for k in ("NDK_ROOT", "DEVLIBS"):
        if not os.path.exists(env[k]):
            print("!! %s 不存在：%s" % (k, env[k]))
            return 2
    if not check_upstream(env, args):
        return 2
    with open(args.plan) as fh:
        plan = json.load(fh)
    tc, flags = base_flags(env, args)

    if args.file:
        src = args.file if os.path.isabs(args.file) else os.path.join(REPO, args.file)
        src = os.path.normpath(src)
        # 在 plan 里找到该文件所属模块，复用其 include / flags
        mod = {"includes": [], "cflags": [], "cppflags": [], "conlyflags": [], "path": ""}
        for m in plan["modules"]:
            if m["is_host"]:
                continue
            for s in m["srcs"]:
                for cand in (s, os.path.join(m["path"], s)):
                    if os.path.normpath(os.path.join(REPO, cand)) == src:
                        mod = m
                        break
        r, cmd, dt, out = compile_one(src, mod, env, plan, args, tc, flags, verbose=True)
        sys.stdout.write(r.stdout)
        sys.stderr.write(r.stderr)
        return r.returncode

    if not args.no_proto and not args.link_only:
        if not gen_proto(env, args):
            return 2

    srcs = collect_sources(plan, env, args)
    print("待编译源文件：%d（模块 %d 个）" % (len(srcs), len({s[0] for s in srcs})))

    todo = []
    for mod_name, src, mod in srcs:
        out = obj_path(src)
        if needs_rebuild(src, out, args.force):
            todo.append((mod_name, src, mod))
    print("需要编译：%d（其余复用 build/out/obj 缓存）" % len(todo))

    failures = []
    done = [0]
    lock_print = __import__("threading").Lock()

    def work(item):
        mod_name, src, mod = item
        r, cmd, dt, out = compile_one(src, mod, env, plan, args, tc, flags, args.verbose)
        with lock_print:
            done[0] += 1
            tag = "OK " if r.returncode == 0 else "FAIL"
            sys.stdout.write("[%3d/%3d] %s %-60s %.1fs\n" % (
                done[0], len(todo), tag, os.path.relpath(src, REPO), dt))
            if r.returncode != 0:
                failures.append((src, r.stderr, cmd))
                sys.stderr.write(r.stderr[-4000:] + "\n")
            sys.stdout.flush()
        return r.returncode

    if todo:
        with ThreadPoolExecutor(max_workers=args.jobs) as ex:
            list(ex.map(work, todo))
    if failures:
        print("\n编译失败 %d 个文件（首个错误见上）" % len(failures))
        return 1

    objs = [obj_path(s) for _, s, _ in srcs]
    missing = [o for o in objs if not os.path.exists(o)]
    if missing:
        print("缺少目标文件：%s" % missing[:5])
        return 1
    print("链接 %d 个目标文件 …" % len(objs))
    so, cmd = link(objs, env, args, tc)
    if not so:
        return 1
    final = strip_and_report(so, env, args)
    print("\n=== 构建成功 ===")
    print("  产物: %s" % final)
    print("  大小: %d bytes" % os.path.getsize(final))
    print("  md5 : %s" % md5(final))
    if not args.skip_audit:
        audit = os.path.join(HERE, "audit_symbols.py")
        if os.path.exists(audit):
            rc = subprocess.call([sys.executable, audit, final, "--devlibs", env["DEVLIBS"],
                                  "--ndk", env["NDK_ROOT"]])
            if rc != 0:
                print("!! 符号审计未通过（设备上会 dlopen 失败）")
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
