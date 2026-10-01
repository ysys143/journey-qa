#!/usr/bin/env python3
"""evidence.py: redact and gate a failure-evidence directory; keep it only if it passes
(gates/SPEC.md "capture/ tools", capture/SPEC.md "Failure evidence").

Usage:
  evidence.py DIR --roster R --denylist D [--denylist D2 ...] [--policy P ...]
              [--secrets F] [--drop GLOB ...] [--allow-uuids F ...] [--ocr require|off]

Steps, in order:
  1. Archives (zip) are unpacked in place (<name>.d/); nested archives too.
  2. Files no gate can scan are deleted: JPEG, GIF, WebP, video, fonts, and
     every PNG that came out of an archive (only top-level PNGs have sibling text).
     So is every file whose path relative to DIR matches a --drop glob (fnmatch;
     an engine names its records of the runner's own source locations here).
  3. Top-level PNGs are stripped with capture/strip_png.py.
  4. Text files: every --secrets entry is replaced by the redaction marker in its
     raw form and in every nesting of JSON escaping and URL encoding up to three
     levels deep, then capture/redact.py is applied.
  5. Gates: leakscan (--skip-images) on DIR, png_meta and image_scan on the PNGs.
     --secrets entries (all forms) are added to the denylists.
  6. Pass: archives are packed again from the scanned files. Anything else
     (violation, gate error, unreadable file): DIR is deleted.
--secrets uses the denylist format; entries shorter than 4 characters are a
usage error because the denylist rule ignores them.
Output: the gates' violation lines (never matched text), then "DISCARDED <DIR>"
when the directory was deleted. Notes go to stderr.
Exit: 0 kept, 1 violation (DIR deleted), 2 gate error or unreadable input (DIR
deleted) or a command-line usage error (DIR left as is; the caller deletes it).
"""

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import fnmatch  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
import urllib.parse  # noqa: E402
import zipfile  # noqa: E402

CAPTURE_DIR = os.environ.get("JQA_CAPTURE_DIR") or os.path.dirname(os.path.abspath(__file__))
GATES_DIR = os.environ.get("JQA_GATES_DIR") or os.path.join(os.path.dirname(CAPTURE_DIR), "gates")
sys.path.insert(0, GATES_DIR)
import _common as C  # noqa: E402

PNG_SIG = b"\x89PNG\r\n\x1a\n"
ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06")
UNSCANNABLE_MAGIC = (b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"\x1a\x45\xdf\xa3", b"wOFF", b"wOF2",
                     b"OTTO", b"\x00\x01\x00\x00")
UNSCANNABLE_EXT = (".jpg", ".jpeg", ".gif", ".webp", ".webm", ".mp4", ".mov", ".woff", ".woff2",
                   ".ttf", ".otf")
MAX_UNPACKED = 1 << 30
# A request body recorded inside a JSON trace is escaped twice; nest encodings up to this depth.
ENCODINGS = (lambda v: json.dumps(v)[1:-1], lambda v: urllib.parse.quote(v, safe=""),
             lambda v: urllib.parse.quote_plus(v, safe=""))
ENCODING_DEPTH = 3
TIMEOUT = 900


def head(path, n=16):
    with open(path, "rb") as fh:
        return fh.read(n)


def unscannable(path, data):
    if data.startswith(UNSCANNABLE_MAGIC) or path.lower().endswith(UNSCANNABLE_EXT):
        return True
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    return data[4:8] == b"ftyp"


def unpack(zpath, budget):
    """Extract zpath into zpath + '.d' and delete zpath. Returns (dir, bytes used)."""
    dest = zpath + ".d"
    used = 0
    try:
        with zipfile.ZipFile(zpath) as zf:
            for info in zf.infolist():
                name = info.filename
                parts = name.replace("\\", "/").split("/")
                if name.startswith(("/", "\\")) or ".." in parts or ":" in parts[0]:
                    raise C.GateError(f"archive member path escapes the archive: {zpath}")
                if info.is_dir():
                    continue
                used += info.file_size
                if used > budget:
                    raise C.GateError(f"archive unpacks beyond the size limit: {zpath}")
                target = os.path.join(dest, *[p for p in parts if p])
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        raise C.GateError(f"cannot unpack {zpath} ({exc.__class__.__name__})") from None
    os.remove(zpath)
    return dest, used


def pack(src_dir, zpath):
    names = []
    for root, dirs, files in os.walk(src_dir):
        dirs.sort()
        names.extend(os.path.join(root, f) for f in sorted(files))
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in names:
            info = zipfile.ZipInfo(os.path.relpath(path, src_dir).replace(os.sep, "/"),
                                   (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(path, "rb") as fh:
                zf.writestr(info, fh.read())
    shutil.rmtree(src_dir)


def secret_forms(path):
    """Every --secrets entry in raw, JSON-escaped and URL-encoded form, longest first."""
    forms = set()
    text = C.read_text_file(path, "secrets file")
    for line in text.split("\n"):
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        if len(raw) < 4:
            raise C.GateError("secrets file has an entry shorter than 4 characters")
        level = {raw}
        for _ in range(ENCODING_DEPTH):
            level = {f(v) for v in level for f in ENCODINGS}
            forms.update(level)
        forms.add(raw)
    return sorted(forms, key=len, reverse=True)


def run(argv, cwd):
    try:
        proc = subprocess.run([sys.executable] + argv, cwd=cwd, capture_output=True, timeout=TIMEOUT,
                              env=dict(os.environ, JQA_GATES_DIR=GATES_DIR, JQA_CAPTURE_DIR=CAPTURE_DIR))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise C.GateError(f"cannot run {os.path.basename(argv[0])} ({exc.__class__.__name__})") from None
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def prepare(top, forms, marker, policies, drop=()):
    """Steps 1-4. Returns (top-level PNGs, archives to repack, dropped count)."""
    archives, pngs, dropped, budget = [], [], 0, MAX_UNPACKED
    queue = [(top, False)]
    while queue:
        root, inside = queue.pop(0)
        for dirpath, dirs, files in os.walk(root):
            dirs.sort()
            for name in sorted(files):
                path = os.path.join(dirpath, name)
                if os.path.islink(path):
                    raise C.GateError(f"symbolic link in evidence: {name}")
                data = head(path)
                rel = os.path.relpath(path, top).replace(os.sep, "/")
                if any(fnmatch.fnmatch(rel, g) for g in drop):
                    os.remove(path)
                    dropped += 1
                elif data.startswith(ZIP_MAGIC):
                    dest, used = unpack(path, budget)
                    budget -= used
                    archives.append((dest, path))
                    queue.append((dest, True))
                elif (inside and data.startswith(PNG_SIG)) or unscannable(path, data):
                    os.remove(path)
                    dropped += 1
                elif data.startswith(PNG_SIG):
                    pngs.append(path)
                else:
                    redact_text_file(path, forms, marker, policies)
            dirs[:] = [d for d in dirs if not any(os.path.join(dirpath, d) == a for a, _ in archives)]
    for png in pngs:
        rc, _ = run([os.path.join(CAPTURE_DIR, "strip_png.py"), png], os.path.dirname(png))
        if rc != 0:
            raise C.GateError(f"strip_png failed on {os.path.basename(png)} (exit {rc})")
    return pngs, archives, dropped


def redact_text_file(path, forms, marker, policies):
    with open(path, "rb") as fh:
        data = fh.read()
    if b"\x00" in data:
        return  # binary: leakscan judges it as is
    text = data.decode("utf-8", errors="replace")
    for form in forms:
        text = text.replace(form, marker)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    argv = [os.path.join(CAPTURE_DIR, "redact.py"), path]
    for p in policies:
        argv += ["--policy", p]
    rc, out = run(argv, os.path.dirname(path))
    if rc != 0:
        raise C.GateError(f"redact.py failed on {os.path.basename(path)} (exit {rc})")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(out)


def gate(top, pngs, leak_args, ocr):
    """Step 5. Returns (worst exit code, violation lines)."""
    cwd = os.path.dirname(top)
    rel = os.path.basename(top)
    rel_pngs = [os.path.relpath(p, cwd) for p in pngs]
    results = [run([os.path.join(GATES_DIR, "leakscan.py")] + leak_args + ["--skip-images", rel], cwd)]
    if rel_pngs:
        results.append(run([os.path.join(GATES_DIR, "png_meta.py")] + rel_pngs, cwd))
        results.append(run([os.path.join(GATES_DIR, "image_scan.py")] + leak_args
                           + ["--ocr", ocr] + rel_pngs, cwd))
    lines = [ln for _, out in results for ln in out.split("\n") if ln.strip()]
    worst = max(rc for rc, _ in results)
    return (worst if worst in (0, 1) else 2), lines


def main(argv=None):
    C.setup_stdio()
    p = argparse.ArgumentParser(description="Redact and gate a failure-evidence directory.")
    p.add_argument("dir", metavar="DIR")
    p.add_argument("--roster")
    p.add_argument("--denylist", action="append", default=[])
    p.add_argument("--policy", action="append", default=[])
    p.add_argument("--allow-uuids", action="append", default=[])
    p.add_argument("--secrets")
    p.add_argument("--drop", action="append", default=[])
    p.add_argument("--ocr", choices=("require", "off"), default="require")
    args = p.parse_args(argv)
    top = os.path.abspath(args.dir)
    if not os.path.isdir(top):
        C.error(f"not a directory: {args.dir}")
        return C.EXIT_USAGE
    tmp = None
    rc = C.EXIT_USAGE
    lines = []
    try:
        if not args.roster or not args.denylist:
            raise C.GateError("--roster and at least one --denylist are required")
        policy = C.load_policy(args.policy)
        forms = secret_forms(args.secrets) if args.secrets else []
        leak_args = ["--roster", os.path.abspath(args.roster)]
        for d in args.denylist:
            leak_args += ["--denylist", os.path.abspath(d)]
        if forms:
            fd, tmp = tempfile.mkstemp(prefix=".run-secrets-", suffix=".txt",
                                       dir=os.path.dirname(os.path.abspath(args.secrets)))
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("\n".join(forms) + "\n")
            leak_args += ["--denylist", tmp]
        for pol in args.policy:
            leak_args += ["--policy", os.path.abspath(pol)]
        for a in args.allow_uuids:
            leak_args += ["--allow-uuids", os.path.abspath(a)]
        pngs, archives, dropped = prepare(top, forms, policy["redaction_marker"],
                                          [os.path.abspath(x) for x in args.policy], args.drop)
        if dropped:
            print(f"NOTE dropped {dropped} file(s) no gate can scan or listed by --drop", file=sys.stderr)
        rc, lines = gate(top, pngs, leak_args, args.ocr)
        if rc == 0:
            for dest, zpath in reversed(archives):
                pack(dest, zpath)
    except C.GateError as exc:
        C.error(exc)
        rc = C.EXIT_USAGE
    finally:
        if tmp:
            os.remove(tmp)
    for line in lines:
        print(line)
    if rc != 0:
        shutil.rmtree(top, ignore_errors=True)
        print(f"DISCARDED {args.dir}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
