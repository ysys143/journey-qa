#!/usr/bin/env python3
"""strip_png.py: drop PNG metadata (gates/SPEC.md "capture/ tools").

Usage:
  strip_png.py IN [OUT]

Keeps only IHDR PLTE IDAT IEND tRNS chunks and drops any bytes after IEND.
Writes OUT (default: rewrite IN in place, atomically), re-checks the result
with png_meta's chunk check, and prints "sha256 <hex> <OUT>".
Exit: 0 ok, 1 re-check failed, 2 usage error / not a PNG / unreadable.
"""

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import hashlib  # noqa: E402
import os  # noqa: E402

GATES_DIR = os.environ.get("JQA_GATES_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gates")
sys.path.insert(0, GATES_DIR)
import _common as C  # noqa: E402
import png_meta  # noqa: E402


def strip_bytes(data):
    """Return stripped PNG bytes. Raises png_meta.PngError if not a valid PNG."""
    chunks, _trailing = png_meta.walk_chunks(data)
    keep = [raw for name, _, raw in chunks if name in png_meta.KEEP_CHUNKS]
    return png_meta.PNG_SIG + b"".join(keep)


def strip_file(src, dst):
    """Strip src into dst. Returns (violations, sha256_hex)."""
    try:
        with open(src, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise C.GateError(f"cannot read {src}: {exc.strerror}") from None
    try:
        out = strip_bytes(data)
    except png_meta.PngError as exc:
        raise C.GateError(f"{src} is not a valid PNG ({exc})") from None
    tmp = dst + ".tmp-strip"
    try:
        with open(tmp, "wb") as fh:
            fh.write(out)
        os.replace(tmp, dst)
    except OSError as exc:
        raise C.GateError(f"cannot write {dst}: {exc.strerror}") from None
    with open(dst, "rb") as fh:
        written = fh.read()
    return png_meta.check_png_bytes(written), hashlib.sha256(written).hexdigest()


def main(argv=None):
    C.setup_stdio()
    p = argparse.ArgumentParser(description="Strip PNG metadata chunks and trailing bytes.")
    p.add_argument("src", metavar="IN")
    p.add_argument("dst", metavar="OUT", nargs="?")
    args = p.parse_args(argv)
    dst = args.dst or args.src
    try:
        violations, digest = strip_file(args.src, dst)
    except C.GateError as exc:
        C.error(exc)
        return C.EXIT_USAGE
    for label in violations:
        print(f"{label} {dst}")
    print(f"sha256 {digest} {dst}")
    return C.EXIT_FAIL if violations else C.EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
