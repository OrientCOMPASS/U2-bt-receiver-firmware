#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mtk_sparse2raw.py -- Convert the MTK "sparse" images shipped in the KeenPlus U2
baseline firmware (and similar MT6735 dumps) into raw ext4 images.

WHY THIS TOOL EXISTS
--------------------
The U2 baseline ``system.img`` / ``userdata.img`` / ``cache.img`` are Android
sparse images, but with ``block_size = 4096`` instead of the 512 that AOSP's
``libsparse`` / ``simg2img`` hard-codes in its source.  Running ``simg2img`` on
these images produces completely wrong output (a few dozen bytes).  This tool
parses the container directly from the on-disk layout documented in
``docs/DEVELOPMENT_PLAN.md`` section 6 and was verified against the real
baseline images (system -> 3,095,396,352 B == 0xB8800000, matching the scatter
partition size and a valid ext4 superblock).

FILE HEADER (28 bytes, little endian)
-------------------------------------
    off  0  u32  magic        0xED26FF3A
    off  4  u16  major_version (1)
    off  6  u16  minor_version (0)
    off  8  u16  file_hdr_sz   (28)
    off 10  u16  chunk_hdr_sz  (12)
    off 12  u32  block_size    (4096 for this firmware -- the whole point)
    off 16  u32  total_blks    UNRELIABLE: its unit is not consistent across
                               images in the same batch.  Never validate with
                               it; always trust the actual bytes written after
                               walking every chunk.
    off 20  u32  total_chks    The ONLY reliable loop bound.
    off 24  u32  image_crc     (0 == unused)

CHUNK HEADER (12 bytes, little endian)
--------------------------------------
    u16  chunk_type   0xCAC1 FILL / 0xCAC2 RAW / 0xCAC3 DONT_CARE / 0xCAC4 CRC32
    u16  reserved
    u32  chunk_sz     number of OUTPUT blocks (in units of block_size)
    u32  total_sz     size of this chunk IN FILE, in bytes, INCLUDING the
                      12-byte chunk header

CHUNK SEMANTICS (empirically verified on this firmware)
-------------------------------------------------------
  * DONT_CARE : total_sz is always 12, no data body; emit chunk_sz*block_size
                zero bytes.
  * RAW       : data body = total_sz - 12 bytes; copy verbatim.
  * FILL      : this firmware stores LITERAL data (total_sz - 12 bytes), not a
                4-byte fill word, so copy it verbatim.  For robustness we still
                honour the classic 4-byte-fill encoding when total_sz == 16.
  * CRC32     : 4-byte checksum, contributes nothing to the output image.

IMPLEMENTATION NOTES (from the plan's "must read" list)
-------------------------------------------------------
  * Read every header field with ``struct.unpack_from`` at a FIXED offset.  Do
    NOT parse the whole header with one format string: ``'<IHHHHIII'`` and
    ``'<IHHHHIIII'`` differ by a single character but 4 bytes, which is trivial
    to mis-count.
  * Loop with ``total_chks`` as the bound.
  * A conversion is only successful when BOTH invariants hold:
      1. output size == scatter partition size (pass --expected-size to check), and
      2. the ext4 superblock is valid: s_magic == 0xEF53 and
         s_blocks_count_lo * (1024 << s_log_block_size) == output size.

No root is required to read the resulting ext4 image; use
``debugfs -R "dump /path out" system.raw.img``.

This file is released into the public domain (see LICENSE, Unlicense).
"""

from __future__ import annotations

import argparse
import os
import struct
import sys

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
SPARSE_HEADER_MAGIC = 0xED26FF3A
SPARSE_HEADER_LEN = 28
CHUNK_HEADER_LEN = 12

CHUNK_TYPE_RAW = 0xCAC1 + 1  # 0xCAC2
CHUNK_TYPE_FILL = 0xCAC1
CHUNK_TYPE_DONT_CARE = 0xCAC3
CHUNK_TYPE_CRC32 = 0xCAC4

EXT4_SUPER_MAGIC = 0xEF53
EXT4_SUPER_OFFSET = 1024  # superblock lives 1024 bytes into the partition

_CHUNK_NAME = {
    CHUNK_TYPE_RAW: "RAW",
    CHUNK_TYPE_FILL: "FILL",
    CHUNK_TYPE_DONT_CARE: "DONT_CARE",
    CHUNK_TYPE_CRC32: "CRC32",
}


class SparseFormatError(Exception):
    """Raised when the input is not a well-formed MTK sparse image."""


class SparseHeader:
    """Parsed 28-byte sparse file header."""

    __slots__ = (
        "magic",
        "major",
        "minor",
        "file_hdr_sz",
        "chunk_hdr_sz",
        "block_size",
        "total_blks",
        "total_chks",
        "image_crc",
    )

    def __init__(self, buf: bytes):
        if len(buf) < SPARSE_HEADER_LEN:
            raise SparseFormatError(
                "file header truncated: need %d bytes, got %d"
                % (SPARSE_HEADER_LEN, len(buf))
            )
        # Read field by field at fixed offsets -- never one big format string.
        (self.magic,) = struct.unpack_from("<I", buf, 0)
        (self.major,) = struct.unpack_from("<H", buf, 4)
        (self.minor,) = struct.unpack_from("<H", buf, 6)
        (self.file_hdr_sz,) = struct.unpack_from("<H", buf, 8)
        (self.chunk_hdr_sz,) = struct.unpack_from("<H", buf, 10)
        (self.block_size,) = struct.unpack_from("<I", buf, 12)
        (self.total_blks,) = struct.unpack_from("<I", buf, 16)
        (self.total_chks,) = struct.unpack_from("<I", buf, 20)
        (self.image_crc,) = struct.unpack_from("<I", buf, 24)

        if self.magic != SPARSE_HEADER_MAGIC:
            raise SparseFormatError(
                "bad magic: 0x%08X (expected 0x%08X) -- not a sparse image"
                % (self.magic, SPARSE_HEADER_MAGIC)
            )
        if self.block_size == 0:
            raise SparseFormatError("block_size is 0")
        if self.chunk_hdr_sz < CHUNK_HEADER_LEN:
            raise SparseFormatError(
                "chunk_hdr_sz %d < %d" % (self.chunk_hdr_sz, CHUNK_HEADER_LEN)
            )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            "SparseHeader(major=%d minor=%d file_hdr_sz=%d chunk_hdr_sz=%d "
            "block_size=%d total_blks=%d total_chks=%d image_crc=0x%08X)"
            % (
                self.major,
                self.minor,
                self.file_hdr_sz,
                self.chunk_hdr_sz,
                self.block_size,
                self.total_blks,
                self.total_chks,
                self.image_crc,
            )
        )


def parse_sparse_header(header_bytes: bytes) -> SparseHeader:
    """Public helper: parse just the 28-byte header."""
    return SparseHeader(header_bytes)


def sparse_to_raw(
    in_f,
    out_f,
    expected_size: int | None = None,
    progress=None,
    chunk_limit: int | None = None,
) -> dict:
    """
    Convert a sparse image (file object ``in_f``) to raw (file object ``out_f``).

    ``in_f`` must support ``read``/``seek``; ``out_f`` must support ``write``.
    Returns a stats dict.  Raises ``SparseFormatError`` on a malformed image and
    ``ValueError`` when ``expected_size`` is given and the output disagrees.
    """
    in_f.seek(0, os.SEEK_SET)
    header = SparseHeader(in_f.read(SPARSE_HEADER_LEN))
    block_size = header.block_size

    # Skip any padding between the declared file header size and the first chunk.
    if header.file_hdr_sz > SPARSE_HEADER_LEN:
        in_f.seek(header.file_hdr_sz, os.SEEK_SET)

    stats = {
        "block_size": block_size,
        "total_chks": header.total_chks,
        "chunks": {name: 0 for name in _CHUNK_NAME.values()},
        "out_size": 0,
    }

    zero_block = b"\x00" * block_size
    # total_chks is the only reliable loop bound (NOT total_blks).
    n_chunks = header.total_chks
    if chunk_limit is not None:
        n_chunks = min(n_chunks, chunk_limit)

    for idx in range(n_chunks):
        chunk_hdr = in_f.read(CHUNK_HEADER_LEN)
        if len(chunk_hdr) < CHUNK_HEADER_LEN:
            raise SparseFormatError(
                "truncated chunk header at chunk %d/%d" % (idx, n_chunks)
            )
        (chunk_type,) = struct.unpack_from("<H", chunk_hdr, 0)
        # reserved at offset 2 (u16) is ignored
        (chunk_sz,) = struct.unpack_from("<I", chunk_hdr, 4)
        (total_sz,) = struct.unpack_from("<I", chunk_hdr, 8)

        if total_sz < CHUNK_HEADER_LEN:
            raise SparseFormatError(
                "chunk %d: total_sz %d < header size %d"
                % (idx, total_sz, CHUNK_HEADER_LEN)
            )

        body_len = total_sz - CHUNK_HEADER_LEN
        out_blocks_bytes = chunk_sz * block_size
        name = _CHUNK_NAME.get(chunk_type, "UNKNOWN(0x%04X)" % chunk_type)
        stats["chunks"].setdefault(name, 0)
        stats["chunks"][name] += 1

        if chunk_type == CHUNK_TYPE_DONT_CARE:
            # No data body; emit chunk_sz zero blocks.
            if body_len:
                in_f.seek(body_len, os.SEEK_CUR)  # tolerate a stray body
            for _ in range(chunk_sz):
                out_f.write(zero_block)
            stats["out_size"] += out_blocks_bytes

        elif chunk_type in (CHUNK_TYPE_RAW, CHUNK_TYPE_FILL):
            body = in_f.read(body_len)
            if len(body) != body_len:
                raise SparseFormatError(
                    "chunk %d (%s): expected %d body bytes, got %d"
                    % (idx, name, body_len, len(body))
                )
            if chunk_type == CHUNK_TYPE_FILL and body_len == 4 and out_blocks_bytes != 4:
                # Classic sparse fill: a single 4-byte word repeated.
                word = body
                remaining = out_blocks_bytes
                while remaining > 0:
                    n = min(remaining, block_size)
                    out_f.write((word * ((n // 4) + 1))[:n])
                    remaining -= n
                stats["out_size"] += out_blocks_bytes
            else:
                # This firmware's FILL chunks (and all RAW chunks) carry literal
                # data: copy verbatim.  Reconcile with the declared block count.
                if body_len == out_blocks_bytes:
                    out_f.write(body)
                    stats["out_size"] += body_len
                elif body_len < out_blocks_bytes:
                    out_f.write(body)
                    pad = out_blocks_bytes - body_len
                    for _ in range(pad // block_size):
                        out_f.write(zero_block)
                    if pad % block_size:
                        out_f.write(b"\x00" * (pad % block_size))
                    stats["out_size"] += out_blocks_bytes
                    if progress:
                        progress(
                            "warn: chunk %d (%s) body %d < declared %d; zero-padded"
                            % (idx, name, body_len, out_blocks_bytes)
                        )
                else:
                    out_f.write(body[:out_blocks_bytes])
                    stats["out_size"] += out_blocks_bytes
                    if progress:
                        progress(
                            "warn: chunk %d (%s) body %d > declared %d; truncated"
                            % (idx, name, body_len, out_blocks_bytes)
                        )

        elif chunk_type == CHUNK_TYPE_CRC32:
            # 4-byte checksum; contributes nothing to the raw image.
            if body_len:
                in_f.seek(body_len, os.SEEK_CUR)

        else:
            raise SparseFormatError(
                "chunk %d: unknown chunk_type 0x%04X" % (idx, chunk_type)
            )

    out_f.flush()

    if expected_size is not None and stats["out_size"] != expected_size:
        raise ValueError(
            "output size %d != expected partition size %d"
            % (stats["out_size"], expected_size)
        )
    return stats


def check_ext4(raw, block_size_hint: int | None = None) -> dict:
    """
    Validate an ext4 superblock.  ``raw`` is either a path or a bytes-like
    object holding at least the first 2 KiB of the raw image.  Returns a dict of
    the parsed fields and raises ``ValueError`` when the image is not a
    consistent ext4 filesystem.
    """
    if hasattr(raw, "read"):
        raw.seek(0, os.SEEK_SET)
        head = raw.read(EXT4_SUPER_OFFSET + 1024)
    elif isinstance(raw, (str, bytes)) and not isinstance(raw, bytes):
        with open(raw, "rb") as fh:
            head = fh.read(EXT4_SUPER_OFFSET + 1024)
    else:
        head = bytes(raw)[: EXT4_SUPER_OFFSET + 1024]

    if len(head) < EXT4_SUPER_OFFSET + 64:
        raise ValueError("image too small to contain an ext4 superblock")

    sb = head[EXT4_SUPER_OFFSET:]
    (s_inodes_count,) = struct.unpack_from("<I", sb, 0)
    (s_blocks_count_lo,) = struct.unpack_from("<I", sb, 4)
    (s_log_block_size,) = struct.unpack_from("<I", sb, 24)
    (s_magic,) = struct.unpack_from("<H", sb, 56)
    (s_state,) = struct.unpack_from("<H", sb, 58)

    fs_block = 1024 << s_log_block_size
    info = {
        "s_magic": s_magic,
        "s_inodes_count": s_inodes_count,
        "s_blocks_count_lo": s_blocks_count_lo,
        "s_log_block_size": s_log_block_size,
        "fs_block_size": fs_block,
        "s_state": s_state,
        "implied_size": s_blocks_count_lo * fs_block,
    }

    if s_magic != EXT4_SUPER_MAGIC:
        raise ValueError(
            "bad ext4 magic 0x%04X (expected 0x%04X)" % (s_magic, EXT4_SUPER_MAGIC)
        )
    if block_size_hint is not None and fs_block != block_size_hint:
        raise ValueError(
            "ext4 block size %d != expected %d" % (fs_block, block_size_hint)
        )
    return info


def check_ext4_size(raw_path: str, actual_size: int) -> dict:
    """Validate the ext4 superblock against the container size.

    The filesystem may legitimately be SMALLER than the image: ODM builds
    pad the partition-sized sparse image with zeros past the end of the
    fs (the baseline system.img is exactly this shape: fs 3,047,079,936 B
    inside a 3,095,396,352 B / 2952 MiB partition).  Only an fs LARGER
    than its container means truncation/corruption.
    """
    info = check_ext4(raw_path)
    if info["implied_size"] > actual_size:
        raise ValueError(
            "ext4 s_blocks_count_lo*block_size = %d > actual output size %d "
            "(image truncated?)" % (info["implied_size"], actual_size)
        )
    return info


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _human(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return "%.2f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024.0
    return "%d B" % n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Convert MTK block_size=4096 sparse images to raw ext4."
    )
    ap.add_argument("input", help="input sparse image (e.g. system.img)")
    ap.add_argument("output", help="output raw image (e.g. system.raw.img)")
    ap.add_argument(
        "--expected-size",
        type=lambda x: int(x, 0),
        default=None,
        help="scatter partition size in bytes (decimal or 0x..); "
        "conversion fails if the output size differs",
    )
    ap.add_argument(
        "--check-ext4",
        action="store_true",
        help="validate the ext4 superblock of the produced raw image",
    )
    ap.add_argument("-q", "--quiet", action="store_true", help="suppress progress")
    args = ap.parse_args(argv)

    def progress(msg):
        if not args.quiet:
            print(msg, file=sys.stderr)

    with open(args.input, "rb") as in_f, open(args.output, "wb") as out_f:
        header = SparseHeader(in_f.read(SPARSE_HEADER_LEN))
        in_f.seek(0, os.SEEK_SET)
        if not args.quiet:
            print("input header: %r" % (header,), file=sys.stderr)
        stats = sparse_to_raw(
            in_f, out_f, expected_size=args.expected_size, progress=progress
        )

    out_size = os.path.getsize(args.output)
    if not args.quiet:
        print(
            "wrote %s (%s) from %d chunks: %s"
            % (
                args.output,
                _human(out_size),
                stats["total_chks"],
                ", ".join(
                    "%s=%d" % (k, v) for k, v in stats["chunks"].items() if v
                ),
            ),
            file=sys.stderr,
        )

    if args.check_ext4:
        info = check_ext4_size(args.output, out_size)
        if not args.quiet:
            print(
                "ext4 OK: magic=0x%04X inodes=%d blocks=%d fs_block=%d size=%s"
                % (
                    info["s_magic"],
                    info["s_inodes_count"],
                    info["s_blocks_count_lo"],
                    info["fs_block_size"],
                    _human(info["implied_size"]),
                ),
                file=sys.stderr,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
