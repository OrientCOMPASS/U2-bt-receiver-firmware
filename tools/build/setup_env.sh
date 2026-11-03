#!/usr/bin/env bash
# setup_env.sh — 在全新沙箱会话里重建构建环境（/tmp 每轮会话都会被清空）
#
# 幂等：已存在的部分自动跳过。全程约 5–10 分钟（取决于网速）。
# 需要的持久化输入（都在工作区里，不进 /tmp）：
#   <workspace>/devlibs/   从设备 pull 下来的 .so（链接输入 + 符号审计基准）
#   <repo>/src/            vendored AOSP system/bt @ android-7.0.0_r36
#   <repo>/external/       vendored tinyxml2（后续还有 fdk-aac / libldac）
#
# 用法： bash tools/build/setup_env.sh [--with-ndk] [--with-aosp] [--with-protoc] [--all]
set -u

NDK_VER="r21e"
NDK_ZIP="android-ndk-${NDK_VER}-linux-x86_64.zip"
NDK_URL="https://dl.google.com/android/repository/${NDK_ZIP}"
NDK_DIR="/tmp/ndk/android-ndk-${NDK_VER}"
AOSP_DIR="/tmp/build"
PROTOC_DIR="/tmp/protoc_build/protobuf-2.6.1"
TAG="android-7.0.0_r36"          # 铁律：永远显式指定 tag
GS="https://android.googlesource.com"

DO_NDK=0; DO_AOSP=0; DO_PROTOC=0; DO_APT=0
for a in "$@"; do
  case "$a" in
    --with-ndk)   DO_NDK=1 ;;
    --with-aosp)  DO_AOSP=1 ;;
    --with-protoc)DO_PROTOC=1 ;;
    --with-apt)   DO_APT=1 ;;
    --all)        DO_NDK=1; DO_AOSP=1; DO_PROTOC=1; DO_APT=1 ;;
    *) echo "未知参数 $a"; exit 2 ;;
  esac
done
if [ $# -eq 0 ]; then DO_NDK=1; DO_AOSP=1; DO_PROTOC=1; DO_APT=1; fi

log(){ echo "[setup] $*"; }

if [ "$DO_APT" = 1 ]; then
  log "安装宿主工具链（build-essential/cmake/binutils/unzip…）"
  command -v gcc >/dev/null || apt-get update -qq
  apt-get install -y -qq build-essential cmake autoconf automake libtool pkg-config \
      ninja-build binutils unzip tar python3 python3-pip >/tmp/setup_apt.log 2>&1
  log "  apt rc=$?（详见 /tmp/setup_apt.log）"
fi

if [ "$DO_NDK" = 1 ]; then
  if [ -x "$NDK_DIR/toolchains/llvm/prebuilt/linux-x86_64/bin/clang" ]; then
    log "NDK 已就位：$NDK_DIR"
  else
    log "下载 NDK $NDK_VER（约 1.19GB，实测 ~13MB/s）"
    mkdir -p /tmp/ndk
    [ -f "/tmp/$NDK_ZIP" ] || python3 -c "
import urllib.request; urllib.request.urlretrieve('$NDK_URL','/tmp/$NDK_ZIP')"
    log "解压 NDK …"
    unzip -q -o "/tmp/$NDK_ZIP" -d /tmp/ndk
    log "  clang: $("$NDK_DIR/toolchains/llvm/prebuilt/linux-x86_64/bin/clang" --version | head -1)"
  fi
fi

# AOSP 头文件树（只浅克隆，不 checkout 别的分支，绝不自动回退文件）
clone_aosp(){ # repo ref dir
  local repo="$1" ref="$2" dir="$3"
  if [ -d "$AOSP_DIR/$dir/.git" ]; then log "  已有 $dir"; return; fi
  log "  clone $dir @ $ref"
  git clone --quiet --depth 1 --single-branch --branch "$ref" "$GS/$repo" "$AOSP_DIR/$dir" \
    || log "  !! clone 失败：$repo @ $ref"
}
if [ "$DO_AOSP" = 1 ]; then
  log "拉取 AOSP 头文件树 @ $TAG（aosp-mirror 缺仓库，一律用 googlesource）"
  mkdir -p "$AOSP_DIR"
  clone_aosp platform/system/core            "$TAG" system_core
  clone_aosp platform/system/media           "$TAG" system_media
  clone_aosp platform/bionic                 "$TAG" bionic
  clone_aosp platform/frameworks/av          "$TAG" frameworks_av
  clone_aosp platform/frameworks/native      "$TAG" frameworks_native
  clone_aosp platform/hardware/libhardware   "$TAG" hardware_libhardware
  clone_aosp platform/libnativehelper        "$TAG" libnativehelper
  clone_aosp platform/external/libcxx        "$TAG" external_libcxx
  clone_aosp platform/external/libcxxabi     "$TAG" external_libcxxabi
  clone_aosp platform/external/libchrome     "$TAG" external_libchrome
  clone_aosp platform/external/protobuf      "$TAG" external_protobuf
  clone_aosp platform/external/zlib          "$TAG" external_zlib
  clone_aosp platform/external/tinyxml2      "$TAG" external_tinyxml2_ref
  # 参考蓝本（android12-release）：只读参考，不参与编译
  clone_aosp platform/system/bt              android12-release system_bt_a12
fi

if [ "$DO_PROTOC" = 1 ]; then
  if [ -x "$PROTOC_DIR/src/protoc" ]; then
    log "protoc 已就位：$("$PROTOC_DIR/src/protoc" --version)"
  else
    log "构建 host protoc 2.6.1（必须与设备 libprotobuf-cpp-full 的 2.6.x 运行时同代）"
    mkdir -p /tmp/protoc_build
    if [ ! -f /tmp/protobuf-2.6.1.tar.bz2 ]; then
      python3 -c "
import urllib.request
urllib.request.urlretrieve('https://github.com/protocolbuffers/protobuf/releases/download/v2.6.1/protobuf-2.6.1.tar.bz2','/tmp/protobuf-2.6.1.tar.bz2')"
    fi
    tar xjf /tmp/protobuf-2.6.1.tar.bz2 -C /tmp/protoc_build
    ( cd "$PROTOC_DIR" && ./configure --disable-shared >/tmp/protoc_cfg.log 2>&1 \
      && cd src && make -j2 protoc >/tmp/protoc_make.log 2>&1 )
    log "  protoc: $("$PROTOC_DIR/src/protoc" --version 2>&1)"
  fi
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
WS="$(dirname "$REPO")"
log "环境自检："
for p in "$NDK_DIR" "$AOSP_DIR" "$PROTOC_DIR/src/protoc" "$WS/devlibs" "$REPO/src" "$REPO/bld/env.json"; do
  if [ -e "$p" ]; then log "  OK   $p"; else log "  MISS $p"; fi
done
log "下一步： python3 tools/build/mk2plan.py --src src --external external --aosp $AOSP_DIR \\"
log "            --buildcfg bld/bdroid_buildcfg --out bld/plan.json"
log "         python3 tools/build/build_bt.py --jobs 2"
