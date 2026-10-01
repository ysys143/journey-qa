"""Shared helpers for the journey-qa gates (not a CLI).

Imported by the gate scripts in this directory and by capture/ tools.

- exit-code constants and GateError (always exit 2)
- policy loading: gates/policy.default.json first, then each --policy file in
  order; list values are concatenated, scalar values are replaced (last wins).
  Load fails (GateError -> exit 2) on unknown keys, bad types, an extra_rules id
  outside ^[A-Z][A-Z0-9_]*$, a regex that does not compile, or an exemption
  without >=1 must_pass and >=1 must_fail control.
- roster, denylist and --allow-uuids loading
- value judgement: redaction marker / placeholder
- text normalization used for DENYLIST matching
"""

import ipaddress
import json
import os
import re
import sys
import unicodedata

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

GATES_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_POLICY_PATH = os.path.join(GATES_DIR, "policy.default.json")

CORE_RULES = (
    "DENYLIST", "EMAIL", "IPV4", "HOMEPATH", "UUID", "BEARER", "AUTH_HEADER",
    "JWT", "APIKEY", "PRIVATE_KEY", "SECRETVAR",
)
FILE_RULES = ("UNSCANNED_IMAGE", "UNSCANNABLE_COMPRESSED")
RULE_ID_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
UUID_RE_FULL = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

POLICY_LIST_KEYS = (
    "allowed_email_domains", "allowed_email_tlds", "allowed_ipv4",
    "allowed_ipv4_cidrs", "allowed_home_users", "secret_key_names",
    "extra_rules", "exemptions", "redactions",
)
POLICY_SCALAR_KEYS = ("secret_key_suffix_regex", "redaction_marker")
_STRING_LIST_KEYS = POLICY_LIST_KEYS[:6]


class GateError(Exception):
    """Usage error, unreadable input or missing tool. Always exit code 2."""


def setup_stdio():
    """Never crash on odd characters in paths; never emit surrogates raw."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass


def error(msg):
    print(f"ERROR {msg}", file=sys.stderr)


# ---------------------------------------------------------------- loading

def read_text_file(path, what):
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise GateError(f"cannot read {what} {path}: {exc.strerror}") from None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise GateError(f"{what} {path} is not valid UTF-8") from None


def load_json(path, what):
    text = read_text_file(path, what)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise GateError(f"{what} {path} is not valid JSON (line {exc.lineno})") from None


def _compile(regex, where):
    if not isinstance(regex, str) or regex == "":
        raise GateError(f"{where}: regex must be a non-empty string")
    try:
        return re.compile(regex)
    except re.error as exc:
        raise GateError(f"{where}: regex does not compile ({exc})") from None


def _merge_policy(base, layer, path):
    if not isinstance(layer, dict):
        raise GateError(f"policy {path} must be a JSON object")
    for key, value in layer.items():
        if key.startswith("_"):
            continue  # comment keys such as "_note"
        if key in POLICY_LIST_KEYS:
            if not isinstance(value, list):
                raise GateError(f"policy {path}: {key} must be a list")
            merged = base.get(key, []) + value
            if key in _STRING_LIST_KEYS:
                for item in value:
                    if not isinstance(item, str):
                        raise GateError(f"policy {path}: {key} must hold strings")
                seen, deduped = set(), []
                for item in merged:
                    if item not in seen:
                        seen.add(item)
                        deduped.append(item)
                merged = deduped
            base[key] = merged
        elif key in POLICY_SCALAR_KEYS:
            if not isinstance(value, str):
                raise GateError(f"policy {path}: {key} must be a string")
            base[key] = value
        else:
            raise GateError(f"policy {path}: unknown key {key!r}")


def load_policy(paths=None):
    """Load default policy plus overlays. Returns a validated, compiled dict."""
    policy = {}
    for path in [DEFAULT_POLICY_PATH] + list(paths or []):
        _merge_policy(policy, load_json(path, "policy"), path)
    for key in POLICY_LIST_KEYS:
        policy.setdefault(key, [])
    for key in POLICY_SCALAR_KEYS:
        if key not in policy:
            raise GateError(f"policy: missing {key}")

    marker = policy["redaction_marker"]
    if marker.strip() == "":
        raise GateError("policy: redaction_marker must not be empty")
    policy["_suffix_re"] = _compile(policy["secret_key_suffix_regex"],
                                    "policy secret_key_suffix_regex")
    policy["_email_domains"] = [d.lower().strip(".") for d in policy["allowed_email_domains"]]
    policy["_email_tlds"] = {t.lower().strip(".") for t in policy["allowed_email_tlds"]}
    ips = set()
    for ip in policy["allowed_ipv4"]:
        try:
            ips.add(ipaddress.IPv4Address(ip))
        except ValueError:
            raise GateError(f"policy allowed_ipv4: invalid address {ip!r}") from None
    policy["_ipv4"] = ips
    nets = []
    for cidr in policy["allowed_ipv4_cidrs"]:
        try:
            nets.append(ipaddress.IPv4Network(cidr, strict=True))
        except ValueError:
            raise GateError(f"policy allowed_ipv4_cidrs: invalid network {cidr!r}") from None
    policy["_ipv4_nets"] = nets

    extra, extra_ids = [], set()
    for i, rule in enumerate(policy["extra_rules"]):
        where = f"policy extra_rules[{i}]"
        if not isinstance(rule, dict):
            raise GateError(f"{where} must be an object")
        rid = rule.get("id")
        if not isinstance(rid, str) or not RULE_ID_RE.match(rid):
            raise GateError(f"{where}: id must match ^[A-Z][A-Z0-9_]*$")
        if rid in CORE_RULES or rid in FILE_RULES or rid in extra_ids:
            raise GateError(f"{where}: duplicate rule id {rid}")
        extra_ids.add(rid)
        rx = _compile(rule.get("regex"), f"{where} ({rid})")
        extra.append((rid, rx, "value" in rx.groupindex))
    policy["_extra"] = extra

    rule_ids = set(CORE_RULES) | set(FILE_RULES) | extra_ids
    exemptions, ex_ids = [], set()
    for i, ex in enumerate(policy["exemptions"]):
        where = f"policy exemptions[{i}]"
        if not isinstance(ex, dict):
            raise GateError(f"{where} must be an object")
        for field in ("id", "rule", "match_regex", "reason", "approved"):
            if not isinstance(ex.get(field), str) or not ex[field].strip():
                raise GateError(f"{where}: missing {field}")
        if ex["id"] in ex_ids:
            raise GateError(f"{where}: duplicate exemption id {ex['id']}")
        ex_ids.add(ex["id"])
        if ex["rule"] not in rule_ids:
            raise GateError(f"{where}: unknown rule {ex['rule']}")
        controls = ex.get("controls")
        if not isinstance(controls, dict):
            raise GateError(f"{where}: controls must list must_pass and must_fail")
        for kind in ("must_pass", "must_fail"):
            names = controls.get(kind)
            if (not isinstance(names, list) or len(names) < 1
                    or not all(isinstance(n, str) and n.strip() for n in names)):
                raise GateError(f"{where}: controls.{kind} needs at least one control")
        rx = _compile(ex["match_regex"], f"{where} ({ex['id']})")
        exemptions.append((ex["rule"], rx))
    policy["_exemptions"] = exemptions

    redactions = []
    for i, red in enumerate(policy["redactions"]):
        where = f"policy redactions[{i}]"
        if not isinstance(red, dict) or not isinstance(red.get("replace"), str):
            raise GateError(f"{where} must be {{\"regex\": ..., \"replace\": ...}}")
        redactions.append((_compile(red.get("regex"), where), red["replace"]))
    policy["_redactions"] = redactions
    policy["_rule_ids"] = list(CORE_RULES) + [r[0] for r in extra] + list(FILE_RULES)
    return policy


class Roster:
    def __init__(self, people, privileged_roles=("admin",)):
        self.people = people
        self.privileged_roles = list(privileged_roles)
        self.handles = []
        self.emails = []
        self.login_emails = []
        self.account_ids = []
        self.provider_accounts = []
        self.privileged_handles = []
        for person in people:
            self.handles.append(person["handle"])
            self.emails.append(person["email"])
            if person["role"] in self.privileged_roles:
                self.privileged_handles.append(person["handle"])
            for acc in person.get("accounts", []):
                self.account_ids.append(acc["account_id"])
                self.provider_accounts.append(f"{acc['provider']}|{acc['account_id']}")
                if acc.get("login_email"):
                    self.login_emails.append(acc["login_email"])
        self.account_ids_lower = {a.lower() for a in self.account_ids}


def load_roster(path):
    data = load_json(path, "roster")
    people = data.get("people") if isinstance(data, dict) else None
    if not isinstance(people, list) or not people:
        raise GateError(f"roster {path}: people must be a non-empty list")
    for i, person in enumerate(people):
        where = f"roster {path}: people[{i}]"
        if not isinstance(person, dict):
            raise GateError(f"{where} must be an object")
        for field in ("id", "name", "email", "handle", "role"):
            if not isinstance(person.get(field), str) or not person[field]:
                raise GateError(f"{where}: missing {field}")
        accounts = person.get("accounts", [])
        if not isinstance(accounts, list):
            raise GateError(f"{where}: accounts must be a list")
        for j, acc in enumerate(accounts):
            if (not isinstance(acc, dict) or not isinstance(acc.get("provider"), str)
                    or not isinstance(acc.get("account_id"), str)):
                raise GateError(f"{where}.accounts[{j}]: provider and account_id required")
            if not UUID_RE_FULL.match(acc["account_id"]):
                raise GateError(f"{where}.accounts[{j}]: account_id is not a UUID")
            if "login_email" in acc and not isinstance(acc["login_email"], str):
                raise GateError(f"{where}.accounts[{j}]: login_email must be a string")
    privileged = data.get("privileged_roles", ["admin"])
    if (not isinstance(privileged, list) or not privileged
            or not all(isinstance(r, str) and r for r in privileged)):
        raise GateError(f"roster {path}: privileged_roles must be a non-empty list of strings")
    return Roster(people, privileged)


def load_denylists(paths):
    """Return [(label, normalized_item)]. Label is DENYLIST[<basename>#<line>]."""
    items = []
    for path in paths:
        name = os.path.basename(path)
        text = read_text_file(path, "denylist")
        for lineno, line in enumerate(text.split("\n"), 1):
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            item = normalize_for_match(raw).strip()
            if len(item) < 4:
                continue
            items.append((f"DENYLIST[{name}#{lineno}]", item))
    if not items:
        raise GateError("denylists contain no usable item (>= 4 characters); refusing to run")
    return items


def load_allow_uuids(paths):
    allowed = set()
    for path in paths or []:
        text = read_text_file(path, "allow-uuids file")
        for lineno, line in enumerate(text.split("\n"), 1):
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            if not UUID_RE_FULL.match(raw):
                raise GateError(f"allow-uuids file {path}:{lineno} is not a UUID")
            allowed.add(raw.lower())
    return allowed


# ---------------------------------------------------------- normalization

# Unicode Default_Ignorable_Code_Point (DerivedCoreProperties.txt): invisible
# characters that can be slipped into a name without changing how it looks.
_DEFAULT_IGNORABLE = re.compile(
    "[\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b-\u180f\u200b-\u200f"
    "\u202a-\u202e\u2060-\u206f\u3164\ufe00-\ufe0f\ufeff\uffa0\ufff0-\ufff8"
    "\U0001bca0-\U0001bca3\U0001d173-\U0001d17a\U000e0000-\U000e0fff]")
_WS_RUN = re.compile(r"[^\S\n]+")


def normalize_for_match(text):
    """NFKC + casefold, drop default-ignorable characters (zero-width space,
    combining grapheme joiner, variation selectors, ...), collapse whitespace runs
    (space, tab, NBSP, U+3000, ...) to one space. Newlines are kept so line
    numbers stay aligned with the original text."""
    text = unicodedata.normalize("NFKC", text)
    text = unicodedata.normalize("NFKC", text.casefold())
    text = _DEFAULT_IGNORABLE.sub("", text)
    return _WS_RUN.sub(" ", text)


# -------------------------------------------------------- value judgement

_ANGLE_PLACEHOLDER = re.compile(r"^<[A-Za-z0-9 _.\-]+>$")
_DOLLAR_PLACEHOLDER = re.compile(
    r"^(?:\$\{[^}]*\}|\$\(.*|\$[A-Za-z_][A-Za-z0-9_]*)$", re.S)


def clean_value(value):
    value = value.strip()
    value = value.lstrip("\"'`")
    return value.rstrip("\"'`,;")


# Characters that can continue a credential. "[REDACTED]realvalue" or
# "[REDACTED]-realvalue" is not redacted; "[REDACTED]," or "[REDACTED])" is.
_CREDENTIAL_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.~+/=")


def is_redacted(value, marker):
    """Value equals the marker, or the marker is followed by a character that
    cannot continue a credential (anything but letters, digits and -_.~+/=)."""
    if value == marker:
        return True
    return (value.startswith(marker) and len(value) > len(marker)
            and value[len(marker)] not in _CREDENTIAL_CHARS)


def is_placeholder(value):
    if value in ("...", "\u2026"):
        return True
    return bool(_ANGLE_PLACEHOLDER.match(value) or _DOLLAR_PLACEHOLDER.match(value))


def value_is_safe(raw, marker):
    """True when a captured secret value is empty, a redaction marker or a
    placeholder. Everything else is treated as a real value."""
    value = clean_value(raw)
    if value == "":
        return True
    if is_redacted(value, marker) or is_placeholder(value):
        return True
    if value.endswith(".") and value.strip("."):
        trimmed = clean_value(value[:-1])
        return trimmed != "" and (is_redacted(trimmed, marker) or is_placeholder(trimmed))
    return False


# ------------------------------------------------------ Authorization header

AUTH_HEADER_RE = re.compile(
    r"(?i)(?<![a-z0-9_])([\"']?)(authorization)([\"']?)([^\S\n]*:[^\S\n]*)")
AUTH_VALUE_LIMIT = 4096  # bound per header so one huge line cannot make the scan quadratic
_AUTH_SCHEME_RE = re.compile(r"([A-Za-z][A-Za-z0-9._~+/\-]*)[^\S\n]+(?=\S)")


def auth_header_spans(text):
    """Yield (start, cred_start, cred_end, scheme) for every "Authorization:"
    header (any case, also Proxy-Authorization, JSON/YAML keys and quoted curl
    arguments). The credential runs to the closing quote when the header or
    its value is quoted, else to the end of the line, and never further than
    AUTH_VALUE_LIMIT characters. scheme is "" when the value is a single token."""
    for m in AUTH_HEADER_RE.finditer(text):
        pos = m.end()
        bound = min(len(text), pos + AUTH_VALUE_LIMIT)
        eol = text.find("\n", pos, bound)
        eol = bound if eol < 0 else eol
        quote = ""
        if pos < eol and text[pos] in "\"'":
            quote = text[pos]
            pos += 1
        elif m.group(1) and not m.group(3):
            quote = m.group(1)
        end = text.find(quote, pos, eol) if quote else -1
        end = eol if end < 0 else end
        while pos < end and text[pos].isspace():
            pos += 1
        value = text[pos:end].rstrip()
        sm = _AUTH_SCHEME_RE.match(value)
        if sm:
            yield m.start(), pos + sm.end(), pos + len(value), sm.group(1)
        else:
            yield m.start(), pos, pos + len(value), ""
