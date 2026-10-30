#!/usr/bin/env python3
"""Patch versionCode in binary AndroidManifest.xml (AXML) 24 -> 25."""
import struct, sys, zipfile, shutil

SRC = "ws/dbgbuild/Bluetooth_sink_v2.apk"
DST = "ws/dbgbuild/unsigned3.apk"

def parse_strings(b, off):
    # string pool chunk at off
    typ, hs, size = struct.unpack_from("<HHI", b, off)
    assert typ == 0x0001, hex(typ)
    strCount, styleCount, flags, stringsStart, stylesStart = struct.unpack_from("<IIIII", b, off+8)
    offs = [struct.unpack_from("<I", b, off+28+i*4)[0] for i in range(strCount)]
    utf8 = bool(flags & 0x100)
    strs = []
    base = off + stringsStart
    for so in offs:
        p = base + so
        if utf8:
            # skip u16-len (chars), then u16-len (bytes) varint-ish
            n = b[p]; p += 1
            if n & 0x80: p += 1
            ln = b[p]; p += 1
            if ln & 0x80:
                ln = ((ln & 0x7f) << 8) | b[p]; p += 1
            strs.append(b[p:p+ln].decode("utf-8", "replace"))
        else:
            ln = struct.unpack_from("<H", b, p)[0]; p += 2
            if ln & 0x8000:
                ln = ((ln & 0x7fff) << 16) | struct.unpack_from("<H", b, p)[0]; p += 2
            strs.append(b[p:p+ln*2].decode("utf-16-le", "replace"))
    return strs

data = zipfile.ZipFile(SRC).read("AndroidManifest.xml")
strs = parse_strings(data, 8)  # string pool right after 8-byte xml header
# find resource map chunk
off = 8
sp_typ, sp_hs, sp_size = struct.unpack_from("<HHI", data, off)
off += sp_size
typ, hs, size = struct.unpack_from("<HHI", data, off)
assert typ == 0x0180, "expected resmap, got %s" % hex(typ)
nres = (size - hs) // 4
resmap = [struct.unpack_from("<I", data, off+hs+i*4)[0] for i in range(nres)]
VC = 0x0101021b
assert VC in resmap, "versionCode not in resmap"
vc_idx = resmap.index(VC)
print("versionCode string idx:", vc_idx, "->", strs[vc_idx])

# walk chunks for START_ELEMENT (0x0102)
off += size
patched = 0
while off < len(data):
    typ, hs, size = struct.unpack_from("<HHI", data, off)
    if typ == 0x0102:
        # lineNumber(4) comment(4) then ns(4) name(4) attrStart(2) attrSize(2) attrCount(2) ...
        attrStart, attrSize, attrCount = struct.unpack_from("<HHH", data, off+hs+8+8+2)
        abase = off + hs + 8 + 8 + attrStart - 2  # recompute properly below
        # standard: ext begins at off+hs+8 (after lineno/comment? no: header=16 incl type/hs/size + lineno+comment =8 => ext at off+16)
        ext = off + 16
        astart = struct.unpack_from("<H", data, ext+8)[0]
        acount = struct.unpack_from("<H", data, ext+12)[0]
        for i in range(acount):
            a = ext + astart + i*20
            name_idx = struct.unpack_from("<I", data, a+4)[0]
            if name_idx == vc_idx:
                dt = data[a+15]
                val = struct.unpack_from("<I", data, a+16)[0]
                print("found versionCode attr at %d: dataType=0x%02x value=%d" % (a, dt, val))
                assert val == 24, "unexpected versionCode %d" % val
                data = data[:a+16] + struct.pack("<I", 25) + data[a+20:]
                patched += 1
    if size == 0: break
    off += size
assert patched == 1, "patched %d" % patched
print("patched OK: versionCode -> 25")

# rebuild zip preserving compression per entry
zin = zipfile.ZipFile(SRC)
zout = zipfile.ZipFile(DST, "w")
for it in zin.infolist():
    b = data if it.filename == "AndroidManifest.xml" else zin.read(it.filename)
    zi = zipfile.ZipInfo(it.filename, date_time=it.date_time)
    zi.compress_type = it.compress_type
    zi.external_attr = it.external_attr
    zout.writestr(zi, b)
zout.close()
print("wrote", DST)
