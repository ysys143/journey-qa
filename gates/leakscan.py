#!/usr/bin/env python3
"""leakscan.py: text leak scanner (gates/SPEC.md "leakscan.py").

Usage:
  leakscan.py --roster R --denylist D [--denylist D2 ...] [--policy P ...]
              [--allow-uuids F ...] [--skip-images] [--no-path-scan] [--list-rules]
              TARGET...

TARGET is a file or a directory (recursive, nothing excluded).
Output: one line per violation, "<RULE> <file>:<line>" (line may be "N(url)",
"N(wrap)" or "path"). The matched text is never printed; denylist hits are
printed as DENYLIST[<list file>#<line>]. Paths are printed as given, except
that a path component which itself contains a denylist item or a disallowed
email is replaced by "[masked-<sha256 prefix>]".
Exit: 0 clean, 1 violation found, 2 usage error / unreadable input.

Library use (image_scan.py): Scanner(policy, roster, denylist_items,
allow_uuids).scan_text(text) -> sorted list of (rule_label, line_tag).
"""

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import bisect  # noqa: E402
import hashlib  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import urllib.parse  # noqa: E402
import zlib  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp",
              ".bmp", ".tif", ".tiff", ".ico", ".heic", ".heif", ".avif")
COMPRESSED_MAGIC = (
    b"\x1f\x8b",                    # gzip
    b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08",  # zip
    b"BZh",                         # bzip2
    b"\xfd7zXZ\x00",                # xz
    b"\x28\xb5\x2f\xfd",            # zstd
    b"%PDF",                        # pdf
    b"7z\xbc\xaf\x27\x1c",          # 7z
    b"Rar!\x1a\x07",                # rar
    b"\x04\x22\x4d\x18",            # lz4
)
IMAGE_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")

# Most rules start with a literal so CPython's re can skip ahead quickly; the
# left/right boundary conditions are then checked in Python by _scan(), which
# retries at start+1 after a rejected candidate (same result as a leading
# look-behind). Rules whose body is an unbounded run (SECRETVAR, sk-, gh*_)
# keep the look-behind in the regex: rejecting inside the run in C keeps the
# scan linear on pathological input.

# EMAIL runs on normalized (casefolded) text.
_EC = r"a-z0-9._%+\-"
_EC_SET = frozenset("abcdefghijklmnopqrstuvwxyz0123456789._%+-")
_EC_AT_SET = _EC_SET | {"@"}
EMAIL_AT_RE = re.compile(r"@((?:[a-z0-9\-]+\.)+[a-z]{2,})")
EMAIL_FULL_RE = re.compile(r"[" + _EC + r"]+@(?:[a-z0-9\-]+\.)+[a-z]{2,}")
# run on the REVERSED normalized text: "\n" + reversed tail that contains "@"
WRAP_TAIL_AT_REV = re.compile(r"\n[^\S\n]*([" + _EC + r"]*@[" + _EC + r"@]*)")
# the next line may carry mail-style quote markers ("> ") before the fragment
WRAP_HEAD_AT = re.compile(r"\n[^\S\n]*(?:>[^\S\n]*)*@")
WRAP_HEAD = re.compile(r"[^\S\n]*(?:>[^\S\n]*)*([" + _EC + r"@]+)")

IPV4_RE = re.compile(r"([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})")
DIGIT_DOT_RUN = re.compile(r"[0-9.]*")

_U = r"([^\s/\\\"'`<>|:;,()\[\]{}*?]+)"
HOME_PLAIN = re.compile(r"/(?:Users|home)/" + _U)
HOME_JSON = re.compile(r"\\/(?:Users|home)\\/" + _U)
HOME_WIN = re.compile(r":(?:\\{1,2}|/)[Uu][Ss][Ee][Rr][Ss](?:\\{1,2}|/)" + _U)
# a token that starts with "/" (not "//", so URL paths after "scheme:" are not
# taken) and contains /Users/<u> or /home/<u> further in: /mnt/c/Users/<u>,
# /var/home/<u>, /System/Volumes/Data/Users/<u>
_TOKEN_BREAKS = r"\s\"'`()\[\]<>{}=,;|:"
HOME_PREFIXED = re.compile(
    r"(?<![^" + _TOKEN_BREAKS + r"])/(?!/)[^" + _TOKEN_BREAKS + r"]*?/(?:Users|home)/" + _U)
HOME_DASH = re.compile(r"-(?:Users|home)-([A-Za-z0-9._\-]{0,256})")

UUID_TAIL_RE = re.compile(r"-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}")
BEARER_RE = re.compile(r"earer[^\S\n]+(\S+)")
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*")
APIKEY_RES = (  # (regex, boundary checked in Python); unbounded runs keep the look-behind
    (re.compile(r"(?<![A-Za-z0-9])sk-(?:ant-)?[A-Za-z0-9_\-]{20,}"), False),
    (re.compile(r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{30,}"), False),
    (re.compile(r"github_pat_[A-Za-z0-9_]+"), False),
    (re.compile(r"xox[abprs]-[A-Za-z0-9\-]+"), False),
    (re.compile(r"AKIA[0-9A-Z]{16}"), True),
)
PRIVATE_KEY_RE = re.compile(r"-----BEGIN[A-Z0-9 ]*PRIVATE KEY[A-Z0-9 ]*-----")
# look-behind kept: without it a long identifier run makes the search quadratic
SECRETVAR_UPPER = re.compile(r"(?<![A-Za-z0-9_])([A-Z_][A-Z0-9_]*)[\"']?[ \t]*[=:][ \t]*(\S*)")
UPPER_KEY = re.compile(r"[A-Z_][A-Z0-9_]*")
PERCENT_RE = re.compile(r"%[0-9A-Fa-f]{2}")

_DIGITS = frozenset("0123456789")
_HEX = frozenset("0123456789abcdefABCDEF")
_ALNUM = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
_IDENT = _ALNUM | {"_"}
_LETTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")


def _scan(rx, text, accept, skip=None):
    """finditer with a Python-side acceptance test. A rejected candidate is
    retried from start+1 (no overlapping candidate is skipped), or from the end
    of skip.match() when every candidate inside that run is rejected too."""
    pos, n = 0, len(text)
    while pos <= n:
        m = rx.search(text, pos)
        if m is None:
            return
        if accept(text, m):
            yield m
            pos = m.end() if m.end() > m.start() else m.start() + 1
        else:
            pos = m.start() + 1
            if skip is not None:
                pos = max(pos, skip.match(text, m.start()).end())


def _prev(text, i):
    return text[i - 1] if i > 0 else ""


_PRODUCT_VERSION_PREFIX = re.compile(r"(?:^|[\s(\"'])[A-Z][A-Za-z]{2,}/$")


def _ipv4_shape_ok(text, m):
    s, e = m.start(), m.end()
    p = _prev(text, s)
    if p and p in _DIGITS:
        return False
    # A product version token such as "Chrome/154.0.0.0" (User-Agent strings) is not an address.
    if p == "/" and m.group(2) == m.group(3) == m.group(4) == "0" and _PRODUCT_VERSION_PREFIX.search(text, max(0, s - 64), s):
        return False
    pp = _prev(text, s - 1)
    if p == "." and pp and pp in _DIGITS:
        return False
    nxt = text[e] if e < len(text) else ""
    if nxt and nxt in _DIGITS:
        return False
    if nxt == "." and e + 1 < len(text) and text[e + 1] in _DIGITS:
        return False
    return True


def _not_after(chars):
    def accept(text, m):
        p = _prev(text, m.start())
        return p == "" or p not in chars
    return accept


_HOME_PLAIN_OK = _not_after(_ALNUM | set("_.-"))
_NOT_AFTER_ALNUM = _not_after(_ALNUM)


def _home_win_ok(text, m):
    s = m.start()
    drive = _prev(text, s)
    if not drive or drive not in _LETTERS:
        return False
    before = _prev(text, s - 1)
    return before == "" or before not in _ALNUM


def _home_dash_ok(text, m):
    p = _prev(text, m.start())
    return p == "" or p.isspace() or p in "/\"'=`([{<:,;|"


def _uuid_ok(text, m):
    s, e = m.start(), m.end()
    if s < 8 or not all(c in _HEX for c in text[s - 8:s]):
        return False
    p = _prev(text, s - 8)
    if p and p in _HEX:
        return False
    return not (e < len(text) and text[e] in _HEX)


def _bearer_ok(text, m):
    s = m.start()
    if _prev(text, s) not in ("B", "b"):
        return False
    p = _prev(text, s - 1)
    return p == "" or (p not in _ALNUM and p != "-")


def _newlines(text):
    return [m.start() for m in re.finditer("\n", text)]


def _url_decode(text):
    for _ in range(3):
        decoded = urllib.parse.unquote(text, errors="replace")
        if decoded == text:
            break
        text = decoded
    return text


def classify(path, data):
    """Return (kind, texts). kind: empty | image | disguised-image | compressed | text.
    texts lists every decoded interpretation to scan; their hits are merged.
    A file with NUL bytes and no BOM is read three ways (printable runs,
    UTF-16LE, UTF-16BE; plus UTF-32 when the NUL pattern says so), because
    UTF-16 text that is mostly Hangul/CJK has too few NUL bytes to detect."""
    if not data:
        return "empty", []
    if path.lower().endswith(IMAGE_EXTS):
        return "image", []
    if data.startswith(COMPRESSED_MAGIC) or _is_zlib_stream(data):
        return "compressed", []
    if data.startswith(IMAGE_MAGIC) or (data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
        return "disguised-image", []
    for bom, enc in ((b"\xff\xfe\x00\x00", "utf-32-le"), (b"\x00\x00\xfe\xff", "utf-32-be"),
                     (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
        if data.startswith(bom):
            return "text", [data[len(bom):].decode(enc, errors="replace")]
    if b"\x00" in data:
        texts = [_extract_strings(data),
                 data.decode("utf-16-le", errors="replace"),
                 data.decode("utf-16-be", errors="replace")]
        enc = _guess_wide_encoding(data)
        if enc and enc.startswith("utf-32"):
            texts.append(data.decode(enc, errors="replace"))
        return "text", texts
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return "text", [data.decode("utf-8", errors="replace")]


def _is_zlib_stream(data):
    """zlib stream (RFC 1950 header, no file magic): valid CMF/FLG check bits
    and a deflate body that inflates to some output without error. A
    truncated stream still counts (fail closed)."""
    if len(data) < 8 or data[0] & 0x0F != 8 or data[0] >> 4 > 7:
        return False
    if ((data[0] << 8) | data[1]) % 31:
        return False
    limit = 16 << 20
    inflater = zlib.decompressobj()
    try:
        out = inflater.decompress(data[:limit], 1 << 20)
    except zlib.error:
        return False
    return bool(out)


def _guess_wide_encoding(data):
    n = len(data)
    if n >= 8:
        quads = n // 4
        cols = [data[i:quads * 4:4].count(0) / quads for i in range(4)]
        if cols[0] < 0.1 and min(cols[1:]) > 0.6:
            return "utf-32-le"
        if cols[3] < 0.1 and min(cols[:3]) > 0.6:
            return "utf-32-be"
    pairs = n // 2
    if pairs < 2:
        return None
    even = data[0:pairs * 2:2].count(0) / pairs
    odd = data[1:pairs * 2:2].count(0) / pairs
    if odd > 0.4 and even < 0.1:
        return "utf-16-le"
    if even > 0.4 and odd < 0.1:
        return "utf-16-be"
    return None


_CONTROL_SPLIT = re.compile("[\x00-\x08\x0a-\x1f\x7f\ufffd]+")


def _extract_strings(data):
    """Printable runs of length >= 4, one per line. Decoded as UTF-8 so that
    non-ASCII names inside binaries are still visible (superset of ASCII runs)."""
    text = data.decode("utf-8", errors="replace")
    return "\n".join(run for run in _CONTROL_SPLIT.split(text) if len(run) >= 4)


class Scanner:
    def __init__(self, policy, roster, deny_items, allow_uuids=()):
        self.policy = policy
        self.marker = policy["redaction_marker"]
        self.deny = deny_items
        self.deny_order = {label: i for i, (label, _) in enumerate(deny_items)}
        self.rule_ids = policy["_rule_ids"]
        self.rule_idx = {r: i for i, r in enumerate(self.rule_ids)}
        self.home_users = set(roster.handles) | set(policy["allowed_home_users"])
        self.home_users_dash = {re.sub(r"[^A-Za-z0-9\-]", "-", u) for u in self.home_users}
        self.uuids = set(roster.account_ids_lower) | set(allow_uuids)
        self.email_domains = policy["_email_domains"]
        self.email_tlds = policy["_email_tlds"]
        self.ipv4_ok = {int(ip) for ip in policy["_ipv4"]}
        self.ipv4_nets = [(int(n.network_address), int(n.netmask)) for n in policy["_ipv4_nets"]]
        self.secret_names = set(policy["secret_key_names"])
        # names that are not upper-case identifiers are not seen by SECRETVAR_UPPER
        odd = sorted((n for n in self.secret_names if n and not UPPER_KEY.fullmatch(n)),
                     key=len, reverse=True)
        self.secret_names_re = None
        if odd:
            self.secret_names_re = re.compile(
                r"(?<![A-Za-z0-9_])[\"']?(" + "|".join(re.escape(n) for n in odd)
                + r")[\"']?[ \t]*[=:][ \t]*(\S*)")
        self.suffix_re = policy["_suffix_re"]
        self.extra = policy["_extra"]
        self.exempt = {}
        for rule, rx in policy["_exemptions"]:
            self.exempt.setdefault(rule, []).append(rx)

    # ---------------------------------------------------------- judgements
    def _exempted(self, rule, match_text):
        return any(rx.fullmatch(match_text) for rx in self.exempt.get(rule, ()))

    def _domain_bad(self, domain):
        for allowed in self.email_domains:
            if domain == allowed or domain.endswith("." + allowed):
                return False
        return domain.rsplit(".", 1)[-1] not in self.email_tlds

    def _email_hits(self, s):
        """Yield (position of '@', email) for disallowed emails in normalized s."""
        for m in EMAIL_AT_RE.finditer(s):
            at = m.start()
            if at == 0 or s[at - 1] not in _EC_SET or not self._domain_bad(m.group(1)):
                continue
            start, floor = at - 1, max(0, at - 256)
            while start > floor and s[start - 1] in _EC_SET:
                start -= 1
            email = s[start:m.end()]
            if not self._exempted("EMAIL", email):
                yield at, email

    def _bad_emails(self, s):
        return [email for _, email in self._email_hits(s)]

    def _deny_hits(self, normalized):
        return [label for label, item in self.deny
                if item in normalized and not self._exempted("DENYLIST", item)]

    def _ipv4_hits(self, text):
        for m in _scan(IPV4_RE, text, _ipv4_shape_ok, DIGIT_DOT_RUN):
            a, b, c, d = (int(g) for g in m.groups())
            if a > 255 or b > 255 or c > 255 or d > 255:
                continue
            value = (a << 24) | (b << 16) | (c << 8) | d
            if value in self.ipv4_ok or any(value & mask == net for net, mask in self.ipv4_nets):
                continue
            if not self._exempted("IPV4", f"{a}.{b}.{c}.{d}"):
                yield m.start()

    def _home_hits(self, text):
        """Yield (start, match_text) for disallowed home paths."""
        for rx, accept, back in ((HOME_PLAIN, _HOME_PLAIN_OK, 0),
                                 (HOME_PREFIXED, None, 0),
                                 (HOME_JSON, None, 0),
                                 (HOME_WIN, _home_win_ok, 1)):
            matches = rx.finditer(text) if accept is None else _scan(rx, text, accept)
            for m in matches:
                user = m.group(1).rstrip(".")
                start = m.start() - back
                if user not in self.home_users and not self._exempted("HOMEPATH", text[start:m.end()]):
                    yield start, text[start:m.end()]
        for m in _scan(HOME_DASH, text, _home_dash_ok):
            rest = m.group(1)
            if rest == "":
                continue
            if any(rest == u or rest.startswith(u + "-") for u in self.home_users_dash):
                continue
            if not self._exempted("HOMEPATH", m.group(0)):
                yield m.start(), m.group(0)

    def _secretvar_hits(self, text):
        seen = set()
        for m in SECRETVAR_UPPER.finditer(text):
            key = m.group(1)
            if key in self.secret_names or self.suffix_re.search(key):
                seen.add(m.start())
                if not C.value_is_safe(m.group(2), self.marker) \
                        and not self._exempted("SECRETVAR", m.group(0)):
                    yield m.start()
        if self.secret_names_re is not None:
            for m in self.secret_names_re.finditer(text):
                if m.start(1) in seen:
                    continue
                if not C.value_is_safe(m.group(2), self.marker) \
                        and not self._exempted("SECRETVAR", m.group(0)):
                    yield m.start()

    @staticmethod
    def _wrap_candidates(nt):
        """Yield (newline position, tail start, tail) for line ends whose last
        email-ish run contains '@', or whose next line starts with '@'."""
        seen = set()
        rev = nt[::-1]
        n = len(nt)
        for m in WRAP_TAIL_AT_REV.finditer(rev):
            nl_pos = n - 1 - m.start()
            tail = m.group(1)[::-1]
            if nl_pos not in seen:
                seen.add(nl_pos)
                yield nl_pos, n - m.end(1), tail
        for m in WRAP_HEAD_AT.finditer(nt):
            nl_pos = m.start()
            if nl_pos in seen:
                continue
            end = nl_pos
            while end > 0 and nt[end - 1] != "\n" and nt[end - 1].isspace():
                end -= 1
            start, floor = end, max(0, end - 256)
            while start > floor and nt[start - 1] in _EC_AT_SET:
                start -= 1
            yield nl_pos, start, nt[start:end]

    # -------------------------------------------------------------- scans
    def scan_text(self, text):
        """Return sorted [(rule_label, line_tag)] for a decoded text, or for a
        list of decoded interpretations of one file (hits merged, deduplicated;
        line numbers refer to the interpretation that produced the hit)."""
        texts = [text] if isinstance(text, str) else list(text)
        hits = set()
        for one in texts:
            hits |= self._scan_keys(one)
        tags = ("", "(url)", "(wrap)")
        return [(label, f"{ln}{tags[variant]}") for ln, variant, _, _, label in sorted(hits)]

    def _scan_keys(self, text):
        hits = set()
        nl = _newlines(text)

        def line_of(pos, offsets):
            return bisect.bisect_left(offsets, pos) + 1

        def add(pos, offsets, variant, rule, label=None, sub=0):
            hits.add((line_of(pos, offsets), variant, self.rule_idx[rule], sub, label or rule))

        # DENYLIST and EMAIL on normalized text (same line structure).
        nt = C.normalize_for_match(text)
        if nt.count("\n") != len(nl):  # normalization must never change line count
            raise C.GateError("internal: normalization changed line structure")
        nnl = _newlines(nt)
        for idx, (label, item) in enumerate(self.deny):
            if self._exempted("DENYLIST", item):
                continue
            start = nt.find(item)
            while start >= 0:
                add(start, nnl, 0, "DENYLIST", label, idx)
                start = nt.find(item, start + 1)
        for at, _ in self._email_hits(nt):
            add(at, nnl, 0, "EMAIL")

        # wrap pass: an email split across two lines
        for nl_pos, start, tail in self._wrap_candidates(nt):
            hm = WRAP_HEAD.match(nt, nl_pos + 1)
            if not tail or hm is None:
                continue
            head = hm.group(1)
            if not (("@" in tail and not EMAIL_FULL_RE.fullmatch(tail)) or head.startswith("@")):
                continue
            joined = tail + head
            if self._bad_emails(joined):
                add(start, nnl, 2, "EMAIL")
            for idx, (label, item) in enumerate(self.deny):
                if item in joined and not self._exempted("DENYLIST", item):
                    add(start, nnl, 2, "DENYLIST", label, idx)

        for pos in self._ipv4_hits(text):
            add(pos, nl, 0, "IPV4")
        for pos, _ in self._home_hits(text):
            add(pos, nl, 0, "HOMEPATH")
        for m in _scan(UUID_TAIL_RE, text, _uuid_ok):
            value = text[m.start() - 8:m.end()]
            if value.lower() not in self.uuids and not self._exempted("UUID", value):
                add(m.start() - 8, nl, 0, "UUID")
        for m in _scan(BEARER_RE, text, _bearer_ok):
            if not C.value_is_safe(m.group(1), self.marker) \
                    and not self._exempted("BEARER", text[m.start() - 1:m.end()]):
                add(m.start() - 1, nl, 0, "BEARER")
        for start, cred_start, cred_end, scheme in C.auth_header_spans(text):
            # scheme Bearer/bearer: judged by the BEARER rule (and its exemptions)
            if scheme in ("Bearer", "bearer") or cred_start >= cred_end:
                continue
            first = text[cred_start:cred_end].split()[0]
            if not C.value_is_safe(first, self.marker) \
                    and not self._exempted("AUTH_HEADER", text[start:cred_end]):
                add(start, nl, 0, "AUTH_HEADER")
        for m in JWT_RE.finditer(text):
            if not self._exempted("JWT", m.group(0)):
                add(m.start(), nl, 0, "JWT")
        for rx, needs_boundary in APIKEY_RES:
            matches = _scan(rx, text, _NOT_AFTER_ALNUM) if needs_boundary else rx.finditer(text)
            for m in matches:
                if not self._exempted("APIKEY", m.group(0)):
                    add(m.start(), nl, 0, "APIKEY")
        for m in PRIVATE_KEY_RE.finditer(text):
            if not self._exempted("PRIVATE_KEY", m.group(0)):
                add(m.start(), nl, 0, "PRIVATE_KEY")
        for pos in self._secretvar_hits(text):
            add(pos, nl, 0, "SECRETVAR")
        for rid, rx, has_value in self.extra:
            for m in rx.finditer(text):
                if m.end() == m.start():
                    continue
                if has_value and m.group("value") is not None \
                        and C.value_is_safe(m.group("value"), self.marker):
                    continue
                if not self._exempted(rid, m.group(0)):
                    add(m.start(), nl, 0, rid)

        # url pass: percent-decoded copy of each line that holds an escape
        url_lines = sorted({line_of(m.start(), nl) for m in PERCENT_RE.finditer(text)})
        for ln in url_lines:
            start = nl[ln - 2] + 1 if ln > 1 else 0
            end = nl[ln - 1] if ln - 1 < len(nl) else len(text)
            line = text[start:end]
            decoded = _url_decode(line)
            if decoded == line:
                continue
            if decoded.count("@") > line.count("@"):
                n_line, n_dec = C.normalize_for_match(line), C.normalize_for_match(decoded)
                if set(self._bad_emails(n_dec)) - set(self._bad_emails(n_line)):
                    hits.add((ln, 1, self.rule_idx["EMAIL"], 0, "EMAIL"))
            if any(decoded.count(ch) > line.count(ch) for ch in "/\\-"):
                if {t for _, t in self._home_hits(decoded)} - {t for _, t in self._home_hits(line)}:
                    hits.add((ln, 1, self.rule_idx["HOMEPATH"], 0, "HOMEPATH"))
            decoded_deny = set(self._deny_hits(C.normalize_for_match(decoded)))
            if decoded_deny:
                plain_deny = set(self._deny_hits(C.normalize_for_match(line)))
                for label in decoded_deny - plain_deny:
                    hits.add((ln, 1, self.rule_idx["DENYLIST"], self.deny_order[label], label))

        return hits

    def path_hits(self, path):
        """DENYLIST and EMAIL applied to the path string (and its url-decoded copy)."""
        labels = set()
        for variant in (path, _url_decode(path)):
            norm = C.normalize_for_match(variant)
            for label in self._deny_hits(norm):
                labels.add((self.rule_idx["DENYLIST"], self.deny_order[label], label))
            if self._bad_emails(norm):
                labels.add((self.rule_idx["EMAIL"], 0, "EMAIL"))
        return [label for _, _, label in sorted(labels)]

    def display_path(self, path):
        """Path as given, with any component that itself leaks masked."""
        if not self.path_hits(path):
            return path
        parts = path.split("/")
        masked = False
        for i, part in enumerate(parts):
            if part and self.path_hits(part):
                digest = hashlib.sha256(part.encode("utf-8", "surrogateescape")).hexdigest()[:12]
                parts[i] = f"[masked-{digest}]"
                masked = True
        if not masked:
            digest = hashlib.sha256(path.encode("utf-8", "surrogateescape")).hexdigest()[:12]
            return f"[masked-{digest}]"
        return "/".join(parts)

    def scan_file(self, path, skip_images=False, path_scan=True):
        """Return output lines for one file."""
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            raise C.GateError(f"cannot read {self.display_path(path)}: {exc.strerror}") from None
        shown = self.display_path(path)
        out = []
        if path_scan:
            out.extend(f"{label} {shown}:path" for label in self.path_hits(path))
        kind, texts = classify(path, data)
        if kind == "image":
            if not skip_images and not self._exempted("UNSCANNED_IMAGE", path):
                out.append(f"UNSCANNED_IMAGE {shown}")
        elif kind == "disguised-image":
            if not self._exempted("UNSCANNED_IMAGE", path):
                out.append(f"UNSCANNED_IMAGE {shown}")
        elif kind == "compressed":
            if not self._exempted("UNSCANNABLE_COMPRESSED", path):
                out.append(f"UNSCANNABLE_COMPRESSED {shown}")
        elif kind == "text":
            out.extend(f"{label} {shown}:{tag}" for label, tag in self.scan_text(texts))
        return out


def discover(targets, display=lambda path: path):
    """Files in deterministic order: targets in given order, directories walked
    with sorted entries. Paths are joined onto the target string as given.
    Error messages show paths through display() (Scanner.display_path masks
    components that leak)."""
    def walk_error(exc):
        # os.walk skips unreadable directories silently by default: fail closed
        raise C.GateError(f"cannot list directory {display(exc.filename or '?')}") from None

    files = []
    for target in targets:
        if os.path.isdir(target):
            seen_dirs = set()
            for root, dirs, names in os.walk(target, followlinks=True, onerror=walk_error):
                try:
                    st = os.stat(root)
                except OSError:
                    raise C.GateError(f"cannot stat directory {display(root)}") from None
                key = (st.st_dev, st.st_ino)
                if key in seen_dirs:
                    dirs[:] = []
                    continue
                seen_dirs.add(key)
                dirs.sort()
                for name in sorted(names):
                    full = os.path.join(root, name)
                    if not os.path.isfile(full):
                        raise C.GateError(f"not a regular file (broken link, fifo, ...): {display(full)}")
                    files.append(full)
        elif os.path.isfile(target):
            files.append(target)
        else:
            raise C.GateError(f"target not found or not a regular file: {display(target)}")
    return files


def build_parser():
    p = argparse.ArgumentParser(description="Text leak scanner (gates/SPEC.md).")
    p.add_argument("--roster")
    p.add_argument("--denylist", action="append", default=[])
    p.add_argument("--policy", action="append", default=[])
    p.add_argument("--allow-uuids", action="append", default=[])
    p.add_argument("--skip-images", action="store_true")
    p.add_argument("--no-path-scan", action="store_true")
    p.add_argument("--list-rules", action="store_true")
    p.add_argument("targets", nargs="*")
    return p


def load_scanner(args):
    policy = C.load_policy(args.policy)
    if not args.roster:
        raise C.GateError("--roster is required")
    if not args.denylist:
        raise C.GateError("at least one --denylist is required")
    roster = C.load_roster(args.roster)
    deny = C.load_denylists(args.denylist)
    allow = C.load_allow_uuids(args.allow_uuids)
    return Scanner(policy, roster, deny, allow)


def main(argv=None):
    C.setup_stdio()
    args = build_parser().parse_args(argv)
    try:
        if args.list_rules:
            for rule in C.load_policy(args.policy)["_rule_ids"]:
                print(rule)
            return C.EXIT_PASS
        scanner = load_scanner(args)
        if not args.targets:
            raise C.GateError("no TARGET given")
        found = False
        for path in discover(args.targets, scanner.display_path):
            lines = scanner.scan_file(path, args.skip_images, not args.no_path_scan)
            for line in lines:
                print(line)
            found = found or bool(lines)
        sys.stdout.flush()
        return C.EXIT_FAIL if found else C.EXIT_PASS
    except C.GateError as exc:
        sys.stdout.flush()
        C.error(exc)
        return C.EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
