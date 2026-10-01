#!/usr/bin/env python3
"""gen_fixtures.py: regenerate every binary / PNG selftest fixture.

Usage:
  gen_fixtures.py [--spec fixtures/gen_spec.json] [--check]

Reads the fixture contents from fixtures/gen_spec.json (this script holds no
fixture strings) and writes, relative to the spec's directory:
  - binary text fixtures: NUL dump, UTF-16LE/BE without BOM, gzip (mtime 0),
    zip (fixed date), bare zlib stream
  - PNGs for png_meta built in pure Python (zlib/struct), with the requested
    extra chunks, trailing bytes or a corrupted CRC
  - OCR PNGs for image_scan: SVG text rendered by `rsvg-convert -z <zoom>`,
    then metadata-stripped with capture/strip_png.py, plus the sibling .png.txt
  - small plain PNGs used by the docs_images fixtures
  - img_diff baseline/actual pairs: a pattern encoded with each scanline filter
    and color type, pixel edits, masks files and args.txt
Output is deterministic for a given zlib and rsvg-convert/font setup.
--check regenerates in memory and reports files that differ
from the tree (exit 1) instead of writing.
Exit: 0 ok, 1 --check found differences, 2 usage error / rsvg-convert missing.
"""

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import gzip  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import struct  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
import zipfile  # noqa: E402
import zlib  # noqa: E402
from xml.sax.saxutils import escape  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
GATES_DIR = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(os.path.dirname(GATES_DIR), "capture"))
import strip_png  # noqa: E402

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def chunk(ctype, payload, corrupt=False):
    crc = zlib.crc32(ctype + payload) & 0xFFFFFFFF
    if corrupt:
        crc ^= 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + ctype + payload + struct.pack(">I", crc)


def png_bytes(width=16, height=16, rgb=(240, 240, 240), extra=(), trailing=b"", corrupt_crc=False):
    row = b"\x00" + bytes(rgb) * width
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    parts = [PNG_SIG, chunk(b"IHDR", ihdr)]
    for ctype, payload in extra:
        parts.append(chunk(ctype, payload))
    parts.append(chunk(b"IDAT", zlib.compress(row * height, 9), corrupt=corrupt_crc))
    parts.append(chunk(b"IEND", b""))
    return b"".join(parts) + trailing


def extra_chunk(spec):
    ctype = spec["type"].encode("ascii")
    if "text" in spec:
        return ctype, spec["text"].encode("latin-1")
    if "hex" in spec:
        return ctype, bytes.fromhex(spec["hex"])
    if "iccp_name" in spec:
        return ctype, (spec["iccp_name"].encode("latin-1") + b"\x00\x00"
                       + zlib.compress(spec["profile"].encode("utf-8"), 9))
    raise SystemExit(f"gen_spec: chunk {spec} has no payload")


def nul_dump(seed, strings):
    stream = b""
    counter = 0
    while len(stream) < 2048:
        stream += hashlib.sha256(f"{seed}:{counter}".encode()).digest()
        counter += 1
    out = bytearray(b"\x00\x01\x02\x03")
    step = len(stream) // (len(strings) + 1)
    for i, s in enumerate(strings):
        out += stream[i * step:(i + 1) * step]
        out += b"\x00" + s.encode("utf-8") + b"\x00"
    out += stream[len(strings) * step:]
    return bytes(out)


def deterministic_zip(member, text):
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo(member, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        info.create_system = 3
        zf.writestr(info, text.encode("utf-8"))
    return buf.getvalue()


def deterministic_zip_members(members):
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for m in members:
            info = zipfile.ZipInfo(m["name"], date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 3
            zf.writestr(info, png_bytes() if m.get("png") else m["text"].encode("utf-8"))
    return buf.getvalue()


def pattern_pixel(pattern, x, y):
    if pattern == "gradient":
        return ((x * 7 + y * 3) % 256, (x * x + y * 11) % 256, (x * y + 40) % 256)
    if pattern == "palette":
        return ((x * 16) % 256, (y * 16) % 256, 128)
    if pattern == "gray":
        v = (x * 9 + y * 5) % 256
        return (v, v, v)
    raise SystemExit(f"gen_spec: unknown pattern {pattern}")


def apply_edit(px, edit):
    op = edit["op"]
    if op == "xor":
        return tuple(c ^ 0x80 for c in px)
    if op == "add":
        return tuple(min(255, c + d) for c, d in zip(px, edit["rgb"]))
    raise SystemExit(f"gen_spec: unknown edit {op}")


def filter_line(ftype, line, prior, bpp):
    out = bytearray(len(line))
    for i, v in enumerate(line):
        a = line[i - bpp] if i >= bpp else 0
        b = prior[i]
        c = prior[i - bpp] if i >= bpp else 0
        if ftype == 0:
            pred = 0
        elif ftype == 1:
            pred = a
        elif ftype == 2:
            pred = b
        elif ftype == 3:
            pred = (a + b) // 2
        else:
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
        out[i] = (v - pred) & 255
    return bytes(out)


def encode_png(item):
    """Encode one img_diff image spec: pattern, size, color_type, filter, edits, interlace."""
    width, height = item.get("size", (40, 30))
    ctype = item.get("color_type", 2)
    ftype = item.get("filter", 0)
    rows = []
    for y in range(height):
        row = []
        for x in range(width):
            px = pattern_pixel(item.get("pattern", "gradient"), x, y)
            for edit in item.get("edits", []):
                x0, y0, x1, y1 = edit.get("rect", (0, 0, width, height))
                if x0 <= x < x1 and y0 <= y < y1:
                    px = apply_edit(px, edit)
            row.append(px)
        rows.append(row)
    extra = []
    if ctype == 2:
        lines, bpp = [b"".join(bytes(p) for p in r) for r in rows], 3
    elif ctype == 6:
        lines, bpp = [b"".join(bytes(p) + b"\xff" for p in r) for r in rows], 4
    elif ctype == 0:
        lines, bpp = [bytes(p[0] for p in r) for r in rows], 1
    elif ctype == 3:
        colors = sorted({p for r in rows for p in r})
        if len(colors) > 256:
            raise SystemExit("gen_spec: palette pattern has more than 256 colors")
        index = {c: i for i, c in enumerate(colors)}
        extra.append((b"PLTE", b"".join(bytes(c) for c in colors)))
        lines, bpp = [bytes(index[p] for p in r) for r in rows], 1
    else:
        raise SystemExit(f"gen_spec: unsupported color type {ctype}")
    raw, prior = b"", bytes(len(lines[0]))
    for line in lines:
        raw += bytes([ftype]) + filter_line(ftype, line, prior, bpp)
        prior = line
    if item.get("truncate_data"):
        raw = raw[: len(raw) // 2]
        idat = zlib.compress(raw, 9)[:-8]
    else:
        idat = zlib.compress(raw, 9)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, ctype, 0, 0, 1 if item.get("interlace") else 0)
    parts = [PNG_SIG, chunk(b"IHDR", ihdr)] + [chunk(t, p) for t, p in extra]
    parts += [chunk(b"IDAT", idat), chunk(b"IEND", b"")]
    return b"".join(parts)


def img_diff_files(spec):
    files = {}
    for fx in spec:
        root = fx["out"]
        if fx.get("args"):
            files[f"{root}/args.txt"] = ("\n".join(fx["args"]) + "\n").encode("utf-8")
        for side in ("baseline", "actual"):
            for item in fx.get(side, []):
                files[f"{root}/{side}/{item['name']}"] = encode_png(item)
                if "masks" in item:
                    masks = {"viewport": {"dpr": item["masks"]["dpr"]}, "rects": item["masks"]["rects"]}
                    files[f"{root}/{side}/{item['name'][:-4]}.masks.json"] = (
                        json.dumps(masks, sort_keys=True) + "\n").encode("utf-8")
    return files


def render_svg_png(lines, font_size, zoom, dst):
    line_h = int(font_size * 1.4)
    pad = max(8, font_size)
    width = int(pad * 2 + max(len(x) for x in lines) * font_size * 0.62)
    height = pad * 2 + line_h * len(lines)
    body = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
            f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
            f'<g font-family="DejaVu Sans Mono, Menlo, monospace" font-size="{font_size}" '
            'fill="#000000" xml:space="preserve">']
    for i, line in enumerate(lines):
        body.append(f'<text x="{pad}" y="{pad + line_h * i + font_size}">{escape(line)}</text>')
    body += ["</g>", "</svg>", ""]
    with tempfile.TemporaryDirectory() as tmp:
        svg = os.path.join(tmp, "f.svg")
        raw = os.path.join(tmp, "f.png")
        with open(svg, "w", encoding="utf-8") as fh:
            fh.write("\n".join(body))
        proc = subprocess.run(["rsvg-convert", "-z", str(zoom), "-f", "png", "-o", raw, svg],
                              capture_output=True)
        if proc.returncode != 0:
            raise SystemExit(f"rsvg-convert failed for {dst}")
        violations, _ = strip_png.strip_file(raw, raw)
        if violations:
            raise SystemExit(f"strip_png left {violations} in {dst}")
        with open(raw, "rb") as fh:
            return fh.read()


def build_all(spec):
    """Return {relative path: bytes}."""
    files = {}
    for item in spec["binary"]:
        kind = item["kind"]
        if kind == "nul-dump":
            files[item["out"]] = nul_dump(item["seed"], item["strings"])
        elif kind in ("utf-16-le", "utf-16-be"):
            files[item["out"]] = item["text"].encode(kind)  # no BOM
        elif kind == "zlib":
            files[item["out"]] = zlib.compress(item["text"].encode("utf-8"), 9)
        elif kind == "gzip":
            files[item["out"]] = gzip.compress(item["text"].encode("utf-8"), compresslevel=9, mtime=0)
        elif kind == "zip":
            files[item["out"]] = deterministic_zip(item["member"], item["text"])
        elif kind == "png":
            files[item["out"]] = png_bytes()
        elif kind == "zip-members":
            files[item["out"]] = deterministic_zip_members(item["members"])
        elif kind == "hex":
            files[item["out"]] = bytes.fromhex(item["hex"])
        else:
            raise SystemExit(f"gen_spec: unknown binary kind {kind}")
    for item in spec["png_meta"]:
        files[item["out"]] = png_bytes(
            rgb=tuple(item.get("rgb", (240, 240, 240))),
            extra=[extra_chunk(c) for c in item.get("chunks", [])],
            trailing=bytes.fromhex(item.get("trailing_hex", "")),
            corrupt_crc=item.get("corrupt_crc", False))
    for item in spec["ocr"]:
        files[item["out"]] = render_svg_png(item["lines"], item["font_size"], item["zoom"], item["out"])
        if item.get("sibling") is not None:
            files[item["out"] + ".txt"] = item["sibling"].encode("utf-8")
    for out in spec["plain_png"]:
        files[out] = png_bytes()
    files.update(img_diff_files(spec.get("img_diff", [])))
    return files


def main(argv=None):
    p = argparse.ArgumentParser(description="Regenerate binary/PNG selftest fixtures.")
    p.add_argument("--spec", default=os.path.join(HERE, "fixtures", "gen_spec.json"))
    p.add_argument("--check", action="store_true")
    args = p.parse_args(argv)
    if shutil.which("rsvg-convert") is None:
        print("ERROR rsvg-convert not found", file=sys.stderr)
        return 2
    try:
        with open(args.spec, encoding="utf-8") as fh:
            spec = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR cannot load spec: {exc.__class__.__name__}", file=sys.stderr)
        return 2
    base = os.path.dirname(args.spec)
    files = build_all(spec)
    differ = 0
    for rel in sorted(files):
        path = os.path.join(base, rel)
        data = files[rel]
        digest = hashlib.sha256(data).hexdigest()[:16]
        if args.check:
            try:
                with open(path, "rb") as fh:
                    same = fh.read() == data
            except OSError:
                same = False
            if not same:
                differ += 1
                print(f"DIFFERS {rel}")
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        print(f"wrote {rel} sha256:{digest}")
    if args.check:
        print(f"checked {len(files)} files, {differ} differ")
        return 1 if differ else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
