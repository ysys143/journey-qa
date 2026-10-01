#!/usr/bin/env python3
"""manifest.py: freeze and verify the gate tree (gates/SPEC.md "manifest.py").

Usage:
  manifest.py freeze --root DIR --out MANIFEST.sha256 [--exclude GLOB ...]
  manifest.py verify --manifest MANIFEST.sha256 [--exclude GLOB ...]

freeze records "<sha256>  <relative path>" for every file under --root, sorted
by relative path, skipping the manifest file itself and paths matching an
--exclude glob (fnmatch on the full relative path, "/" separated). The first
line records the root relative to the manifest's directory, so verify needs
only --manifest.
verify prints MODIFIED <path>, MISSING <path> and UNLISTED <path> (a file not
in the manifest). Paths matching --exclude are ignored.
Exit: 0 match, 1 drift found, 2 usage error / unreadable or malformed manifest.
"""

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import fnmatch  # noqa: E402
import hashlib  # noqa: E402
import os  # noqa: E402
import posixpath  # noqa: E402
import re  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402

HEADER = "# jqa-manifest v1 root="
ENTRY = re.compile(r"^([0-9a-f]{64})  (.+)$")


def sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
    except OSError as exc:
        raise C.GateError(f"cannot read {path}: {exc.strerror}") from None
    return h.hexdigest()


def excluded(rel, globs):
    return any(fnmatch.fnmatchcase(rel, g) for g in globs)


def list_files(root, skip_path, globs):
    """{relative posix path: filesystem path} for every file under root."""
    out = {}
    skip_norm = os.path.normpath(skip_path) if skip_path else None
    for cur, dirs, names in os.walk(root):
        # Interpreter caches and OS metadata are build noise, never part of a frozen gate.
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(names):
            if name.endswith((".pyc", ".pyo")) or name == ".DS_Store":
                continue
            full = os.path.join(cur, name)
            if skip_norm and os.path.normpath(full) == skip_norm:
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if excluded(rel, globs):
                continue
            if not os.path.isfile(full):
                raise C.GateError(f"not a regular file: {full}")
            out[rel] = full
    return out


def freeze(root, out_path, globs):
    if not os.path.isdir(root):
        raise C.GateError(f"--root is not a directory: {root}")
    files = list_files(root, out_path, globs)
    out_dir = os.path.dirname(out_path) or "."
    root_rel = os.path.relpath(root, out_dir).replace(os.sep, "/")
    lines = [HEADER + root_rel]
    lines += [f"{sha256_file(files[rel])}  {rel}" for rel in sorted(files)]
    tmp = out_path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(lines) + "\n")
        os.replace(tmp, out_path)
    except OSError as exc:
        raise C.GateError(f"cannot write {out_path}: {exc.strerror}") from None
    print(f"FROZEN {len(files)} files -> {out_path}")
    return C.EXIT_PASS


def verify(manifest, globs):
    text = C.read_text_file(manifest, "manifest")
    lines = text.split("\n")
    if not lines or not lines[0].startswith(HEADER):
        raise C.GateError(f"{manifest}: missing '{HEADER}<dir>' header")
    root_rel = lines[0][len(HEADER):].strip()
    root = posixpath.normpath(posixpath.join(os.path.dirname(manifest) or ".", root_rel))
    expected = {}
    for n, line in enumerate(lines[1:], 2):
        if not line:
            continue
        m = ENTRY.match(line)
        if not m:
            raise C.GateError(f"{manifest}:{n}: malformed entry")
        if m.group(2) in expected:
            raise C.GateError(f"{manifest}:{n}: duplicate entry")
        expected[m.group(2)] = m.group(1)
    if not expected:
        raise C.GateError(f"{manifest}: no entries")
    if not os.path.isdir(root):
        raise C.GateError(f"manifest root is not a directory: {root}")
    actual = list_files(root, manifest, globs)
    out = []
    for rel in sorted(set(expected) | set(actual)):
        if excluded(rel, globs):
            continue
        if rel not in actual:
            out.append(f"MISSING {rel}")
        elif rel not in expected:
            out.append(f"UNLISTED {rel}")
        elif sha256_file(actual[rel]) != expected[rel]:
            out.append(f"MODIFIED {rel}")
    for line in out:
        print(line)
    return C.EXIT_FAIL if out else C.EXIT_PASS


def main(argv=None):
    C.setup_stdio()
    p = argparse.ArgumentParser(description="Freeze / verify a sha256 manifest of the gate tree.")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--root", required=True)
    f.add_argument("--out", required=True)
    f.add_argument("--exclude", action="append", default=[])
    v = sub.add_parser("verify")
    v.add_argument("--manifest", required=True)
    v.add_argument("--exclude", action="append", default=[])
    args = p.parse_args(argv)
    try:
        if args.cmd == "freeze":
            return freeze(args.root, args.out, args.exclude)
        return verify(args.manifest, args.exclude)
    except C.GateError as exc:
        sys.stdout.flush()
        C.error(exc)
        return C.EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
