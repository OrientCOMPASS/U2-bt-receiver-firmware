# docs/02 — 构建系统

## 0. 一句话

**不用 AOSP soong/make**，而是：`mk2plan.py` 解析 `src/` 里的官方 `Android.mk` 得到与原厂"源码等价"的编译计划 → `build_bt.py` 用 NDK r21e clang 独立编译链接 → `audit_symbols.py` 做刷入前符号审计。

## 1. 目录约定

```
repo/
├── src/                     vendored AOSP platform/system/bt @ android-7.0.0_r36
│                            （之后所有改动都属于本项目，按阶段独立 commit）
├── external/                vendored 第三方（tinyxml2；P2 加 fdk-aac，P3 加 libldac）
├── bld/                     ★构建配置（进版本库）
│   ├── plan.json            mk2plan.py 产物：文件清单 + include + flags
│   ├── UPSTREAM.json        上游 tag/commit/工具链记录
│   ├── env.json             NDK_ROOT/AOSP_ROOT/DEVLIBS/PROTOC 路径
│   ├── bdroid_buildcfg/     平台构建配置（-DHAS_BDROID_BUILDCFG）
│   └── shim_include/        NDK sysroot 缺的头（linux/binder.h，原样取自 bionic r36）
└── bld/out/                 ★构建产物（.gitignore，且不进工作区快照）
    ├── aosp_top/  gen/proto/  obj/
    ├── bluetooth.default.so          strip 后（刷入用）
    ├── bluetooth.default.so.unstripped  符号化用（addr2line / nm）
    └── bt_p0_source_equiv.so       P0 基线留存副本
```

> ⚠️ 目录名不能叫 `build/`、`out/`、`dist/`、`target/`：**工作区快照会排除这些名字**，内容会莫名消失。所以配置目录叫 `bld/`。

## 2. 全新会话的启动顺序

```bash
# 1) 重建 /tmp 环境（NDK + AOSP 头树 + host protoc + apt 工具）
bash tools/build/setup_env.sh --all          # 约 5–10 分钟

# 2) devlibs 已持久化在工作区（50 个 .so + sepolicy/file_contexts）
#    build_bt.py 的 load_env() 会自动回退到 <workspace>/devlibs

# 3) 生成编译计划（Android.mk 变了才需要重跑）
python3 tools/build/mk2plan.py --src src --external external \
        --aosp /tmp/build --buildcfg bld/bdroid_buildcfg --out bld/plan.json

# 4) 构建（312 obj，约 3–6 分钟）
python3 tools/build/build_bt.py --jobs 2

# 5) 刷入前符号审计（必须 0 个未解析强符号）
python3 tools/build/audit_symbols.py bld/out/bluetooth.default.so \
        --devlibs devlibs --ndk /tmp/ndk/android-ndk-r21e
```

`build_bt.py` 常用开关：`--opt 2`（性能构建，默认 `-O0 -g`）、`--force`（全量重编）、`--file <path>`（只编一个文件）、`--link-only`、`--verbose`、`--no-proto`、`--skip-audit`。

## 3. 工具链与编译参数（实测锁定）

| 项 | 值 |
|---|---|
| 编译器 | NDK **r21e** clang / clang++（`android-9`） |
| Target | `armv7a-none-linux-androideabi24` |
| Arch flags | `-march=armv7-a -mfloat-abi=softfp -mfpu=neon -mthumb` |
| RTTI / 异常 | `-fno-rtti -fno-exceptions`（与原厂一致：产物里 0 个 `_ZTI*`） |
| protobuf | `-DGOOGLE_PROTOBUF_NO_RTTI`（2.6.1 的 `dynamic_cast` 需要它） |
| 优化 | 调试期 `-O0 -g`；稳定期 `--opt 2` |
| C++ 标准库 | **AOSP `external/libcxx @ r36` 头**（`std::__1`）+ `-nostdinc++ -nostdlib++`，链接设备 `libc++.so`。**绝不能用 NDK 的 `__ndk1`** |
| Include 顺序 | src → external → AOSP 头树 → NDK sysroot → `bld/shim_include`（**最后**） |
| 链接器 | NDK **bfd ld**（不是 lld），`-Wl,-Bsymbolic,-Bsymbolic-functions,--no-undefined`，`-soname bluetooth.default.so` |
| 并发 | `--jobs 2`（沙箱 ~1GB 内存；写法是 `--jobs N`，不是 `-jN`） |

**产物 DT_NEEDED（必须与原厂一致）**：
`libcutils.so libdl.so liblog.so libz.so libpower.so libprotobuf-cpp-full.so libmedia.so libutils.so libchrome.so libc++.so libc.so libm.so`

**导出**：`HAL_MODULE_INFO_SYM`（HMI）+ AOSP r36 的 HMI/JNI 兼容符号集。
（实测：`com.android.bluetooth` 的 jni **不**链接 `bluetooth.default.so`，所以 Java 层不直接依赖我们的导出符号——这也是"native 侧主动注册 sink"路线可行的前提，见 docs/05。）

## 4. 链接输入 = 设备真实 .so

`devlibs/`（50 个文件，16MB）是从设备 pull 下来的运行时库快照：`bt_stock.so` + `libmedia/libutils/libcutils/libchrome/libc++/libprotobuf-cpp-full/libz/libpower/libdl/liblog/libc/libm` 等 22 个传递依赖，外加 `file_contexts.bin`、`property_contexts`、`sepolicy`。

用它做链接输入的好处：**DT_NEEDED 与符号集与目标机 100% 一致**，不会出现"沙箱编得过、设备上 dlopen 炸"。

## 5. protobuf（2.6.1，r36 时代）

- googlesource 上**没有** `platform/external/protobuf-cpp-full`；r36 用的是 `platform/external/protobuf`（**2.6.1**）。
- GitHub release 资产名是 `protobuf-2.6.1.tar.bz2`（`protobuf-cpp-*.tar.gz` 是 3.x 才有的命名）。
- 必须在 `src/` 目录内 `./configure && make protoc`（host 版），产物 `/tmp/protoc_build/protobuf-2.6.1/src/protoc`。
- 代码生成：`protoc --proto_path=<top> --cpp_out=bld/out/gen/proto`，include 根同时给 `bld/out/gen/proto` 和 `bld/out/gen/proto/system/bt`。

## 6. 上游核对（不做自动回退）

`build_bt.py` 会读 `bld/UPSTREAM.json` 打印 tag/commit 做**只读核对**。
**明令禁止**：自动 `git checkout` 上游版本 / 自动"修复"被改动的文件——那会冲掉手改内容，属于被禁止的"自愈"。

## 7. 铁律

- 一次 build-flash-test 循环**只改一个变量**；刷入后立刻记录 md5 + 行为。
- 不改与厂商二进制共享的结构体布局（`AudioTrack`：MTK libmedia 最大成员写偏移 656，AOSP r36 `sizeof`=664，**ABI 兼容，已确认，勿再验证**）。
- media 热路径尽量零新增分配；新结构体单独分配 + canary。
