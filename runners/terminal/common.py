"""Shared pieces of the terminal drivers (not a CLI).

Every driver produces one step transcript in the same format:

    $ <cmd or launch>
    <normalized output, verbatim; an omission is "[omitted: <reason>]">
    exit=<n>                      or      status=done|timeout|not-verified

and sends it through the same pipeline: scrub secrets from the raw bytes, write the raw
transcript (mode 600, placeholders only), normalize (normalize.py), redact
(capture/redact.py), gate (gates/leakscan.py with the denylists and the run denylist), then
count every secret in every file the run wrote. A failing gate or a nonzero count fails the
step and the redacted transcript is not kept.

Exit codes of every driver: 0 passed, 1 failed (also timeout and not-verified), 2 cannot run
(bad input, missing tool, unsafe secret file). Nothing here prints a secret value.
"""

import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.parse

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
from normalize import normalize  # noqa: E402

NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
TEMPLATE_RE = re.compile(r"\{\{\s*([a-z]+)\.([A-Za-z0-9_-]+)(?:\.([A-Za-z0-9_]+))?\s*\}\}")
PERSONA_FIELDS = ("name", "email", "handle", "id", "role", "team")
MIN_VALUE_LEN = 4


class CannotRun(Exception):
    """The driver cannot run this step (exit 2)."""


def now_ms():
    return int(time.monotonic() * 1000)


# --------------------------------------------------------------------------- files

def private_write(path, data, mode=0o600):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as fh:
            fd = None
            fh.write(data if isinstance(data, bytes) else data.encode("utf-8"))
    finally:
        if fd is not None:
            os.close(fd)


def private_dir(path):
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def secret_classes(step):
    """The step's secret_classes declaration: {name: 'test' | 'real'}."""
    sc = step.get("secret_classes", {})
    if not isinstance(sc, dict) or not all(NAME_RE.match(str(k)) and v in ("test", "real") for k, v in sc.items()):
        raise CannotRun("secret_classes must map secret names to test or real")
    return sc


def read_step(path):
    try:
        with open(path, encoding="utf-8") as fh:
            step = json.load(fh)
    except (OSError, ValueError) as exc:
        raise CannotRun(f"cannot read step file {path}: {exc}") from None
    if not isinstance(step, dict) or not ID_RE.match(str(step.get("id", ""))):
        raise CannotRun("step file needs an object with an id of letters, digits, '_', '-', '.'")
    return step


# --------------------------------------------------------------------------- context

class Context:
    """Paths, roster, denylists and secret values of one driver invocation."""

    def __init__(self, args):
        self.out = os.path.abspath(args.out)
        self.raw_dir = os.path.abspath(args.raw_dir or os.path.join(self.out, "secrets", "terminal"))
        self.secrets_dir = os.path.abspath(args.secrets_dir or os.path.join(self.out, "secrets", "values"))
        self.run_denylist = os.path.abspath(args.run_denylist or os.path.join(self.out, "secrets", "run-denylist.txt"))
        self.denylists = [os.path.abspath(d) for d in args.denylist]
        self.policies = [os.path.abspath(p) for p in args.policy]
        self.roster_path = os.path.abspath(args.roster or os.path.join(REPO, "fixtures", "roster.example.json"))
        self.wrapper = shlex.split(args.wrapper) if args.wrapper else []
        self.secrets = {}  # name -> value, memory only
        self.secret_classes = {}  # name -> test | real, from the step
        self.roster = None
        if not self.denylists:
            raise CannotRun("at least one --denylist is required (the real denylist, kept outside the repository)")
        for d in self.denylists:
            if not os.path.isfile(d):
                raise CannotRun(f"denylist not found: {d}")
        for p in self.policies:
            if not os.path.isfile(p):
                raise CannotRun(f"policy not found: {p}")
        if not os.path.isfile(self.roster_path):
            raise CannotRun(f"roster not found: {self.roster_path}")
        os.makedirs(os.path.join(self.out, "terminal"), exist_ok=True)
        private_dir(self.raw_dir)
        if not os.path.exists(self.run_denylist):
            private_write(self.run_denylist, "# one-time values seen during this run; delete at wrap-up\n")

    # -- templates

    def person(self, pid, field):
        if self.roster is None:
            try:
                with open(self.roster_path, encoding="utf-8") as fh:
                    self.roster = {p["id"]: p for p in json.load(fh)["people"]}
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise CannotRun(f"roster unreadable: {exc}") from None
        p = self.roster.get(pid)
        if p is None or field not in PERSONA_FIELDS or p.get(field) is None:
            raise CannotRun(f"template persona.{pid}.{field} does not resolve")
        return str(p[field])

    def template(self, text, where, test_secrets=False):
        """Resolve {{persona.*}}. A {{secret.*}} is an error (secrets travel another way), except
        where test_secrets is set and the secret is declared class test in secret_classes: then
        its value is substituted (a throwaway value, still scrubbed from every written file)."""
        def sub(m):
            ns, ident, field = m.groups()
            if ns == "persona" and field:
                return self.person(ident, field)
            if ns == "secret" and not field and test_secrets:
                cls = self.secret_classes.get(ident)
                if cls == "test":
                    return self.secret(ident)
                raise CannotRun(f"{where}: {m.group(0)} is {'class real' if cls == 'real' else 'not declared class test'}; "
                                "only a throwaway value declared class test may appear in literal text "
                                "(a real credential is entered by a human, never by a driver)")
            raise CannotRun(f"{where}: {m.group(0)} is not allowed here (secrets are only entered through "
                            "a pty dialog or a tmux paste_buffer key, never in a command line)")
        return TEMPLATE_RE.sub(sub, text)

    # -- secrets

    def secret(self, name):
        """Read secrets-dir/<name> (mode 600) into memory and register it."""
        if not NAME_RE.match(name):
            raise CannotRun(f"secret name {name!r} is not valid")
        if name in self.secrets:
            return self.secrets[name]
        path = os.path.join(self.secrets_dir, name)
        try:
            st = os.stat(path)
        except OSError:
            raise CannotRun(f"secret {name} is not available (no file {path})") from None
        if st.st_mode & 0o077:
            raise CannotRun(f"secret file for {name} must be mode 600 (group/other access found)")
        with open(path, "rb") as fh:
            value = fh.read().decode("utf-8").strip("\r\n")
        self.register(name, value)
        return value

    def register(self, name, value):
        if "\n" in value or "\r" in value:
            raise CannotRun(f"secret {name} spans lines and cannot be gated")
        if len(value) < MIN_VALUE_LEN:
            raise CannotRun(f"secret {name} is shorter than {MIN_VALUE_LEN} characters and cannot be gated")
        if self.secrets.get(name) == value:
            return
        self.secrets[name] = value
        with open(self.run_denylist, "r+", encoding="utf-8") as fh:
            if value not in fh.read().split("\n"):
                fh.write(value + "\n")

    def capture_secret(self, name, value):
        """A value extracted from output: run secrets dir (600) and the run denylist."""
        if not NAME_RE.match(name):
            raise CannotRun(f"capture_as name {name!r} is not valid")
        private_dir(self.secrets_dir)
        private_write(os.path.join(self.secrets_dir, name), value + "\n")
        self.register(name, value)

    # -- scrubbing

    def scrub(self, data):
        """Defensive replace of every secret (raw, JSON-escaped, URL-encoded) in bytes or text."""
        is_bytes = isinstance(data, (bytes, bytearray))
        text = bytes(data).decode("utf-8", errors="surrogateescape") if is_bytes else data
        for name, value in sorted(self.secrets.items(), key=lambda kv: -len(kv[1])):
            marker = f"[secret:{name}]"
            for form in forms(value):
                text = text.replace(form, marker)
        return text.encode("utf-8", errors="surrogateescape") if is_bytes else text

    def count_leaks(self):
        """Occurrences of each secret in every file written under out and raw_dir (not the
        secret store and the run denylist). Returns {name: count} for names with count > 0."""
        skip = [os.path.realpath(self.secrets_dir), os.path.realpath(self.run_denylist)]
        roots = {os.path.realpath(self.out), os.path.realpath(self.raw_dir)}
        needles = {n: [f.encode("utf-8") for f in forms(v)] for n, v in self.secrets.items()}
        found = {}
        for root in roots:
            for dirpath, _dirs, files in os.walk(root):
                for fname in files:
                    path = os.path.realpath(os.path.join(dirpath, fname))
                    if any(path == s or path.startswith(s + os.sep) for s in skip):
                        continue
                    try:
                        with open(path, "rb") as fh:
                            blob = fh.read()
                    except OSError:
                        continue
                    for name, forms_ in needles.items():
                        c = sum(blob.count(f) for f in forms_)
                        if c:
                            found[name] = found.get(name, 0) + c
        return found

    # -- command construction

    def remote_argv(self, cmd):
        """The command as ONE argument for the remote shell: <wrapper> bash -lc '<cmd>'."""
        if self.wrapper:
            return self.wrapper + ["bash", "-lc", cmd]
        return ["/bin/sh", "-c", cmd]


def forms(value):
    out = [value]
    for f in (json.dumps(value)[1:-1], urllib.parse.quote(value, safe="")):
        if f not in out:
            out.append(f)
    return out


def add_common_args(ap):
    ap.add_argument("--step", required=True, help="step JSON file (id and the driver's fields)")
    ap.add_argument("--out", required=True, help="run directory; transcripts go to <out>/terminal/")
    ap.add_argument("--raw-dir", help="raw transcripts, mode 600 (default <out>/secrets/terminal)")
    ap.add_argument("--secrets-dir", help="one file per secret, mode 600 (default <out>/secrets/values)")
    ap.add_argument("--run-denylist", help="one-time values (default <out>/secrets/run-denylist.txt)")
    ap.add_argument("--denylist", action="append", default=[], help="denylist file; repeatable")
    ap.add_argument("--policy", action="append", default=[], help="policy file; repeatable")
    ap.add_argument("--roster", help="roster (default fixtures/roster.example.json)")
    ap.add_argument("--wrapper", help="adapter-local command that enters the isolated environment "
                    "(for example a shell into a VM); the step command is passed to it as one quoted argument")


# --------------------------------------------------------------------------- pipeline

def omitted(reason):
    return f"[omitted: {reason}]\n"


def _run_tool(argv, **kw):
    try:
        return subprocess.run(argv, capture_output=True, **kw)
    except OSError as exc:
        raise CannotRun(f"cannot run {os.path.basename(argv[1])}: {exc.strerror}") from None


def record(ctx, step_id, header, body, trailer, capture_as=None):
    """Write the transcript of one step through the full pipeline.

    body: raw bytes or text as the program/terminal produced them.
    Returns a dict: gates (list of rule labels), leaks ({name: count}), kept (bool),
    redacted (the redacted transcript text or None), body (redacted body or None),
    capture_error (str or None), raw (path).
    """
    info = {"gates": [], "leaks": {}, "kept": False, "redacted": None, "body": None, "capture_error": None}
    data = body.encode("utf-8") if isinstance(body, str) else bytes(body)
    if capture_as:
        text = normalize(data)
        try:
            m = re.search(capture_as["pattern"], text, re.M)
        except re.error as exc:
            raise CannotRun(f"capture_as pattern does not compile: {exc}") from None
        if m and m.groups() and m.group(1):
            ctx.capture_secret(capture_as["name"], m.group(1))
        else:
            info["capture_error"] = f"capture_as {capture_as['name']}: pattern did not match"
    data = ctx.scrub(data)
    header = ctx.scrub(header)
    raw_path = os.path.join(ctx.raw_dir, step_id + ".raw.txt")
    private_write(raw_path, f"$ {header}\n".encode("utf-8") + data + (trailer + "\n").encode("utf-8"))
    info["raw"] = raw_path
    norm = normalize(data)
    transcript = f"$ {header}\n{norm}{trailer}\n"
    norm_path = os.path.join(ctx.raw_dir, step_id + ".norm.txt")
    private_write(norm_path, transcript)
    red = _run_tool([sys.executable, os.path.join(REPO, "capture", "redact.py"), norm_path,
                     *[a for p in ctx.policies for a in ("--policy", p)]])
    if red.returncode != 0:
        raise CannotRun("redact.py failed: " + red.stderr.decode("utf-8", "replace").strip()[:200])
    redacted = red.stdout.decode("utf-8")
    out_path = os.path.join(ctx.out, "terminal", step_id + ".txt")
    private_write(out_path, redacted, 0o644)
    scan = _run_tool([sys.executable, os.path.join(REPO, "gates", "leakscan.py"), "--roster", ctx.roster_path,
                      *[a for d in ctx.denylists + [ctx.run_denylist] for a in ("--denylist", d)],
                      *[a for p in ctx.policies for a in ("--policy", p)], "--skip-images", out_path])
    if scan.returncode == 2:
        os.remove(out_path)
        raise CannotRun("leakscan could not run: " + scan.stderr.decode("utf-8", "replace").strip()[:200])
    if scan.returncode == 1:
        info["gates"] = [ln.split(" ", 1)[0] + (":" + ln.rsplit(":", 1)[-1] if ":" in ln else "")
                         for ln in scan.stdout.decode("utf-8", "replace").splitlines() if ln.strip()]
    info["leaks"] = ctx.count_leaks()
    if info["gates"] or info["leaks"]:
        os.remove(out_path)
        return info
    info["kept"] = True
    info["redacted"] = redacted
    lines = redacted.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    info["body"] = "\n".join(lines[header.count("\n") + 1:-1]) + "\n"
    return info


def evaluate(status, exit_code, expect_exit, contains, info):
    """Verdict shared by exec and pty: returns (ok, problems)."""
    problems = []
    if status != "done":
        problems.append(f"status {status}")
    elif expect_exit is not None and exit_code != expect_exit:
        problems.append(f"exit {exit_code}, expected {expect_exit}")
    if info["capture_error"]:
        problems.append(info["capture_error"])
    if info["gates"]:
        problems.append("gate: " + ", ".join(info["gates"]))
    if info["leaks"]:
        problems.append("secret found in written files: " + ", ".join(f"{k} x{v}" for k, v in info["leaks"].items()))
    if info["kept"]:
        for needle in contains or []:
            if needle not in info["body"]:
                problems.append("stdout_contains: text not found")
                break
    elif contains:
        problems.append("stdout_contains not evaluated: transcript not kept")
    return (not problems), problems


def write_result(ctx, step, driver, status, exit_code, started, ok, problems, info, extra=None):
    res = {
        "id": step["id"], "driver": driver, "status": status, "exit": exit_code, "passed": ok,
        "duration_ms": now_ms() - started, "problems": problems,
        "transcript": os.path.join("terminal", step["id"] + ".txt") if info and info["kept"] else None,
        "gates": info["gates"] if info else [], "secret_leaks": info["leaks"] if info else {},
    }
    if extra:
        res.update(extra)
    path = os.path.join(ctx.out, "terminal", step["id"] + ".result.json")
    private_write(path, json.dumps(res, indent=1) + "\n", 0o644)
    print(f"[terminal] {step['id']} {driver} {'ok' if ok else 'FAIL'} status={status}"
          + (f" exit={exit_code}" if exit_code is not None else "")
          + (" :: " + "; ".join(problems) if problems else ""))
    return res


def run_cli(fn, argv=None):
    try:
        return fn(argv)
    except CannotRun as exc:
        print(f"cannot run: {exc}", file=sys.stderr)
        return 2
