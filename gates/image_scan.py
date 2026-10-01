#!/usr/bin/env python3
"""image_scan.py: image leak gate (gates/SPEC.md "image_scan.py").

Usage:
  image_scan.py --roster R --denylist D [--denylist D2 ...] [--policy P ...]
                [--allow-uuids F ...] [--ocr require|off] TARGET...

TARGET is a PNG file or a directory (recursive *.png). Other image formats
(and non-PNG files given explicitly) are reported as UNSUPPORTED_IMAGE.
For each PNG:
  - sibling text "<image>.png.txt" must exist, else MISSING_SIBLING <png>
  - every leakscan rule is applied to the sibling: "<RULE> (sibling) <png>:<line>"
  - OCR (`tesseract <png> stdout`) output gets the same rules: "<RULE> (ocr) <png>:<line>"
--ocr require (default): exit 2 when tesseract is missing or fails.
--ocr off: skip OCR, print "NOTE ocr disabled" on stderr. Use only when chosen explicitly.
The sibling is the first line of defence; OCR misses small text.
Exit: 0 clean, 1 violation, 2 usage error / unreadable input / missing tool.
"""

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402
import leakscan  # noqa: E402

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def discover(targets, display):
    """Return [(path, is_png_candidate)] in deterministic order. Error
    messages show paths through display() (leaking components masked)."""
    def walk_error(exc):
        raise C.GateError(f"cannot list directory {display(exc.filename or '?')}") from None

    out = []
    for target in targets:
        if os.path.isdir(target):
            for root, dirs, names in os.walk(target, onerror=walk_error):
                dirs.sort()
                for name in sorted(names):
                    low = name.lower()
                    if low.endswith(".png"):
                        out.append((os.path.join(root, name), True))
                    elif low.endswith(leakscan.IMAGE_EXTS):
                        out.append((os.path.join(root, name), False))
        elif os.path.isfile(target):
            out.append((target, target.lower().endswith(".png")))
        else:
            raise C.GateError(f"target not found: {display(target)}")
    return out


def run_ocr(path):
    arg = path if not path.startswith("-") else "./" + path
    env = dict(os.environ, OMP_THREAD_LIMIT=os.environ.get("OMP_THREAD_LIMIT", "1"))
    try:
        proc = subprocess.run(["tesseract", arg, "stdout"], capture_output=True,
                              timeout=300, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise C.GateError(f"tesseract failed to run: {exc.__class__.__name__}") from None
    if proc.returncode != 0:
        raise C.GateError(f"tesseract exited {proc.returncode}")
    return proc.stdout.decode("utf-8", errors="replace")


def main(argv=None):
    C.setup_stdio()
    p = argparse.ArgumentParser(description="PNG leak gate: sibling text + OCR.")
    p.add_argument("--roster")
    p.add_argument("--denylist", action="append", default=[])
    p.add_argument("--policy", action="append", default=[])
    p.add_argument("--allow-uuids", action="append", default=[])
    p.add_argument("--ocr", choices=("require", "off"), default="require")
    p.add_argument("targets", nargs="+")
    args = p.parse_args(argv)
    try:
        scanner = leakscan.load_scanner(args)
        if args.ocr == "require":
            if shutil.which("tesseract") is None:
                raise C.GateError("tesseract not found (use --ocr off only when chosen explicitly)")
        else:
            print("NOTE ocr disabled", file=sys.stderr)
        found = False
        images = discover(args.targets, scanner.display_path)
        if not images:
            raise C.GateError("no images found under the given targets")
        for path, is_png in images:
            shown = scanner.display_path(path)
            lines = []
            try:
                with open(path, "rb") as fh:
                    data = fh.read()
            except OSError as exc:
                raise C.GateError(f"cannot read {shown}: {exc.strerror}") from None
            if not is_png or not data.startswith(PNG_SIG):
                lines.append(f"UNSUPPORTED_IMAGE {shown}")
            else:
                sibling = path + ".txt"
                if not os.path.isfile(sibling):
                    lines.append(f"MISSING_SIBLING {shown}")
                else:
                    try:
                        with open(sibling, "rb") as fh:
                            sib_data = fh.read()
                    except OSError as exc:
                        raise C.GateError(f"cannot read sibling of {shown}: {exc.strerror}") from None
                    kind, texts = leakscan.classify(sibling, sib_data)
                    if kind in ("image", "disguised-image", "compressed"):
                        lines.append(f"UNSCANNABLE_SIBLING {shown}")
                    elif kind == "text":
                        lines.extend(f"{label} (sibling) {shown}:{tag}"
                                     for label, tag in scanner.scan_text(texts))
                if args.ocr == "require":
                    lines.extend(f"{label} (ocr) {shown}:{tag}"
                                 for label, tag in scanner.scan_text(run_ocr(path)))
            for line in lines:
                print(line)
            found = found or bool(lines)
        return C.EXIT_FAIL if found else C.EXIT_PASS
    except C.GateError as exc:
        sys.stdout.flush()
        C.error(exc)
        return C.EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
