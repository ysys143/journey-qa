#!/usr/bin/env python3
"""png_meta.py: PNG file-name and metadata-chunk gate (gates/SPEC.md "png_meta.py").

Usage:
  png_meta.py [--name-re REGEX] [--allow-chunk TYPE ...] PNG...

PNG may be a file or a directory (recursive, *.png). Pure-Python chunk walk
with CRC validation; no exiftool needed.

Violations (one line each, "<RULE> <file>"):
  NON_ASCII_NAME           file name has characters outside printable ASCII
  BAD_NAME                 file name does not match --name-re
  NOT_PNG                  bad signature, broken chunk structure, CRC mismatch
  METADATA_CHUNK[<type>]   chunk outside IHDR PLTE IDAT IEND tRNS and --allow-chunk
  TRAILING_DATA            bytes after IEND
Exit: 0 clean, 1 violation, 2 usage error / unreadable input.
"""

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import struct  # noqa: E402
import zlib  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402

PNG_SIG = b"\x89PNG\r\n\x1a\n"
KEEP_CHUNKS = ("IHDR", "PLTE", "IDAT", "IEND", "tRNS")
DEFAULT_NAME_RE = r"^[0-9]{2}-[a-z0-9]+(-[a-z0-9]+)*(\.light|\.dark)?\.png$"
_CHUNK_TYPE_RE = re.compile(rb"^[A-Za-z]{4}$")


class PngError(ValueError):
    pass


def walk_chunks(data):
    """Return ([(type, payload_bytes, raw_chunk_bytes)], trailing_bytes).
    Raises PngError on any structural problem."""
    if not data.startswith(PNG_SIG):
        raise PngError("signature mismatch")
    pos = len(PNG_SIG)
    chunks = []
    while True:
        if pos + 8 > len(data):
            raise PngError("truncated chunk header")
        length, ctype = struct.unpack(">I4s", data[pos:pos + 8])
        if not _CHUNK_TYPE_RE.match(ctype):
            raise PngError("invalid chunk type")
        end = pos + 8 + length + 4
        if length > 0x7FFFFFFF or end > len(data):
            raise PngError("chunk runs past end of file")
        payload = data[pos + 8:pos + 8 + length]
        (crc,) = struct.unpack(">I", data[pos + 8 + length:end])
        if zlib.crc32(ctype + payload) & 0xFFFFFFFF != crc:
            raise PngError("CRC mismatch")
        name = ctype.decode("ascii")
        if not chunks and (name != "IHDR" or length != 13):
            raise PngError("first chunk is not IHDR")
        chunks.append((name, payload, data[pos:end]))
        pos = end
        if name == "IEND":
            return chunks, data[pos:]


def check_png_bytes(data, allow_chunks=()):
    """Content violations for one PNG: NOT_PNG, METADATA_CHUNK[x], TRAILING_DATA."""
    try:
        chunks, trailing = walk_chunks(data)
    except PngError:
        return ["NOT_PNG"]
    if not any(c[0] == "IDAT" for c in chunks):
        return ["NOT_PNG"]
    allowed = set(KEEP_CHUNKS) | set(allow_chunks)
    out = []
    for name, _, _ in chunks:
        label = f"METADATA_CHUNK[{name}]"
        if name not in allowed and label not in out:
            out.append(label)
    if trailing:
        out.append("TRAILING_DATA")
    return out


def check_name(path, name_re):
    base = os.path.basename(path)
    out = []
    if any(not (0x20 <= ord(ch) <= 0x7E) for ch in base):
        out.append("NON_ASCII_NAME")
    if not name_re.match(base):
        out.append("BAD_NAME")
    return out


def discover_pngs(targets):
    files = []
    for target in targets:
        if os.path.isdir(target):
            for root, dirs, names in os.walk(target):
                dirs.sort()
                for name in sorted(names):
                    if name.lower().endswith(".png"):
                        files.append(os.path.join(root, name))
        elif os.path.isfile(target):
            files.append(target)
        else:
            raise C.GateError(f"not found: {target}")
    return files


def main(argv=None):
    C.setup_stdio()
    p = argparse.ArgumentParser(description="PNG file name and metadata chunk gate.")
    p.add_argument("--name-re", default=DEFAULT_NAME_RE)
    p.add_argument("--allow-chunk", action="append", default=[])
    p.add_argument("pngs", nargs="+")
    args = p.parse_args(argv)
    try:
        try:
            name_re = re.compile(args.name_re)
        except re.error as exc:
            raise C.GateError(f"--name-re does not compile ({exc})") from None
        for chunk in args.allow_chunk:
            if not _CHUNK_TYPE_RE.match(chunk.encode("ascii", "replace")):
                raise C.GateError(f"--allow-chunk {chunk!r} is not a 4-letter chunk type")
        files = discover_pngs(args.pngs)
        if not files:
            raise C.GateError("no PNG files found")
        found = False
        for path in files:
            try:
                with open(path, "rb") as fh:
                    data = fh.read()
            except OSError as exc:
                raise C.GateError(f"cannot read {path}: {exc.strerror}") from None
            for label in check_name(path, name_re) + check_png_bytes(data, args.allow_chunk):
                print(f"{label} {path}")
                found = True
        return C.EXIT_FAIL if found else C.EXIT_PASS
    except C.GateError as exc:
        sys.stdout.flush()
        C.error(exc)
        return C.EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
