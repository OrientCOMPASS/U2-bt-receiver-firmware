#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for scripts/make_logo.py (container parse/build, RGB565, replace)."""

from __future__ import annotations

import os
import struct
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import make_logo as ml  # noqa: E402


def build_container(block_sizes):
    """Build a synthetic MTK logo container: [uint32 offsets][blocks...]."""
    n = len(block_sizes)
    table_len = 4 * n
    offs = []
    cur = table_len
    for sz in block_sizes:
        offs.append(cur)
        cur += sz
    out = bytearray()
    for o in offs:
        out += struct.pack("<I", o)
    for i, sz in enumerate(block_sizes):
        out += bytes([(i + 1) & 0xFF]) * sz
    return bytes(out)


class TestRgb565(unittest.TestCase):
    def test_pack_black(self):
        self.assertEqual(ml.rgb888_to_rgb565(0, 0, 0), 0x0000)

    def test_pack_white(self):
        # 0xFF,0xFF,0xFF -> 5 bits of 1s (0x1F)<<11 | 6 bits (0x3F)<<5 | 5 bits (0x1F)
        self.assertEqual(ml.rgb888_to_rgb565(255, 255, 255), 0xFFFF)

    def test_pack_red(self):
        self.assertEqual(ml.rgb888_to_rgb565(255, 0, 0), 0xF800)

    def test_pack_green(self):
        self.assertEqual(ml.rgb888_to_rgb565(0, 255, 0), 0x07E0)

    def test_pack_blue(self):
        self.assertEqual(ml.rgb888_to_rgb565(0, 0, 255), 0x001F)

    def test_solid_size(self):
        blob = ml.solid_rgb565(4, 3, 255, 0, 0)
        self.assertEqual(len(blob), 4 * 3 * 2)
        self.assertEqual(blob, struct.pack("<H", 0xF800) * 12)

    def test_pixels_roundtrip(self):
        px = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)]
        blob = ml.pixels_to_rgb565(px, 2, 2)
        vals = struct.unpack("<4H", blob)
        self.assertEqual(vals, (0xF800, 0x07E0, 0x001F, 0xFFFF))


class TestContainer(unittest.TestCase):
    def test_detect_and_parse(self):
        data = build_container([100, 200, 300])
        info = ml.parse_logo(data)
        self.assertEqual(info["table"], [12, 112, 312])
        self.assertEqual(len(info["blocks"]), 3)
        self.assertEqual([b["size"] for b in info["blocks"]], [100, 200, 300])

    def test_parse_single_block(self):
        data = build_container([64])
        info = ml.parse_logo(data)
        self.assertEqual(info["table"], [4])
        self.assertEqual(info["blocks"][0]["size"], 64)

    def test_build_roundtrip(self):
        data = build_container([100, 200, 300])
        info = ml.parse_logo(data)
        starts = info["table"]
        ends = starts[1:] + [len(data)]
        blocks = [data[s:e] for s, e in zip(starts, ends) if e > s]
        rebuilt = ml.build_logo(info["table"], blocks)
        # Same total size and same block contents
        self.assertEqual(len(rebuilt), len(data))
        info2 = ml.parse_logo(rebuilt)
        self.assertEqual([b["size"] for b in info2["blocks"]], [100, 200, 300])

    def test_replace_block_preserves_others(self):
        data = build_container([100, 200, 300])
        new = b"\xAB" * 200  # same size as block 1
        out = ml.replace_block(data, 1, new, compressed=False)
        info = ml.parse_logo(out)
        starts = info["table"]
        ends = starts[1:] + [len(out)]
        blocks = [out[s:e] for s, e in zip(starts, ends) if e > s]
        self.assertEqual(len(blocks[1]), 200)
        self.assertEqual(blocks[1], new)
        # other blocks keep their original fill byte
        self.assertEqual(blocks[0], b"\x01" * 100)
        self.assertEqual(blocks[2], b"\x03" * 300)

    def test_replace_with_compression(self):
        import zlib

        data = build_container([50, 4096])
        new = bytes(range(256)) * 16  # 4096 bytes, compressible
        out = ml.replace_block(data, 1, new, compressed=True)
        info = ml.parse_logo(out)
        starts = info["table"]
        ends = starts[1:] + [len(out)]
        blk1 = out[starts[1]:ends[1]]
        self.assertEqual(blk1[0], 0x78)  # zlib magic
        self.assertEqual(zlib.decompress(blk1), new)

    def test_detect_rejects_garbage(self):
        with self.assertRaises(ValueError):
            ml.detect_offset_table(b"\x00\x00\x00\x00" * 8)


if __name__ == "__main__":
    unittest.main(verbosity=2)
