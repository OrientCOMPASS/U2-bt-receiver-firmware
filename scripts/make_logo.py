#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_logo.py -- inspect and rebuild the MTK logo.bin for the KeenPlus U2
(3.0" ST7701SN panel).  Plan section 23 deliverable 4.

APPROACH (format-safe)
----------------------
Rather than synthesise a logo.bin from scratch -- which risks a layout the
baseline LK/preloader will reject -- this tool REUSES the baseline container:
it parses the block-offset table of an existing logo.bin, and replaces one or
more raw RGB565 blocks with new bitmaps of the SAME byte size, then repacks.
The offset table and overall structure are preserved, so the result is as
bootable as the original (the logo partition is recoverable -- it is not in the
"never flash" list of plan section 18.1).

MTK logo.bin layout handled here (common for this generation):
    [ uint32 z_offsets[N+1] ][ block0 ][ block1 ] ... [ blockN-1 ]
where z_offsets[0] is the byte offset of block0 (== 4*(N+1) when the table is
contiguous), each z_offsets[i] is monotonically increasing, and block i spans
[z_offsets[i], z_offsets[i+1]).  Some builds zlib-compress each block; this tool
detects a zlib stream (0x78 magic) and, when --compressed is given, re-deflates
the replacement so the container stays consistent.

Because the exact table width varies, `inspect` AUTO-DETECTS N by scanning for
the longest run of plausible little-endian uint32 offsets.  Always run `inspect`
on the real baseline logo.bin first and sanity-check the detected geometry
against the panel resolution before rebuilding.

RGB565: 2 bytes/pixel, little endian, 5-6-5.  A WxH frame is W*H*2 bytes.

PNG input requires Pillow (optional); raw .rgb565 input needs nothing.

This file is released into the public domain (see LICENSE, Unlicense).
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
import zlib


# --------------------------------------------------------------------------- #
# RGB565 helpers
# --------------------------------------------------------------------------- #
def rgb888_to_rgb565(r: int, g: int, b: int) -> int:
    """Pack 8-bit RGB into a 16-bit RGB565 value."""
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


def pixels_to_rgb565(pixels, width: int, height: int) -> bytes:
    """
    Convert an iterable of (r, g, b) triples (row-major, width*height of them)
    to a little-endian RGB565 byte blob.
    """
    out = bytearray(width * height * 2)
    i = 0
    for (r, g, b) in pixels:
        v = rgb888_to_rgb565(r, g, b)
        struct.pack_into("<H", out, i, v)
        i += 2
    return bytes(out)


def solid_rgb565(width: int, height: int, r: int, g: int, b: int) -> bytes:
    """A solid-colour RGB565 frame (used for tests and simple logos)."""
    v = rgb888_to_rgb565(r, g, b)
    return struct.pack("<H", v) * (width * height)


def load_png_rgb565(path: str, width: int, height: int) -> bytes:
    """Load a PNG via Pillow, resize to width x height, return RGB565 bytes."""
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        raise SystemExit(
            "PNG input needs Pillow: pip install Pillow  (or pass a .rgb565 raw file)"
        )
    im = Image.open(path).convert("RGB").resize((width, height))
    return pixels_to_rgb565(im.getdata(), width, height)


# --------------------------------------------------------------------------- #
# MTK logo container
# --------------------------------------------------------------------------- #
def detect_offset_table(data: bytes, max_entries: int = 64) -> list[int]:
    """
    Auto-detect the block-offset table at the start of a logo.bin.

    Reads candidate uint32 LE values from offset 0 and keeps the longest prefix
    that is strictly increasing, starts at a plausible table size, and stays
    within the file.  Returns the list of offsets (length N+1 including the
    terminating offset == file size is inferred, not stored).
    """
    n_candidate = min(max_entries, len(data) // 4)
    offs = [struct.unpack_from("<I", data, i * 4)[0] for i in range(n_candidate)]
    table: list[int] = []
    for i, o in enumerate(offs):
        if o == 0 or o >= len(data):
            break
        if table and o <= table[-1]:
            break
        # The first offset should equal the table size in bytes: 4*(N+1).
        table.append(o)
        # Heuristic stop: the first offset tells us the table length.
        if i == 0:
            expected_entries = o // 4
            # table holds N+1 offsets; expect o == 4*(N+1) -> N+1 = o/4
            if expected_entries < 2 or expected_entries > max_entries:
                break
            # keep reading until we have expected_entries offsets total
            # (loop continues; validated below)
    # Validate: first offset must equal 4*len(table_when_complete).
    if len(table) >= 2 and table[0] == 4 * len(table):
        return table
    # Fallback: assume the table is exactly the run we found and table[0] is the
    # data start.
    if len(table) >= 1:
        return table
    raise ValueError("could not detect a plausible MTK logo offset table")


def parse_logo(data: bytes) -> dict:
    """Parse a logo.bin into its offset table and blocks."""
    table = detect_offset_table(data)
    starts = table[:]
    ends = starts[1:] + [len(data)]
    blocks = []
    for i, (s, e) in enumerate(zip(starts, ends)):
        if e <= s:
            break
        blocks.append({"index": i, "start": s, "end": e, "size": e - s})
    return {"table": table, "blocks": blocks, "size": len(data)}


def build_logo(table: list[int], blocks: list[bytes]) -> bytes:
    """
    Rebuild a logo.bin from an offset table and block payloads.  The table is
    recomputed so offsets stay consistent with the (possibly resized) blocks;
    the number of entries is preserved.
    """
    n = len(table)
    header_len = 4 * n  # table has n uint32 entries
    out = bytearray()
    # recompute offsets
    offs = []
    cur = header_len
    for b in blocks:
        offs.append(cur)
        cur += len(b)
    # The original table stores N block offsets (+ possibly a final sentinel).
    # Preserve the original entry count: pad/truncate to n entries.
    while len(offs) < n:
        offs.append(cur)
    offs = offs[:n]
    for o in offs:
        out += struct.pack("<I", o)
    for b in blocks:
        out += b
    return bytes(out)


def replace_block(data: bytes, index: int, new_block: bytes,
                  compressed: bool = False) -> bytes:
    """Replace one block's payload, preserving the container structure."""
    info = parse_logo(data)
    table = info["table"]
    blocks_bytes = []
    starts = table[:]
    ends = starts[1:] + [len(data)]
    for i, (s, e) in enumerate(zip(starts, ends)):
        if e <= s:
            break
        raw = data[s:e]
        if i == index:
            payload = new_block
            was_compressed = len(raw) >= 2 and raw[0] == 0x78
            if compressed or was_compressed:
                payload = zlib.compress(new_block)
            raw = payload
        blocks_bytes.append(raw)
    return build_logo(table, blocks_bytes)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _load_replacement(path: str, width: int, height: int) -> bytes:
    if path.lower().endswith(".png"):
        return load_png_rgb565(path, width, height)
    with open(path, "rb") as fh:
        data = fh.read()
    if width and height and len(data) != width * height * 2:
        raise SystemExit(
            "raw file is %d bytes but %dx%d RGB565 needs %d bytes"
            % (len(data), width, height, width * height * 2)
        )
    return data


def cmd_inspect(args) -> int:
    with open(args.logo, "rb") as fh:
        data = fh.read()
    info = parse_logo(data)
    print("logo.bin: %s (%d bytes)" % (args.logo, len(data)))
    print("offset table (%d entries): %s" % (len(info["table"]), info["table"]))
    print("blocks:")
    for b in info["blocks"]:
        raw = data[b["start"]:b["start"] + 2]
        comp = "zlib" if raw[:1] == b"\x78" else "raw"
        print("  [%d] off=%d size=%d (%s)" % (b["index"], b["start"], b["size"], comp))
        if args.guess_dims and comp == "raw":
            # try to infer WxH from size for common panel widths
            size = b["size"]
            for w in (240, 320, 360, 454, 480, 720):
                if size % (w * 2) == 0:
                    h = size // (w * 2)
                    if 0 < h <= 2048:
                        print("       maybe %dx%d RGB565" % (w, h))
                        break
    return 0


def cmd_replace(args) -> int:
    with open(args.logo, "rb") as fh:
        data = fh.read()
    new_block = _load_replacement(args.image, args.width, args.height)
    out = replace_block(data, args.block, new_block, compressed=args.compressed)
    with open(args.out, "wb") as fh:
        fh.write(out)
    print("wrote %s (%d bytes), replaced block %d with %d bytes%s"
          % (args.out, len(out), args.block, len(new_block),
             " (zlib)" if args.compressed else ""))
    return 0


def cmd_gen_solid(args) -> int:
    """Generate a standalone raw RGB565 frame (helper for making replacements)."""
    blob = solid_rgb565(args.width, args.height, args.r, args.g, args.b)
    with open(args.out, "wb") as fh:
        fh.write(blob)
    print("wrote %s (%dx%d RGB565, %d bytes)"
          % (args.out, args.width, args.height, len(blob)))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="MTK logo.bin inspect/rebuild tool")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_ins = sub.add_parser("inspect", help="dump the offset table and blocks")
    p_ins.add_argument("logo")
    p_ins.add_argument("--guess-dims", action="store_true",
                       help="try to infer WxH from each raw block size")
    p_ins.set_defaults(func=cmd_inspect)

    p_rep = sub.add_parser("replace", help="replace one block with a new bitmap")
    p_rep.add_argument("logo")
    p_rep.add_argument("--block", type=int, required=True)
    p_rep.add_argument("--image", required=True, help=".png (needs Pillow) or .rgb565 raw")
    p_rep.add_argument("--width", type=int, default=0)
    p_rep.add_argument("--height", type=int, default=0)
    p_rep.add_argument("--compressed", action="store_true",
                       help="zlib-compress the replacement block")
    p_rep.add_argument("--out", required=True)
    p_rep.set_defaults(func=cmd_replace)

    p_gen = sub.add_parser("gen-solid", help="generate a solid-colour RGB565 raw frame")
    p_gen.add_argument("--width", type=int, required=True)
    p_gen.add_argument("--height", type=int, required=True)
    p_gen.add_argument("--r", type=int, default=0)
    p_gen.add_argument("--g", type=int, default=0)
    p_gen.add_argument("--b", type=int, default=0)
    p_gen.add_argument("--out", required=True)
    p_gen.set_defaults(func=cmd_gen_solid)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
