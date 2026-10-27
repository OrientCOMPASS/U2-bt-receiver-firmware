#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for scripts/mtk_sparse2raw.py.

These tests are fully self-contained (no baseline firmware, no network): they
synthesise MTK block_size=4096 sparse images with every chunk type, decode them
with the production converter, and assert byte-exact round-trips plus ext4
superblock validation and error handling.

Run with:  python3 -m unittest discover -s scripts/tests -v
       or:  python3 scripts/tests/test_mtk_sparse2raw.py
"""

from __future__ import annotations

import io
import os
import struct
import sys
import unittest

# Make ``scripts/`` importable when run from the repo root or from tests/.
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import mtk_sparse2raw as m  # noqa: E402

BLOCK = 4096  # the MTK firmware's block_size (the whole reason simg2img fails)

# chunk type constants
RAW = m.CHUNK_TYPE_RAW
FILL = m.CHUNK_TYPE_FILL
DONT = m.CHUNK_TYPE_DONT_CARE
CRC = m.CHUNK_TYPE_CRC32


# --------------------------------------------------------------------------- #
# Sparse ENCODER (test fixture builder) -- mirrors the on-disk layout the
# decoder expects.  Not shipped in the firmware; used only to make test images.
# --------------------------------------------------------------------------- #
def _file_header(block_size, total_chks, total_blks=0):
    h = bytearray(28)
    struct.pack_into("<I", h, 0, m.SPARSE_HEADER_MAGIC)
    struct.pack_into("<H", h, 4, 1)  # major
    struct.pack_into("<H", h, 6, 0)  # minor
    struct.pack_into("<H", h, 8, 28)  # file_hdr_sz
    struct.pack_into("<H", h, 10, 12)  # chunk_hdr_sz
    struct.pack_into("<I", h, 12, block_size)
    struct.pack_into("<I", h, 16, total_blks)
    struct.pack_into("<I", h, 20, total_chks)
    struct.pack_into("<I", h, 24, 0)  # image_crc
    return bytes(h)


def _chunk_header(chunk_type, chunk_sz, total_sz):
    h = bytearray(12)
    struct.pack_into("<H", h, 0, chunk_type)
    struct.pack_into("<H", h, 2, 0)  # reserved
    struct.pack_into("<I", h, 4, chunk_sz)
    struct.pack_into("<I", h, 8, total_sz)
    return bytes(h)


def build_sparse(chunks, block_size=BLOCK):
    """
    ``chunks`` is a list of (type, payload) where:
      RAW / FILL(literal) : payload is the literal output bytes (len == chunk_sz*block)
      FILL(classic)       : payload is a 4-byte fill word
      DONT_CARE           : payload is the number of blocks (int)
      CRC32               : payload is 4 bytes (ignored on output)
    Returns the full sparse image bytes.
    """
    body = bytearray()
    for ctype, payload in chunks:
        if ctype == DONT:
            nblk = int(payload)
            body += _chunk_header(DONT, nblk, 12)
        elif ctype == CRC:
            body += _chunk_header(CRC, 0, 12 + len(payload))
            body += payload
        elif ctype == FILL and len(payload) == 4:
            # classic 4-byte fill covering N blocks; caller encodes N in a tuple
            raise ValueError("use (FILL, (word, nblk)) for classic fill")
        else:
            data = bytes(payload)
            assert len(data) % block_size == 0, "literal body must be block-aligned"
            body += _chunk_header(ctype, len(data) // block_size, 12 + len(data))
            body += data
    return _file_header(block_size, len(chunks)) + bytes(body)


def build_sparse_mixed(specs, block_size=BLOCK):
    """
    More explicit builder supporting classic fill.  Each spec is a dict:
      {'type': RAW|FILL|DONT|CRC, ...}
    RAW/FILL literal: {'type':RAW,'data':bytes}
    classic fill:     {'type':FILL,'word':b'\\xab\\xcd\\xef\\x01','nblk':N}
    dont care:        {'type':DONT,'nblk':N}
    crc:              {'type':CRC,'crc':b'\\x00\\x00\\x00\\x00'}
    """
    body = bytearray()
    for s in specs:
        t = s["type"]
        if t == DONT:
            body += _chunk_header(DONT, s["nblk"], 12)
        elif t == CRC:
            crc = s.get("crc", b"\x00\x00\x00\x00")
            body += _chunk_header(CRC, 0, 12 + len(crc))
            body += crc
        elif t == FILL and "word" in s:
            word = s["word"]
            nblk = s["nblk"]
            # classic encoding: 4-byte body, output nblk blocks
            body += _chunk_header(FILL, nblk, 12 + 4)
            body += word
        else:
            data = bytes(s["data"])
            assert len(data) % block_size == 0
            body += _chunk_header(t, len(data) // block_size, 12 + len(data))
            body += data
    return _file_header(block_size, len(specs)) + bytes(body)


def make_ext4_superblock(nblocks, block_size=BLOCK, inodes=64):
    """
    Return a bytes blob (block_size long) whose first 1024..1024+64 bytes hold a
    valid ext4 superblock describing ``nblocks`` of ``block_size``.
    """
    blk = bytearray(block_size)
    sb_off = 1024
    log_block = {1024: 0, 2048: 1, 4096: 2}[block_size]
    struct.pack_into("<I", blk, sb_off + 0, inodes)  # s_inodes_count
    struct.pack_into("<I", blk, sb_off + 4, nblocks)  # s_blocks_count_lo
    struct.pack_into("<I", blk, sb_off + 24, log_block)  # s_log_block_size
    struct.pack_into("<H", blk, sb_off + 56, m.EXT4_SUPER_MAGIC)  # s_magic
    struct.pack_into("<H", blk, sb_off + 58, 1)  # s_state = cleanly unmounted
    return bytes(blk)


def decode(sparse_bytes, expected_size=None):
    in_f = io.BytesIO(sparse_bytes)
    out_f = io.BytesIO()
    stats = m.sparse_to_raw(in_f, out_f, expected_size=expected_size)
    return out_f.getvalue(), stats


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
class TestHeaderParsing(unittest.TestCase):
    def test_valid_header_fields(self):
        img = build_sparse([(DONT, 1)])
        h = m.parse_sparse_header(img[:28])
        self.assertEqual(h.magic, m.SPARSE_HEADER_MAGIC)
        self.assertEqual(h.block_size, BLOCK)
        self.assertEqual(h.file_hdr_sz, 28)
        self.assertEqual(h.chunk_hdr_sz, 12)
        self.assertEqual(h.total_chks, 1)

    def test_bad_magic_raises(self):
        img = bytearray(build_sparse([(DONT, 1)]))
        struct.pack_into("<I", img, 0, 0xDEADBEEF)
        with self.assertRaises(m.SparseFormatError):
            m.parse_sparse_header(bytes(img))

    def test_truncated_header_raises(self):
        with self.assertRaises(m.SparseFormatError):
            m.parse_sparse_header(b"\x3a\xff&\xed" + b"\x00" * 10)

    def test_block_size_512_also_parses(self):
        # The tool is not hard-coded to 4096; it must honour whatever the header says.
        img = build_sparse_mixed([{"type": DONT, "nblk": 2}], block_size=512)
        raw, stats = decode(img)
        self.assertEqual(stats["block_size"], 512)
        self.assertEqual(len(raw), 2 * 512)


class TestChunkRoundTrip(unittest.TestCase):
    def test_raw_roundtrip(self):
        data = os.urandom(BLOCK * 3)
        img = build_sparse([(RAW, data)])
        raw, stats = decode(img)
        self.assertEqual(raw, data)
        self.assertEqual(stats["chunks"]["RAW"], 1)
        self.assertEqual(stats["out_size"], len(data))

    def test_dont_care_emits_zeros(self):
        img = build_sparse([(DONT, 4)])
        raw, stats = decode(img)
        self.assertEqual(raw, b"\x00" * (BLOCK * 4))
        self.assertEqual(stats["chunks"]["DONT_CARE"], 1)

    def test_literal_fill_roundtrip(self):
        # This firmware stores literal bytes even under the FILL type.
        data = (b"\xaa" * BLOCK) + (b"\xbb" * BLOCK)
        img = build_sparse([(FILL, data)])
        raw, stats = decode(img)
        self.assertEqual(raw, data)
        self.assertEqual(stats["chunks"]["FILL"], 1)

    def test_classic_4byte_fill_expands(self):
        word = b"\x11\x22\x33\x44"
        img = build_sparse_mixed([{"type": FILL, "word": word, "nblk": 3}])
        raw, stats = decode(img)
        self.assertEqual(len(raw), BLOCK * 3)
        self.assertEqual(raw, (word * (BLOCK * 3 // 4)))

    def test_crc32_contributes_nothing(self):
        data = os.urandom(BLOCK)
        img = build_sparse_mixed(
            [{"type": RAW, "data": data}, {"type": CRC, "crc": b"\xde\xad\xbe\xef"}]
        )
        raw, stats = decode(img)
        self.assertEqual(raw, data)
        self.assertEqual(stats["chunks"]["CRC32"], 1)

    def test_mixed_all_chunk_types(self):
        raw0 = os.urandom(BLOCK * 2)  # RAW
        raw1 = b"\x5a" * BLOCK  # literal FILL
        # Sequence: RAW(2) + DONT(3) + FILL literal(1) + classic FILL(2) + RAW(1) + CRC
        img = build_sparse_mixed(
            [
                {"type": RAW, "data": raw0},
                {"type": DONT, "nblk": 3},
                {"type": FILL, "data": raw1},
                {"type": FILL, "word": b"\x01\x02\x03\x04", "nblk": 2},
                {"type": RAW, "data": os.urandom(BLOCK)},
                {"type": CRC, "crc": b"\x00\x00\x00\x00"},
            ]
        )
        raw, stats = decode(img)
        expected = (
            raw0
            + b"\x00" * (BLOCK * 3)
            + raw1
            + (b"\x01\x02\x03\x04" * (BLOCK * 2 // 4))
            + raw[0:0]  # placeholder
        )
        # rebuild expected precisely (the 5th chunk's random data):
        # recompute by re-reading the source we embedded
        self.assertEqual(len(raw), BLOCK * (2 + 3 + 1 + 2 + 1))
        self.assertEqual(raw[: BLOCK * 2], raw0)
        self.assertEqual(raw[BLOCK * 2 : BLOCK * 5], b"\x00" * (BLOCK * 3))
        self.assertEqual(raw[BLOCK * 5 : BLOCK * 6], raw1)
        self.assertEqual(
            raw[BLOCK * 6 : BLOCK * 8], b"\x01\x02\x03\x04" * (BLOCK * 2 // 4)
        )
        self.assertEqual(stats["total_chks"], 6)

    def test_total_chks_is_loop_bound_not_total_blks(self):
        # total_blks is deliberately set to a bogus value; the decoder must ignore it.
        data = os.urandom(BLOCK * 2)
        img = bytearray(build_sparse([(RAW, data)]))
        struct.pack_into("<I", img, 16, 0xFFFFFFFF)  # corrupt total_blks
        raw, stats = decode(bytes(img))
        self.assertEqual(raw, data)


class TestExt4Validation(unittest.TestCase):
    def _make_ext4_image(self, nblocks=8):
        sb_block = make_ext4_superblock(nblocks, BLOCK)
        # block 0 holds the superblock; remaining blocks are filler data
        raw = sb_block + os.urandom(BLOCK * (nblocks - 1))
        return raw

    def test_check_ext4_ok(self):
        nblocks = 8
        raw = self._make_ext4_image(nblocks)
        info = m.check_ext4(raw)
        self.assertEqual(info["s_magic"], m.EXT4_SUPER_MAGIC)
        self.assertEqual(info["fs_block_size"], BLOCK)
        self.assertEqual(info["s_blocks_count_lo"], nblocks)
        self.assertEqual(info["implied_size"], nblocks * BLOCK)

    def test_check_ext4_size_match(self):
        nblocks = 8
        raw = self._make_ext4_image(nblocks)
        img = build_sparse([(RAW, raw)])
        out, _ = decode(img)
        # write to a temp file to exercise the path-based checker
        import tempfile

        with tempfile.NamedTemporaryFile(delete=False) as tf:
            tf.write(out)
            path = tf.name
        try:
            info = m.check_ext4_size(path, len(out))
            self.assertEqual(info["implied_size"], len(out))
        finally:
            os.unlink(path)

    def test_check_ext4_bad_magic(self):
        raw = bytearray(self._make_ext4_image(4))
        struct.pack_into("<H", raw, 1024 + 56, 0x1234)  # corrupt magic
        with self.assertRaises(ValueError):
            m.check_ext4(bytes(raw))

    def test_check_ext4_size_mismatch(self):
        raw = self._make_ext4_image(4)
        with self.assertRaises(ValueError):
            # claim the image is a different size than the superblock implies
            m.check_ext4_size(io.BytesIO(raw), 999999)

    def test_expected_size_enforced(self):
        data = os.urandom(BLOCK * 3)
        img = build_sparse([(RAW, data)])
        with self.assertRaises(ValueError):
            decode(img, expected_size=BLOCK * 4)
        raw, _ = decode(img, expected_size=BLOCK * 3)
        self.assertEqual(raw, data)


class TestErrorHandling(unittest.TestCase):
    def test_truncated_chunk_header(self):
        img = build_sparse([(RAW, os.urandom(BLOCK))])
        with self.assertRaises(m.SparseFormatError):
            decode(img[: 28 + 6])  # cut mid chunk header

    def test_truncated_chunk_body(self):
        img = build_sparse([(RAW, os.urandom(BLOCK))])
        with self.assertRaises(m.SparseFormatError):
            decode(img[: 28 + 12 + 100])  # header ok, body short

    def test_unknown_chunk_type(self):
        img = bytearray(build_sparse([(DONT, 1)]))
        struct.pack_into("<H", img, 28, 0xBEEF)  # bad chunk type
        with self.assertRaises(m.SparseFormatError):
            decode(bytes(img))

    def test_total_sz_smaller_than_header(self):
        img = bytearray(build_sparse([(DONT, 1)]))
        struct.pack_into("<I", img, 28 + 8, 4)  # total_sz < 12
        with self.assertRaises(m.SparseFormatError):
            decode(bytes(img))


class TestRealisticSystemImage(unittest.TestCase):
    """
    Mimic the real system.img shape from the plan: many FILL(literal) chunks
    interleaved with DONT_CARE holes, ending at an exact partition size with a
    valid ext4 superblock -- the same invariants the baseline conversion passed.
    """

    def test_large_mixed_matches_partition_size_and_ext4(self):
        nblocks = 64
        # block 0 = superblock; sprinkle data and holes
        specs = []
        sb = make_ext4_superblock(nblocks, BLOCK)
        specs.append({"type": RAW, "data": sb})  # block 0
        # 10 literal blocks of data
        specs.append({"type": FILL, "data": os.urandom(BLOCK * 10)})
        # a 20-block hole
        specs.append({"type": DONT, "nblk": 20})
        # 33 more blocks of literal data to reach 1+10+20+33 = 64
        specs.append({"type": FILL, "data": os.urandom(BLOCK * 33)})
        img = build_sparse_mixed(specs)
        raw, stats = decode(img, expected_size=nblocks * BLOCK)
        self.assertEqual(len(raw), nblocks * BLOCK)
        info = m.check_ext4(raw)
        self.assertEqual(info["implied_size"], nblocks * BLOCK)
        self.assertEqual(stats["total_chks"], 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
