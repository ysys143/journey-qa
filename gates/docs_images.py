#!/usr/bin/env python3
"""docs_images.py: docs <-> image correspondence gate (gates/SPEC.md "docs_images.py").

Usage:
  docs_images.py --pages-root DIR [--page FILE ...] --images-dir DIR
                 [--locale-suffix .<lang> ...]

Pages: every *.md under --pages-root plus each --page file.
References: markdown images ![..](path "title"), <img src="">, <img srcset="">,
<source srcset=""> (each comma-separated candidate). http(s):, data: and #
references are skipped and only noted as "EXTERNAL_REF <page>:<line>" (the
reference itself is not printed). "?..." and "#..." suffixes are dropped,
percent-escapes are decoded. A reference starting with "/" is resolved against
--pages-root. Paths are resolved lexically (. and ..), never made absolute;
existence is checked with exact case.

Violations:
  BROKEN_REF <page> -> <ref>          referenced file does not exist
  ORPHAN_IMAGE <png>                  PNG under --images-dir referenced by no page
  LOCALE_MISMATCH <page> <-> <twin>   x.md and x<suffix>.md reference different
                                      sets; followed by "  diff: <path>" lines
Pass every path in the same form (all relative or all absolute).
Exit: 0 clean, 1 violation, 2 usage error / missing directory / no pages.
"""

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import os  # noqa: E402
import posixpath  # noqa: E402
import re  # noqa: E402
import urllib.parse  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402

MD_IMAGE = re.compile(r"!\[[^\]]*\]\(\s*(<[^>]*>|[^)\s]+)(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*\)")
TAG = re.compile(r"<(img|source)\b([^>]*)>", re.I | re.S)
ATTR = re.compile(r"\b(src|srcset)\s*=\s*(\"([^\"]*)\"|'([^']*)'|([^\s>]+))", re.I)
EXTERNAL = re.compile(r"^(?:https?:|data:|#|//)", re.I)


def norm(path):
    path = path.replace("\\", "/")
    return posixpath.normpath(path) if path else path


def exists_exact_case(path, cache):
    """True when path exists as a regular file with exactly this spelling."""
    if not os.path.isfile(path):
        return False
    parts = norm(path).split("/")
    cur = "/" if path.startswith("/") else "."
    for part in parts:
        if part in ("", "."):
            continue
        if part == "..":
            cur = posixpath.join(cur, part)
            continue
        if cur not in cache:
            try:
                cache[cur] = set(os.listdir(cur))
            except OSError:
                return False
        if part not in cache[cur]:
            return False
        cur = posixpath.join(cur, part)
    return True


def extract_refs(text):
    """Yield (line, raw_ref) in document order."""
    found = []
    for m in MD_IMAGE.finditer(text):
        ref = m.group(1)
        if ref.startswith("<") and ref.endswith(">"):
            ref = ref[1:-1]
        found.append((m.start(), ref))
    for t in TAG.finditer(text):
        for a in ATTR.finditer(t.group(2)):
            value = next(v for v in a.group(3, 4, 5) if v is not None)
            if a.group(1).lower() == "srcset":
                for cand in value.split(","):
                    cand = cand.strip()
                    if cand:
                        found.append((t.start() + a.start(), cand.split()[0]))
            else:
                found.append((t.start() + a.start(), value.strip()))
    found.sort(key=lambda x: x[0])
    return [(text.count("\n", 0, pos) + 1, ref) for pos, ref in found]


def resolve(page, ref, pages_root):
    ref = ref.split("#", 1)[0].split("?", 1)[0]
    ref = urllib.parse.unquote(ref)
    if ref.startswith("/"):
        return norm(posixpath.join(norm(pages_root), ref.lstrip("/")))
    return norm(posixpath.join(posixpath.dirname(norm(page)), ref))


def collect_pages(pages_root, extra_pages):
    pages = []
    for root, dirs, names in os.walk(pages_root):
        dirs.sort()
        for name in sorted(names):
            if name.endswith(".md"):
                pages.append(os.path.join(root, name))
    for page in extra_pages:
        if not os.path.isfile(page):
            raise C.GateError(f"page not found: {page}")
        if page not in pages:
            pages.append(page)
    return pages


def main(argv=None):
    C.setup_stdio()
    p = argparse.ArgumentParser(description="Docs <-> image correspondence gate.")
    p.add_argument("--pages-root", required=True)
    p.add_argument("--page", action="append", default=[])
    p.add_argument("--images-dir", required=True)
    p.add_argument("--locale-suffix", action="append", default=[])
    args = p.parse_args(argv)
    try:
        if not os.path.isdir(args.pages_root):
            raise C.GateError(f"--pages-root is not a directory: {args.pages_root}")
        if not os.path.isdir(args.images_dir):
            raise C.GateError(f"--images-dir is not a directory: {args.images_dir}")
        pages = collect_pages(args.pages_root, args.page)
        if not pages:
            raise C.GateError("no pages found")
        cache = {}
        out = []
        page_refs = {}
        referenced = set()
        for page in pages:
            text = C.read_text_file(page, "page")
            refs = set()
            for line, ref in extract_refs(text):
                if EXTERNAL.match(ref):
                    print(f"EXTERNAL_REF {page}:{line}")
                    continue
                target = resolve(page, ref, args.pages_root)
                refs.add(target)
                referenced.add(target)
                if not exists_exact_case(target, cache):
                    out.append(f"BROKEN_REF {page} -> {ref}")
            page_refs[norm(page)] = (page, refs)

        images = []
        for root, dirs, names in os.walk(args.images_dir):
            dirs.sort()
            for name in sorted(names):
                if name.lower().endswith(".png"):
                    images.append(os.path.join(root, name))
        for img in images:
            if norm(img) not in referenced:
                out.append(f"ORPHAN_IMAGE {img}")

        for key in sorted(page_refs):
            page, refs = page_refs[key]
            if not key.endswith(".md"):
                continue
            stem = key[:-3]
            if any(stem.endswith(sfx) for sfx in args.locale_suffix):
                continue
            for sfx in args.locale_suffix:
                twin_key = stem + sfx + ".md"
                if twin_key not in page_refs:
                    continue
                twin, twin_refs = page_refs[twin_key]
                diff = sorted(refs ^ twin_refs)
                if diff:
                    out.append(f"LOCALE_MISMATCH {page} <-> {twin}")
                    out.extend(f"  diff: {d}" for d in diff)
        for line in out:
            print(line)
        return C.EXIT_FAIL if out else C.EXIT_PASS
    except C.GateError as exc:
        sys.stdout.flush()
        C.error(exc)
        return C.EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
