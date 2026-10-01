#!/usr/bin/env python3
"""render_terminal.py: render a (redacted) transcript as a terminal-style PNG.

Usage:
  render_terminal.py TRANSCRIPT OUT.png [--cols N] [--max-cols 240] [--scale 2]

Builds a monospace SVG (font "DejaVu Sans Mono", 16px, line
height 22, padding 18, background #1e1e1e, foreground #d4d4d4), rasterizes it
with `rsvg-convert -z SCALE` (--scale, default 2), strips metadata with strip_png, and copies the
transcript byte-for-byte to the sibling OUT.png.txt (what image_scan reads).
The capture spec's 2x rule applies to real captures: leave --scale at 2 there.
A smaller --scale is for repository sample images only. Tabs expand to 8 columns.
Nothing is cut silently. Without --cols the image is as wide as the longest line, up to
--max-cols (default 240). A line longer than the width (--cols, or --max-cols when --cols is
absent) is wrapped: every segment but the last ends with the visible continuation marker
U+21B5, and the width includes that marker. What happened is printed ("render cols=.. longest=..
wrapped_lines=..") and recorded in the sidecar OUT.png.render.json. The sibling OUT.png.txt
stays the unwrapped transcript, so identifiers split by a wrap remain whole for the text gate.
Requires the DejaVu Sans Mono font installed where rsvg-convert runs: the
character width is DejaVu-specific, and another font misaligns the layout.
This tool does not redact: run capture/redact.py first and leakscan the result.
Exit: 0 written, 1 strip re-check failed, 2 usage error / unreadable input /
rsvg-convert missing or failing.
"""

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
from xml.sax.saxutils import escape  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strip_png  # noqa: E402  (also puts the gates dir on sys.path)
import _common as C  # noqa: E402

FONT_FAMILY = "DejaVu Sans Mono"
FONT_SIZE = 16
LINE_HEIGHT = 22
PADDING = 18
CHAR_WIDTH = 9.65  # DejaVu Sans Mono advance at 16px (0.602 em)
BG = "#1e1e1e"
FG = "#d4d4d4"


MARKER = "\u21b5"  # visible continuation marker at the end of a wrapped segment
DEFAULT_MAX_COLS = 240


def split_lines(text):
    lines = [raw.expandtabs(8) for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    if lines and lines[-1] == "":
        lines.pop()
    return lines or [""]


def wrap_lines(text, cols):
    """Return (lines, wrapped): every line longer than cols is split into segments of
    cols-1 characters plus MARKER; wrapped counts the source lines that were split."""
    lines = []
    wrapped = 0
    for raw in split_lines(text):
        if len(raw) > cols:
            wrapped += 1
            while len(raw) > cols:
                lines.append(raw[:cols - 1] + MARKER)
                raw = raw[cols - 1:]
        lines.append(raw)
    return lines, wrapped


def build_svg(lines, cols):
    width = int(round(PADDING * 2 + cols * CHAR_WIDTH))
    height = PADDING * 2 + LINE_HEIGHT * len(lines)
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="{BG}"/>',
        f'<g font-family="{escape(FONT_FAMILY)}" font-size="{FONT_SIZE}" fill="{FG}" '
        'xml:space="preserve">',
    ]
    for i, line in enumerate(lines):
        y = PADDING + LINE_HEIGHT * i + FONT_SIZE
        safe = "".join(ch for ch in line if ch == "\t" or ord(ch) >= 0x20)
        out.append(f'<text x="{PADDING}" y="{y}">{escape(safe)}</text>')
    out.append("</g>")
    out.append("</svg>")
    return "\n".join(out) + "\n"


def main(argv=None):
    C.setup_stdio()
    p = argparse.ArgumentParser(description="Render a transcript as a terminal PNG.")
    p.add_argument("transcript", metavar="TRANSCRIPT")
    p.add_argument("out", metavar="OUT.png")
    p.add_argument("--cols", type=int, default=None, help="image width in columns (default: the longest line, up to --max-cols)")
    p.add_argument("--max-cols", type=int, default=DEFAULT_MAX_COLS, help="cap for the automatic width (default 240)")
    p.add_argument("--scale", type=int, default=2, help="rasterizer zoom (default 2; real captures keep 2)")
    args = p.parse_args(argv)
    try:
        if args.cols is not None and args.cols < 10:
            raise C.GateError("--cols must be at least 10")
        if args.max_cols < 10:
            raise C.GateError("--max-cols must be at least 10")
        if args.scale < 1 or args.scale > 4:
            raise C.GateError("--scale must be 1 to 4")
        if not args.out.lower().endswith(".png"):
            raise C.GateError("OUT must end with .png")
        if shutil.which("rsvg-convert") is None:
            raise C.GateError("rsvg-convert not found")
        try:
            with open(args.transcript, "rb") as fh:
                raw = fh.read()
        except OSError as exc:
            raise C.GateError(f"cannot read {args.transcript}: {exc.strerror}") from None
        text = raw.decode("utf-8", errors="replace")
        longest = max(len(x) for x in split_lines(text))
        cols = args.cols if args.cols is not None else max(10, min(longest, args.max_cols))
        lines, wrapped = wrap_lines(text, cols)
        svg = build_svg(lines, cols)
        with tempfile.TemporaryDirectory() as tmp:
            svg_path = os.path.join(tmp, "render.svg")
            png_path = os.path.join(tmp, "render.png")
            with open(svg_path, "w", encoding="utf-8") as fh:
                fh.write(svg)
            proc = subprocess.run(["rsvg-convert", "-z", str(args.scale), "-f", "png", "-o", png_path, svg_path],
                                  capture_output=True, timeout=120)
            if proc.returncode != 0 or not os.path.isfile(png_path):
                raise C.GateError(f"rsvg-convert exited {proc.returncode}")
            violations, digest = strip_png.strip_file(png_path, png_path)
            if violations:
                for label in violations:
                    print(f"{label} {args.out}")
                return C.EXIT_FAIL
            shutil.copyfile(png_path, args.out)
        shutil.copyfile(args.transcript, args.out + ".txt")
        with open(args.out + ".render.json", "w", encoding="utf-8") as fh:
            json.dump({"cols": cols, "longest_line": longest, "wrapped_lines": wrapped,
                       "continuation_marker": MARKER if wrapped else None,
                       "cols_source": "flag" if args.cols is not None else "longest line (capped)"}, fh, indent=1)
            fh.write("\n")
        print(f"render cols={cols} longest={longest} wrapped_lines={wrapped}"
              + (f" marker=U+21B5" if wrapped else " (no wrapping)"))
        print(f"sha256 {digest} {args.out}")
        print(f"sibling {args.out}.txt")
        print(f"sidecar {args.out}.render.json")
        return C.EXIT_PASS
    except C.GateError as exc:
        C.error(exc)
        return C.EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
