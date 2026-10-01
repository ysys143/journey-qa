#!/usr/bin/env python3
"""adapter_dir.py: the one adapter resolution rule (adapters/README.md "Where an adapter lives").

Usage:
  adapter_dir.py [--product NAME] [--adapter DIR] [--project DIR] [--why]

An adapter is a directory path. Search order, first hit wins:
  1. explicit --adapter DIR, else the JQA_ADAPTER_DIR environment variable
  2. <project>/.journey-qa/adapters/<product>/   (--project, default: current directory)
  3. the bundled adapters/<product>/ next to this repository's gates/ (examples)

An explicit adapter that is not a directory is an error; the search never falls through
to a lower source after an explicit choice. Prints the absolute adapter path on stdout and,
with --why, the source on stderr.
Exit: 0 resolved, 2 usage error or not found (2 is never a pass).
"""

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLED = os.path.join(os.path.dirname(HERE), "adapters")
PRODUCT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def resolve(product=None, adapter=None, project=None, env=None, bundled=BUNDLED):
    """Return (path, source). Raises ValueError with a message when nothing usable."""
    env = os.environ if env is None else env
    explicit = adapter or env.get("JQA_ADAPTER_DIR") or ""
    if explicit:
        src = "--adapter" if adapter else "JQA_ADAPTER_DIR"
        if not os.path.isdir(explicit):
            raise ValueError(f"{src} is not a directory: {explicit}")
        return os.path.abspath(explicit), src
    if not product:
        raise ValueError("need --product NAME, --adapter DIR or JQA_ADAPTER_DIR")
    if not PRODUCT_RE.match(product) or product in (".", ".."):
        raise ValueError(f"invalid product name: {product}")
    cands = [("project", os.path.join(project or os.getcwd(), ".journey-qa", "adapters", product)),
             ("bundled", os.path.join(bundled, product))]
    for src, path in cands:
        if os.path.isdir(path):
            return os.path.abspath(path), src
    raise ValueError("adapter not found for product %s; searched: %s"
                     % (product, ", ".join(p for _, p in cands)))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--product")
    p.add_argument("--adapter")
    p.add_argument("--project")
    p.add_argument("--why", action="store_true")
    try:
        args = p.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        path, src = resolve(args.product, args.adapter, args.project)
    except ValueError as exc:
        print(f"adapter_dir: {exc}", file=sys.stderr)
        return 2
    if args.why:
        print(f"adapter_dir: {src}", file=sys.stderr)
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
