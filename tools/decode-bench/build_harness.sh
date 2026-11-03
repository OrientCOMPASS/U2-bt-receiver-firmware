#!/usr/bin/env bash
# build_harness.sh <tag> <objdir> [ldacdec_srcdir] [ldacdec_extra_flags]
#
# Builds a STATIC armv7 codec_harness (runs under qemu-arm-static or on the
# U2 itself) that links the decoder objects from <objdir>:
#   - embdrv SBC encoder+decoder objects
#   - all FDK AAC objects (aac_*.o)
#   - libldacdec objects from <objdir>, OR freshly compiled from
#     [ldacdec_srcdir] when given (Phase B optimized decoder), with
#     [ldacdec_extra_flags] (e.g. "-O2 -mfpu=neon-vfpv4").
# The Sony LDAC *encoder* (test-stream generation) is compiled once into
# $W/harness/obj_ldacenc and shared by all variants.
set -eu
TAG=$1; OBJ=$2; LDAC_SRC=${3:-}; LDAC_EXTRA=${4:-}
W="${BTW_BUILD:-/tmp/btw-build}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
HB="$W/harness/$TAG"
mkdir -p "$HB"
# shellcheck source=/dev/null
source "$W/env.sh"

HARNESS_INC=( "${BT_INC[@]}" "${AAC_INC[@]}" -I"$W/src/libldacdec" -I/tmp/libldac81/inc )

# ---- 0) shared stubs (static bionic has no liblog) ----
STUB="$W/harness/stubs_harness.c"
cat > "$STUB" <<'EOF'
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
int __android_log_print(int prio, const char *tag, const char *fmt, ...)
{ (void)prio; va_list ap; va_start(ap, fmt);
  fprintf(stderr, "[%s] ", tag ? tag : "?"); vfprintf(stderr, fmt, ap);
  fputc('\n', stderr); va_end(ap); return 0; }
int __android_log_write(int prio, const char *tag, const char *text)
{ (void)prio; fprintf(stderr, "[%s] %s\n", tag ? tag : "?", text ? text : ""); return 0; }
int __android_log_vprint(int prio, const char *tag, const char *fmt, va_list ap)
{ (void)prio; fprintf(stderr, "[%s] ", tag ? tag : "?"); vfprintf(stderr, fmt, ap);
  fputc('\n', stderr); return 0; }
void __android_log_assert(const char *cond, const char *tag, const char *fmt, ...)
{ fprintf(stderr, "ASSERT %s (%s)\n", cond ? cond : "", tag ? tag : ""); abort(); }
/* Force PT_TLS alignment >= 32 for static bionic (NDK static-link quirk:
   "TLS segment is underaligned: alignment is 8, needs 32 for ARM Bionic") */
static __thread char tls_align_forcer[32] __attribute__((aligned(32)));
void *stub_tls_anchor(void) { return &tls_align_forcer; }

/* SBC encoder (embdrv) trace glue */
int appl_trace_level = 0;
void LogMsg(const char *fmt, ...) { (void)fmt; }
/* Android event-log writer referenced by lpp_tran.cpp */
int __android_log_error_write(int log_id, const char *tag, long serial,
                              const char *method, long method_type,
                              const char *error_msg)
{ (void)log_id;(void)tag;(void)serial;(void)method;(void)method_type;(void)error_msg; return 0; }
EOF
$CC "${TARGET[@]}" "${SYSROOT[@]}" -O1 -fno-emulated-tls -c "$STUB" -o "$HB/stubs.o"

# ---- 1) harness main: FIXED flags across variants (only lib objects differ) ----
$CCXX "${TARGET[@]}" "${SYSROOT[@]}" -std=gnu++14 -O1 -ffp-contract=off \
    "${HARNESS_INC[@]}" -c "$REPO/tools/decode-bench/codec_harness.cpp" -o "$HB/harness.o"

# ---- 2) Sony LDAC encoder objects (shared, built once) ----
ENC="$W/harness/obj_ldacenc"
mkdir -p "$ENC"
if [ -z "$(ls -A "$ENC" 2>/dev/null)" ]; then
  echo "[harness] compiling Sony libldacBT_enc (test-stream generator)"
  # UNITY build per Sony Android.bp: only ldaclib.c + ldacBT.c are TUs;
  # they #include all tables/impl .c files themselves.
  for f in ldaclib.c ldacBT.c; do
    $CC "${TARGET[@]}" "${SYSROOT[@]}" -std=gnu99 -O2 -I/tmp/libldac81/inc \
        -I/tmp/libldac81/src -Wno-unused-result -Wno-absolute-value -Wno-parentheses \
        -c "/tmp/libldac81/src/$f" -o "$ENC/${f%.c}.o" || \
        { echo "[harness] sony-enc compile FAILED: $f"; exit 1; }
  done
fi

# ---- 3) libldacdec objects: from OBJDIR or from custom source dir ----
LD_OBJS=()
if [ -n "$LDAC_SRC" ]; then
  echo "[harness] compiling libldacdec variant: $LDAC_SRC ($LDAC_EXTRA)"
  VDIR="$HB/ldacdec_objs"; mkdir -p "$VDIR"; rm -f "$VDIR"/*.o
  for f in "$LDAC_SRC"/*.c; do
    # shellcheck disable=SC2086
    $CC "${TARGET[@]}" "${SYSROOT[@]}" -std=gnu99 -O2 -ffp-contract=off \
        $LDAC_EXTRA -I"$LDAC_SRC" -c "$f" -o "$VDIR/$(basename "$f").o"
  done
  mapfile -t LD_OBJS < <(find "$VDIR" -name '*.o' | sort)
else
  mapfile -t LD_OBJS < <(find "$OBJ" -name 'libldacdec_*.o' | sort)
fi

# ---- 4) link (static) ----
mapfile -t SBC_OBJS < <(find "$OBJ" -name 'bt_embdrv_sbc_*.o' | sort)
mapfile -t AAC_OBJS < <(find "$OBJ" -name 'aac_*.o' | sort)
mapfile -t ENC_OBJS < <(find "$ENC" -name '*.o' | sort)
echo "[harness] linking harness_$TAG (sbc=${#SBC_OBJS[@]} aac=${#AAC_OBJS[@]} ldacdec=${#LD_OBJS[@]} ldacenc=${#ENC_OBJS[@]})"
$CCXX "${TARGET[@]}" "${SYSROOT[@]}" -static -static-libstdc++ \
  -o "$HB/harness_$TAG" \
  "$HB/harness.o" "$HB/stubs.o" \
  "${SBC_OBJS[@]}" "${AAC_OBJS[@]}" "${LD_OBJS[@]}" "${ENC_OBJS[@]}" \
  -L"$NDK/sysroot/usr/lib/arm-linux-androideabi" -lm -lc
"$NDK/bin/llvm-strip" -o "$HB/harness_$TAG.stripped" "$HB/harness_$TAG" 2>/dev/null || true
ls -la "$HB/harness_$TAG"*
echo "[harness] OK: $HB/harness_$TAG"
