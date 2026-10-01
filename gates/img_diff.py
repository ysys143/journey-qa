#!/usr/bin/env python3
"""img_diff.py: compare captures with approved baseline images (gates/SPEC.md "img_diff.py").

Usage:
  img_diff.py [--threshold 0.01] [--tolerance 24] BASELINE ACTUAL

BASELINE and ACTUAL are two PNG files, or two directories whose *.png files
(recursive) are paired by relative path. A pixel differs when the largest
absolute difference over its R, G and B channels exceeds --tolerance. Pixels
inside mask rectangles are ignored: <name>.masks.json next to either image
({"viewport": {"dpr": N}, "rects": [{"x","y","w","h"}]}, CSS px scaled by
dpr; the union of both files applies). The metric is
  differing unmasked pixels / unmasked pixels
and a pair passes when it is at or below --threshold. PNG decoding is pure
Python (zlib + the five scanline filters); alpha is ignored.

Violations (one line each):
  IMAGE_DIFF <actual> ratio=<r>   metric above --threshold
  SIZE_MISMATCH <actual>          dimensions differ from the baseline
  FULLY_MASKED <actual>           no unmasked pixel left to compare
  MISSING_ACTUAL <baseline>       a baseline image has no capture (directory mode)
  NO_BASELINE <actual>            a capture has no baseline image (directory mode)
  NOT_PNG <file>                  signature, chunk structure, CRC or data damaged
  UNSUPPORTED_PNG <file>          interlaced, or a bit depth / color type this decoder lacks
Exit: 0 every pair passes, 1 violation, 2 usage error / unreadable input /
bad masks file / no PNG found.
"""

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import itertools  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import os  # noqa: E402
import struct  # noqa: E402
import zlib  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402
import png_meta  # noqa: E402

CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
DEPTHS = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}


class Unsupported(ValueError):
    pass


def _unfilter(data, height, stride, bpp):
    """Yield reconstructed scanlines (bytes) from the decompressed IDAT stream."""
    if len(data) < height * (stride + 1):
        raise png_meta.PngError("image data too short")
    prior = bytes(stride)
    width_mask = (1 << (8 * stride)) - 1 if stride else 0
    low7 = int.from_bytes(b"\x7f" * stride, "big") if stride else 0
    high = int.from_bytes(b"\x80" * stride, "big") if stride else 0
    pos = 0
    for _ in range(height):
        ftype = data[pos]
        line = data[pos + 1:pos + 1 + stride]
        pos += stride + 1
        if ftype == 0:
            out = bytes(line)
        elif ftype == 1:  # Sub: running sum per byte lane, modulo 256
            buf = bytearray(stride)
            for lane in range(bpp):
                buf[lane::bpp] = bytes(map((255).__and__, itertools.accumulate(line[lane::bpp])))
            out = bytes(buf)
        elif ftype == 2:  # Up: bytewise add without carries across bytes
            a = int.from_bytes(line, "big")
            b = int.from_bytes(prior, "big")
            out = ((((a & low7) + (b & low7)) ^ ((a ^ b) & high)) & width_mask).to_bytes(stride, "big")
        elif ftype == 3:  # Average
            buf = bytearray(line)
            for i in range(stride):
                left = buf[i - bpp] if i >= bpp else 0
                buf[i] = (buf[i] + ((left + prior[i]) >> 1)) & 255
            out = bytes(buf)
        elif ftype == 4:  # Paeth
            buf = bytearray(line)
            for i in range(bpp):
                buf[i] = (buf[i] + prior[i]) & 255
            for i in range(bpp, stride):
                a = buf[i - bpp]
                b = prior[i]
                c = prior[i - bpp]
                pa = b - c if b > c else c - b
                pb = a - c if a > c else c - a
                pc = a + b - c - c
                if pc < 0:
                    pc = -pc
                if pa <= pb and pa <= pc:
                    pred = a
                elif pb <= pc:
                    pred = b
                else:
                    pred = c
                buf[i] = (buf[i] + pred) & 255
            out = bytes(buf)
        else:
            raise png_meta.PngError("unknown filter type")
        yield out
        prior = out


def _unpack_bits(line, depth, count):
    """Samples of a sub-byte scanline, one per byte, still unscaled."""
    per = 8 // depth
    mask = (1 << depth) - 1
    table = [bytes((b >> (8 - depth * (k + 1))) & mask for k in range(per)) for b in range(256)]
    return b"".join(table[b] for b in line)[:count]


def decode_png(data):
    """Return (width, height, rows) with each row as packed RGB bytes.
    Raises png_meta.PngError (damaged) or Unsupported."""
    chunks, _trailing = png_meta.walk_chunks(data)
    width, height, depth, ctype, comp, filt, interlace = struct.unpack(">IIBBBBB", chunks[0][1])
    if ctype not in DEPTHS or depth not in DEPTHS[ctype] or comp != 0 or filt != 0:
        raise Unsupported("color type / bit depth")
    if interlace != 0:
        raise Unsupported("interlaced")
    if width == 0 or height == 0:
        raise png_meta.PngError("empty image")
    palette = None
    idat = []
    for name, payload, _raw in chunks:
        if name == "PLTE":
            palette = payload
        elif name == "IDAT":
            idat.append(payload)
    if ctype == 3 and (palette is None or len(palette) % 3):
        raise png_meta.PngError("palette missing")
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error:
        raise png_meta.PngError("compressed data damaged") from None
    channels = CHANNELS[ctype]
    bits = channels * depth
    stride = (width * bits + 7) // 8
    bpp = max(1, bits // 8)
    rows = []
    if ctype == 3:
        entries = len(palette) // 3
        lut = [bytes(palette[i * 3 + k] if i < entries else 0 for i in range(256)) for k in range(3)]
    for line in _unfilter(raw, height, stride, bpp):
        if depth == 16:
            line = line[0::2]  # high byte of each sample
        elif depth < 8:
            line = _unpack_bits(line, depth, width)
            if ctype == 0:
                scale = 255 // ((1 << depth) - 1)
                line = bytes(v * scale for v in line)
        rgb = bytearray(width * 3)
        if ctype == 2:
            rgb[:] = line
        elif ctype == 6:
            rgb[0::3], rgb[1::3], rgb[2::3] = line[0::4], line[1::4], line[2::4]
        elif ctype == 0:
            rgb[0::3] = rgb[1::3] = rgb[2::3] = line
        elif ctype == 4:
            gray = line[0::2]
            rgb[0::3] = rgb[1::3] = rgb[2::3] = gray
        else:
            idx = bytes(line)
            if entries < 256 and max(idx) >= entries:
                raise png_meta.PngError("palette index out of range")
            rgb[0::3] = idx.translate(lut[0])
            rgb[1::3] = idx.translate(lut[1])
            rgb[2::3] = idx.translate(lut[2])
        rows.append(bytes(rgb))
    return width, height, rows


def masks_path(png):
    return png[:-len(".png")] + ".masks.json"


def load_masks(png, width, height):
    """Mask rectangles in physical pixels as {row: [(x0, x1), ...]} (merged later)."""
    path = masks_path(png)
    if not os.path.isfile(path):
        return []
    data = C.load_json(path, "masks file")
    try:
        dpr = float((data.get("viewport") or {}).get("dpr", 1))
        rects = data["rects"]
        if not isinstance(rects, list) or dpr <= 0:
            raise ValueError
        out = []
        for r in rects:
            x, y, w, h = (float(r[k]) for k in ("x", "y", "w", "h"))
            if w < 0 or h < 0:
                raise ValueError
            x0, y0 = max(0, math.floor(x * dpr)), max(0, math.floor(y * dpr))
            x1, y1 = min(width, math.ceil((x + w) * dpr)), min(height, math.ceil((y + h) * dpr))
            if x1 > x0 and y1 > y0:
                out.append((x0, y0, x1, y1))
        return out
    except (AttributeError, KeyError, TypeError, ValueError):
        raise C.GateError(f"masks file is malformed: {path}") from None


def compare(base_rows, act_rows, width, height, rects, tolerance):
    """Return (differing unmasked pixels, unmasked pixels)."""
    diff = 0
    unmasked = 0
    for y in range(height):
        ra, rb = base_rows[y], act_rows[y]
        live = bytearray(b"\x01") * width
        for x0, y0, x1, y1 in rects:
            if y0 <= y < y1:
                live[x0:x1] = bytes(x1 - x0)
        unmasked += live.count(1)
        if ra == rb:
            continue
        for x in range(width):
            if not live[x]:
                continue
            i = 3 * x
            if ra[i] == rb[i] and ra[i + 1] == rb[i + 1] and ra[i + 2] == rb[i + 2]:
                continue
            if (abs(ra[i] - rb[i]) > tolerance or abs(ra[i + 1] - rb[i + 1]) > tolerance
                    or abs(ra[i + 2] - rb[i + 2]) > tolerance):
                diff += 1
    return diff, unmasked


def read_png(path):
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise C.GateError(f"cannot read {path}: {exc.strerror}") from None
    return decode_png(data)


def check_pair(base, act, threshold, tolerance):
    """Violation lines for one pair."""
    decoded = []
    for path in (base, act):
        try:
            decoded.append(read_png(path))
        except png_meta.PngError:
            return [f"NOT_PNG {path}"]
        except Unsupported:
            return [f"UNSUPPORTED_PNG {path}"]
    (wa, ha, rows_a), (wb, hb, rows_b) = decoded
    if (wa, ha) != (wb, hb):
        return [f"SIZE_MISMATCH {act}"]
    rects = load_masks(base, wa, ha) + load_masks(act, wa, ha)
    diff, unmasked = compare(rows_a, rows_b, wa, ha, rects, tolerance)
    if unmasked == 0:
        return [f"FULLY_MASKED {act}"]
    ratio = diff / unmasked
    if ratio > threshold:
        return [f"IMAGE_DIFF {act} ratio={ratio:.6f}"]
    return []


def list_pngs(root):
    found = []
    for dirpath, dirs, names in os.walk(root):
        dirs.sort()
        for name in sorted(names):
            if name.lower().endswith(".png"):
                found.append(os.path.relpath(os.path.join(dirpath, name), root))
    return found


def pairs(baseline, actual):
    """Return ([(base, act)], violations) for files or directories."""
    if os.path.isfile(baseline) and os.path.isfile(actual):
        return [(baseline, actual)], []
    if not (os.path.isdir(baseline) and os.path.isdir(actual)):
        raise C.GateError("BASELINE and ACTUAL must be two existing files or two directories")
    base_set, act_set = list_pngs(baseline), list_pngs(actual)
    out, violations = [], []
    for rel in base_set:
        if rel in act_set:
            out.append((os.path.join(baseline, rel), os.path.join(actual, rel)))
        else:
            violations.append(f"MISSING_ACTUAL {os.path.join(baseline, rel)}")
    for rel in act_set:
        if rel not in base_set:
            violations.append(f"NO_BASELINE {os.path.join(actual, rel)}")
    if not base_set and not act_set:
        raise C.GateError("no PNG files found")
    return out, violations


def main(argv=None):
    C.setup_stdio()
    p = argparse.ArgumentParser(description="Masked pixel comparison against baseline images.")
    p.add_argument("--threshold", type=float, default=0.01)
    p.add_argument("--tolerance", type=int, default=24)
    p.add_argument("baseline")
    p.add_argument("actual")
    args = p.parse_args(argv)
    try:
        if not 0 <= args.threshold <= 1:
            raise C.GateError("--threshold must be within 0..1")
        if not 0 <= args.tolerance <= 255:
            raise C.GateError("--tolerance must be within 0..255")
        todo, lines = pairs(args.baseline, args.actual)
        for base, act in todo:
            lines.extend(check_pair(base, act, args.threshold, args.tolerance))
        for line in lines:
            print(line)
        sys.stdout.flush()
        print(f"NOTE compared {len(todo)} pair(s)", file=sys.stderr)
        return C.EXIT_FAIL if lines else C.EXIT_PASS
    except C.GateError as exc:
        sys.stdout.flush()
        C.error(exc)
        return C.EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
