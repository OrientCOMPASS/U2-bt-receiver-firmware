#!/usr/bin/env bash
# opt_build.sh -- rebuild ALL stack TUs with -O2 (-ffp-contract=off) into
# /tmp/btw-build-o2 and link the artifacts.  Phase A of the decode power
# optimization: same sources as the shipped -O0 build, optimized codegen.
#
# NOTE: /tmp is sandbox-ephemeral; re-run scripts/bt_standalone_build.sh
# (full setup) first if /tmp/btw-build is missing.
set -u
W="${BTW_BUILD:-/tmp/btw-build}"
V="${BTW_BUILD_OPT:-/tmp/btw-build-o2}"
JOBS=${JOBS:-2}

[ -f "$W/env.sh" ] || { echo "opt_build: $W/env.sh missing - run bt_standalone_build.sh first"; exit 1; }
mkdir -p "$V/obj" "$V/out" "$V/logs"

cat > "$V/env.sh" <<'EOF'
source "$W/env.sh"
# Phase A flags: -O0 -> -O2, FP contraction off (guarantees identical IEEE
# FP rounding vs the -O0 build => bit-exact PCM verification is meaningful).
CFLAGS=("${CFLAGS[@]/-O0/-O2}")
CFLAGS+=(-ffp-contract=off)
CXXFLAGS=("${CXXFLAGS[@]/-O0/-O2}")
CXXFLAGS+=(-ffp-contract=off)
EOF
cp "$W/compile_one.sh" "$V/compile_one.sh"
chmod +x "$V/compile_one.sh"

TOTAL=$(wc -l < "$W/tu_list.txt")
echo "[opt] compiling $TOTAL TUs with -O2 (-j$JOBS)"
awk '{print $1"\n"$2}' "$W/tu_list.txt" \
  | xargs -P"$JOBS" -n2 "$V/compile_one.sh" "$V" > "$V/logs/results.log" 2>&1 || true
P=$(grep -c '^PASS' "$V/logs/results.log" || true)
F=$(grep -c '^FAIL' "$V/logs/results.log" || true)
echo "[opt] compile result: $P/$TOTAL passed, $F failed"
if [ "$F" != 0 ]; then
  grep '^FAIL' "$V/logs/results.log" | head -20
  for e in $(grep '^FAIL' "$V/logs/results.log" | head -3 | awk '{print $2}'); do
    echo "--- $e"; head -8 "$V/obj/$(echo "$e" | tr '/' '_').err" 2>/dev/null
  done
  exit 1
fi

# ---- link (mirrors bt_standalone_build.sh link_artifacts) ----
source "$W/env.sh"
OBJ="$V/obj"; OUTDIR="$V/out"
mapfile -t aac_objs      < <(find "$OBJ" -name 'aac_*.o' | sort)
mapfile -t bt_objs       < <(find "$OBJ" -name 'bt_*.o' ! -name 'bt_audio_a2dp_hw_*' | sort)
mapfile -t ldacdec_objs  < <(find "$OBJ" -name 'libldacdec_*.o' | sort)
mapfile -t tinyxml2_objs < <(find "$OBJ" -name 'tinyxml2_*.o' | sort)
mapfile -t a2dp_objs     < <(find "$OBJ" -name 'bt_audio_a2dp_hw_src_*.o' | sort)
mapfile -t osi_objs      < <(find "$OBJ" -name 'bt_osi_*.o' | sort)

echo "[opt] libFraunhoferAAC_o2.a"
"$AR" rcs "$OUTDIR/libFraunhoferAAC_o2.a" "${aac_objs[@]}"

mkdir -p "$OUTDIR/stubs"
for stub in libcutils libutils libhardware libmedia; do
  "$CCXX" "${TARGET[@]}" "${SYSROOT[@]}" -shared -x c++ /dev/null \
    -Wl,-soname,"$stub.so" -o "$OUTDIR/stubs/$stub.so" || exit 1
done

echo "[opt] bluetooth_o2.so"
"$CCXX" "${TARGET[@]}" "${SYSROOT[@]}" -shared -static-libstdc++ \
  -o "$OUTDIR/bluetooth_o2.so" "${bt_objs[@]}" "${aac_objs[@]}" \
  "${ldacdec_objs[@]}" \
  "${tinyxml2_objs[@]}" \
  -Wl,-Bsymbolic -llog -lz -ldl \
  -Wl,--no-as-needed -L"$OUTDIR/stubs" -lcutils -lutils -lhardware -lmedia || exit 1

echo "[opt] audio.a2dp_o2.so"
"$CCXX" "${TARGET[@]}" "${SYSROOT[@]}" -shared -static-libstdc++ \
  -o "$OUTDIR/audio.a2dp_o2.so" "${a2dp_objs[@]}" "${osi_objs[@]}" \
  -Wl,-Bsymbolic -llog || exit 1

ls -la "$OUTDIR"
echo "[opt] done"
