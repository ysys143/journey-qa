#!/usr/bin/env python3
"""redact.py: write a redacted copy of a transcript to stdout (gates/SPEC.md "capture/ tools").

Usage:
  redact.py FILE [--policy P ...]

Rules (value replaced by the policy redaction_marker, default "[REDACTED]"):
  - the whole credential of an Authorization header, any scheme (Basic,
    Token, Digest, ...; also Proxy-Authorization, JSON keys and quoted curl -H
    arguments), keeping the scheme word
  - "Bearer <token>" anywhere, case-insensitive
  - JWT (eyJ...), APIKEY shapes (sk-, sk-ant-, ghp_/gho_/ghu_/ghs_/ghr_,
    github_pat_, xox[abprs]-, AKIA...)
  - whole PRIVATE KEY blocks (BEGIN..END; an unterminated block is redacted to
    the end of the file)
  - SECRETVAR keys in both KEY=value and KEY: value form (secret_key_names, or an
    upper-case key matching secret_key_suffix_regex); placeholders are kept
  - values after "password:" / "passphrase:" prompts (also "password for x:")
  - policy redactions[] ({"regex": ..., "replace": ...}), applied last
The result must pass leakscan; the selftest "redact" gate checks that.
Emails, names and home paths are NOT redacted here: those are fixed at the
source (roster values) and caught by leakscan.
Exit: 0 written, 2 usage error / unreadable input / bad policy.
"""

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402

GATES_DIR = os.environ.get("JQA_GATES_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gates")
sys.path.insert(0, GATES_DIR)
import _common as C  # noqa: E402

PRIVATE_BLOCK = re.compile(
    r"-----BEGIN[A-Z0-9 ]*PRIVATE KEY[A-Z0-9 ]*-----.*?(?:-----END[A-Z0-9 ]*PRIVATE KEY[A-Z0-9 ]*-----|\Z)",
    re.S)
AUTH_SCHEME = re.compile(r"(?i)(?<![A-Za-z0-9\-])(bearer)([ \t]+[\"'`]?)([^\s\"'`]+)")
JWT = re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*")
APIKEY = re.compile(
    r"(?<![A-Za-z0-9])sk-(?:ant-)?[A-Za-z0-9_\-]{20,}"
    r"|(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{30,}"
    r"|github_pat_[A-Za-z0-9_]+"
    r"|xox[abprs]-[A-Za-z0-9\-]+"
    r"|(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}")
KEYVAL = re.compile(
    r"(?<![A-Za-z0-9_])([\"']?)([A-Za-z_][A-Za-z0-9_]*)([\"']?[ \t]*[=:][ \t]*)"
    r"(\"[^\"\n]*\"?|'[^'\n]*'?|\S*)")
PROMPT = re.compile(r"(?i)\b(password|passphrase)((?:[ \t]+for[ \t]+[^:\n]*)?[ \t]*:[ \t]*)(\S[^\n]*)")


def redact_text(text, policy):
    marker = policy["redaction_marker"]
    names = set(policy["secret_key_names"])
    suffix = policy["_suffix_re"]

    text = PRIVATE_BLOCK.sub(marker + " private key block", text)

    pieces, last = [], 0
    for _start, cred_start, cred_end, _scheme in C.auth_header_spans(text):
        if cred_start >= cred_end or cred_start < last:
            continue
        if C.value_is_safe(text[cred_start:cred_end].split()[0], marker):
            continue
        pieces.append(text[last:cred_start])
        pieces.append(marker)
        last = cred_end
    text = "".join(pieces) + text[last:]

    def scheme(m):
        if C.value_is_safe(m.group(3), marker):
            return m.group(0)
        return m.group(1) + m.group(2) + marker
    text = AUTH_SCHEME.sub(scheme, text)
    text = JWT.sub(marker, text)
    text = APIKEY.sub(marker, text)

    def keyval(m):
        key = m.group(2)
        is_secret = key in names or (key == key.upper() and suffix.search(key))
        value = m.group(4)
        if not is_secret or C.value_is_safe(value, marker):
            return m.group(0)
        quote = value[0] if value[:1] in ("\"", "'") else ""
        return m.group(1) + key + m.group(3) + quote + marker + quote
    text = KEYVAL.sub(keyval, text)

    def prompt(m):
        if C.value_is_safe(m.group(3), marker):
            return m.group(0)
        return m.group(1) + m.group(2) + marker
    text = PROMPT.sub(prompt, text)

    for rx, replace in policy["_redactions"]:
        try:
            text = rx.sub(replace, text)
        except (re.error, IndexError) as exc:
            raise C.GateError(f"policy redactions: replace template failed ({exc})") from None
    return text


def main(argv=None):
    C.setup_stdio()
    p = argparse.ArgumentParser(description="Print a redacted copy of FILE.")
    p.add_argument("file", metavar="FILE")
    p.add_argument("--policy", action="append", default=[])
    args = p.parse_args(argv)
    try:
        policy = C.load_policy(args.policy)
        try:
            with open(args.file, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            raise C.GateError(f"cannot read {args.file}: {exc.strerror}") from None
        if b"\x00" in data:
            raise C.GateError(f"{args.file} looks binary; redact works on text transcripts only")
        text = data.decode("utf-8", errors="replace")
        sys.stdout.write(redact_text(text, policy))
        sys.stdout.flush()
        return C.EXIT_PASS
    except C.GateError as exc:
        C.error(exc)
        return C.EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
