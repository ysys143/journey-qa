#!/usr/bin/env python3
"""tmux_driver.py: drive an interactive session in a dedicated tmux server and judge it from
durable state.

Usage:
  tmux_driver.py --step STEP.json --out DIR [common options]
                 [--run-id ID] [--socket-dir DIR] [--tmux PATH]
                 [--blocked-pattern REGEX] [--sql-command CMD] [--poll-ms 100]

Step fields:
  id, launch (command started in the pane), size ("COLSxROWS", default 220x50),
  keys (ordered list of {literal: text} | {key: NamedKey} | {paste_buffer: secret name} | {wait_for: regex};
  {secret: name} is accepted as an alias of paste_buffer),
  done_when (screen regex, required), timeout_s (ceiling for the whole step, default 300),
  verify (list of checks, required: the verdict comes from these, not from the screen),
  verify_note (required reason when verify is empty), expect_exit (optional, read from
  #{pane_dead_status} once the program exits), capture_as, cleanup (list of commands that
  revert state the run created; they run on every path), secret_classes (name -> test | real,
  filled by the engine from the scenario's secrets declaration).

Secret classes: {{secret.<name>}} inside a literal item is accepted only when secret_classes
declares <name> as test (a throwaway value; it travels as send-keys argv and is still scrubbed from
every written file and added to the run denylist). A real or undeclared secret is refused, and
{{secret.*}} in launch, wait_for, done_when, cleanup and verify is refused for every class.
Real credentials are never entered by a driver; use a human gate.

Verify checks: {kind: file, path, exists | contains | equals | matches, remote?},
{kind: api, url, status (default 200), contains}, {kind: sql, query, contains | equals}
(needs --sql-command, a command that reads the query on stdin and prints the result).

Mechanics:
  - One server per invocation on a short socket path (-S <socket-dir>/<run-id>), started with
    -f /dev/null; the default server is never touched. The socket path is checked against
    sun_path (103 bytes on macOS, 107 elsewhere); too long means exit 2. The server is killed
    in a finally block on every path.
  - The pane runs the launch command in a clean environment (env -i with HOME PATH TERM LANG TZ
    NO_COLOR=1 HISTFILE=/dev/null), remain-on-exit on, the window at the step's size.
  - Literal text goes through send-keys -l, named keys through send-keys without -l, and Enter
    is always its own key. A paste_buffer secret is read from a mode 600 file into memory, loaded with
    `load-buffer -b <buffer> -` (value on stdin, never on any argv), pasted with
    `paste-buffer -d -b <buffer>` (the buffer is deleted) and never typed. After every paste the
    driver asserts `list-buffers` is empty and the value is absent from `capture-pane -p -S - -E -`.
    If the program echoed the value, the history is cleared (`clear-history`; when the value is
    still on the visible rows, the pane is reset as well), the transcript records
    `[omitted: echoed secret cleared]` and the check runs again; a value that survives fails the
    step. SIGTERM, SIGINT and SIGHUP run the same cleanup as any other path. wait_for and
    done_when poll `capture-pane -p -J -S - -E -` with the step ceiling; there is no fixed wait.
  - A screen that matches --blocked-pattern (an outer policy layer refused the action) ends the
    step with status=not-verified, never passed.

Transcript: "$ <launch>", the normalized final screen and history, then "exit=<n>" when the
program exited, "status=done" when done_when matched while it still ran, "status=timeout" or
"status=not-verified". Written through the pipeline in common.py.
Exit: 0 passed, 1 failed (timeout, not-verified, failed check, gate), 2 cannot run.
"""

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import secrets as pysecrets  # noqa: E402
import shlex  # noqa: E402
import shutil  # noqa: E402
import signal  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as K  # noqa: E402
from normalize import normalize  # noqa: E402

HISTORY_LIMIT = 100000
SIZE_RE = re.compile(r"^([0-9]{2,3})x([0-9]{2,3})$")
KEY_RE = re.compile(r"^(?:[CMS]-)*(?:[A-Za-z][A-Za-z0-9]*|[!-~])$")
DEAD_NOTE = re.compile(r"^Pane is dead \(.*\)$")
SUN_PATH_MAX = 103 if sys.platform == "darwin" else 107


class Session:
    """Thin wrapper over one dedicated tmux server."""

    def __init__(self, tmux, sock):
        self.tmux, self.sock, self.pane = tmux, sock, None
        self.env = {k: v for k, v in os.environ.items() if k != "TMUX"}

    def cmd(self, *args, input=None, check=True):
        p = subprocess.run([self.tmux, "-S", self.sock, "-f", "/dev/null", *args], input=input,
                           capture_output=True, env=self.env)
        if check and p.returncode != 0:
            raise K.CannotRun(f"tmux {args[0]} failed: {p.stderr.decode('utf-8', 'replace').strip()[:200]}")
        return p

    def capture(self, rng=("-S", "-", "-E", "-")):
        p = self.cmd("capture-pane", "-p", "-J", *rng, "-t", self.pane, check=False)
        if p.returncode != 0:
            return None
        text = normalize(p.stdout)
        lines = text.split("\n")
        while lines and lines[-1] == "":
            lines.pop()
        if lines and DEAD_NOTE.match(lines[-1]):  # tmux's own note under a dead pane, not program output
            lines.pop()
        return ("\n".join(lines) + "\n") if lines else ""

    def state(self):
        """(dead, exit status or None, history_size) or None when the server or pane is gone."""
        p = self.cmd("display-message", "-p", "-t", self.pane,
                     "#{pane_dead}|#{pane_dead_status}|#{history_size}", check=False)
        if p.returncode != 0:
            return None
        dead, status, hist = (p.stdout.decode().strip().split("|") + ["", "", ""])[:3]
        return dead == "1", (int(status) if status.lstrip("-").isdigit() else None), int(hist) if hist.isdigit() else 0

    def send_literal(self, text):
        for i, part in enumerate(text.split("\n")):
            if i:
                self.cmd("send-keys", "-t", self.pane, "Enter")
            if part:
                self.cmd("send-keys", "-t", self.pane, "-l", "--", part)

    def send_key(self, name):
        self.cmd("send-keys", "-t", self.pane, name)

    def buffers_left(self):
        p = self.cmd("list-buffers", check=False)
        return [ln for ln in p.stdout.decode("utf-8", "replace").splitlines() if ln.strip()]

    def holds(self, value):
        """True when the value is on the pane's visible rows or in its history."""
        p = self.cmd("capture-pane", "-p", "-J", "-S", "-", "-E", "-", "-t", self.pane, check=False)
        blob = p.stdout
        return any(f.encode("utf-8") in blob for f in K.forms(value))

    def paste_secret(self, name, value, ctx):
        """Paste through the buffer channel, then assert nothing of it is left on the server.
        Returns (transcript text for what was cleared, problems, record for the result)."""
        pieces, problems = [], []
        buf = f"jqa-{name}-{pysecrets.token_hex(3)}"
        self.cmd("load-buffer", "-b", buf, "-", input=value.encode("utf-8"))
        p = self.cmd("paste-buffer", "-d", "-b", buf, "-t", self.pane, check=False)
        if p.returncode != 0:
            self.cmd("delete-buffer", "-b", buf, check=False)
            raise K.CannotRun("tmux paste-buffer failed")
        left = self.buffers_left()
        if left:
            for ln in left:
                self.cmd("delete-buffer", "-b", ln.split(":", 1)[0], check=False)
            problems.append(f"tmux list-buffers shows {len(left)} buffer(s) after pasting {name}")
        end = time.monotonic() + 0.5  # the echo, if any, arrives a moment after the paste
        found = self.holds(value)
        while not found and time.monotonic() < end:
            time.sleep(0.05)
            found = self.holds(value)
        if found:
            hist = self.capture(("-S", "-", "-E", "-1"))
            pieces.append(ctx.scrub(hist or ""))
            self.cmd("clear-history", "-t", self.pane, check=False)
            if self.holds(value):  # still on the visible rows: reset the pane, keeping what was shown
                pieces.append(ctx.scrub(self.capture(("-S", "0", "-E", "-")) or ""))
                self.cmd("send-keys", "-R", "-t", self.pane, check=False)
                self.cmd("clear-history", "-t", self.pane, check=False)
            pieces.append(K.omitted("echoed secret cleared"))
        clean = not self.holds(value)
        if found and not clean:
            problems.append(f"the value of {name} is still in the pane after clear-history")
        return "".join(pieces), problems, {"name": name, "buffers_left": len(left), "echoed": found,
                                           "scrollback_clean": clean}

    def server_alive(self):
        return self.cmd("list-sessions", check=False).returncode == 0

    def kill(self):
        self.cmd("kill-server", check=False)
        end = time.monotonic() + 3
        while time.monotonic() < end and (os.path.exists(self.sock) or self.server_alive()):
            time.sleep(0.05)
        if os.path.exists(self.sock) and not self.server_alive():
            try:
                os.unlink(self.sock)
            except OSError:
                pass
        return not os.path.exists(self.sock) and not self.server_alive()


def check_tmux(path):
    try:
        out = subprocess.run([path, "-V"], capture_output=True, timeout=10).stdout.decode().strip()
    except (OSError, subprocess.SubprocessError):
        raise K.CannotRun("tmux could not be run; install tmux 3.2 or newer") from None
    m = re.search(r"(\d+)\.(\d+)", out)
    if m and (int(m.group(1)), int(m.group(2))) < (3, 2):
        raise K.CannotRun(f"tmux 3.2 or newer is required, found: {out}")


def parse_step(ctx, step):
    ctx.secret_classes = K.secret_classes(step)
    launch = step.get("launch")
    if not isinstance(launch, str) or not launch.strip():
        raise K.CannotRun("tmux step needs launch (a non-empty string)")
    launch = ctx.template(launch, "launch")
    size = str(step.get("size", "220x50"))
    m = SIZE_RE.match(size)
    if not m:
        raise K.CannotRun(f"size must look like 220x50, got {size!r}")
    if not isinstance(step.get("done_when"), str) or not step["done_when"]:
        raise K.CannotRun("tmux step needs done_when (a screen regex)")
    timeout = float(step.get("timeout_s", 300))
    if timeout <= 0:
        raise K.CannotRun("timeout_s must be positive")
    verify = step.get("verify")
    if not isinstance(verify, list):
        raise K.CannotRun("tmux step needs verify (a list of checks; the verdict comes from durable state)")
    if not verify and not (isinstance(step.get("verify_note"), str) and step["verify_note"].strip()):
        raise K.CannotRun("an empty verify list needs a verify_note that says why")
    expect_exit = step.get("expect_exit")
    if expect_exit is not None and not isinstance(expect_exit, int):
        raise K.CannotRun("expect_exit must be an integer")
    keys = []
    for i, item in enumerate(step.get("keys", [])):
        if not isinstance(item, dict) or len(item) != 1:
            raise K.CannotRun(f"keys[{i}] must have exactly one of literal, key, paste_buffer, wait_for")
        (kind, val), = item.items()
        if kind == "literal" and isinstance(val, str):
            keys.append(("literal", ctx.template(val, f"keys[{i}].literal", test_secrets=True)))
        elif kind == "key" and isinstance(val, str) and KEY_RE.match(val):
            keys.append(("key", val))
        elif kind in ("paste_buffer", "secret") and isinstance(val, str):
            if ctx.secret_classes.get(val) == "real":
                raise K.CannotRun(f"keys[{i}]: secret {val} is class real; a real credential is entered by a human, not a driver")
            keys.append(("secret", val))
            ctx.secret(val)
        elif kind == "wait_for" and isinstance(val, str):
            keys.append(("wait_for", compile_rx(ctx.template(val, f"keys[{i}].wait_for"), f"keys[{i}].wait_for")))
        else:
            raise K.CannotRun(f"keys[{i}] is not valid (literal text, a named key, a paste_buffer secret name or a wait_for regex)")
    cleanup = step.get("cleanup", [])
    if not isinstance(cleanup, list) or not all(isinstance(c, str) for c in cleanup):
        raise K.CannotRun("cleanup must be a list of commands")
    done = compile_rx(ctx.template(step["done_when"], "done_when"), "done_when")
    checks = [normalize_check(ctx, i, c) for i, c in enumerate(verify)]
    return launch, (int(m.group(1)), int(m.group(2))), keys, done, timeout, checks, expect_exit, \
        [ctx.template(c, "cleanup") for c in cleanup]


def compile_rx(pattern, where):
    try:
        return re.compile(pattern, re.M)
    except re.error as exc:
        raise K.CannotRun(f"{where} does not compile: {exc}") from None


def normalize_check(ctx, i, c):
    if not isinstance(c, dict) or c.get("kind") not in ("file", "api", "sql"):
        raise K.CannotRun(f"verify[{i}] needs kind file, api or sql")
    out = dict(c)
    for k in ("path", "url", "query", "contains", "equals", "matches"):
        if k in out:
            if not isinstance(out[k], str):
                raise K.CannotRun(f"verify[{i}].{k} must be a string")
            out[k] = ctx.template(out[k], f"verify[{i}].{k}")
    need = {"file": "path", "api": "url", "sql": "query"}[c["kind"]]
    if need not in out:
        raise K.CannotRun(f"verify[{i}] ({c['kind']}) needs {need}")
    return out


def run_checks(ctx, checks, sql_command):
    results = []
    for i, c in enumerate(checks):
        try:
            ok, why = run_check(ctx, c, sql_command)
        except K.CannotRun:
            raise
        except Exception as exc:  # a check that cannot be evaluated is a failed check, never a pass
            ok, why = False, f"error: {type(exc).__name__}"
        results.append({"index": i, "kind": c["kind"], "ok": ok, "reason": why})
    return results


def text_checks(c, text):
    if "equals" in c and text.rstrip("\n") != c["equals"].rstrip("\n"):
        return False, "equals: differs"
    if "contains" in c and c["contains"] not in text:
        return False, "contains: not found"
    if "matches" in c and not re.search(c["matches"], text, re.M):
        return False, "matches: no match"
    return True, ""


def run_check(ctx, c, sql_command):
    kind = c["kind"]
    if kind == "file":
        if c.get("remote"):
            p = subprocess.run(ctx.remote_argv("cat -- " + shlex.quote(c["path"])), capture_output=True, timeout=60)
            exists, text = p.returncode == 0, p.stdout.decode("utf-8", "replace")
        else:
            exists = os.path.isfile(c["path"])
            text = open(c["path"], encoding="utf-8", errors="replace").read() if exists else ""
        want = c.get("exists", True)
        if exists != bool(want):
            return False, "exists: " + ("missing" if want else "present")
        if not exists:
            return True, ""
        return text_checks(c, text)
    if kind == "api":
        try:
            with urllib.request.urlopen(c["url"], timeout=15) as r:
                status, text = r.status, r.read(1 << 20).decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            status, text = exc.code, ""
        if status != c.get("status", 200):
            return False, f"status {status}"
        return text_checks(c, text)
    if not sql_command:
        raise K.CannotRun("a sql verify check needs --sql-command (the adapter's local database command)")
    p = subprocess.run(shlex.split(sql_command), input=c["query"].encode("utf-8"), capture_output=True, timeout=60)
    if p.returncode != 0:
        return False, f"sql command exit {p.returncode}"
    return text_checks(c, p.stdout.decode("utf-8", "replace"))


def socket_path(args, run_id):
    base = args.socket_dir or os.path.join("/tmp", f"jqa-{os.getuid()}")
    sock = os.path.join(os.path.abspath(base), run_id)
    if len(os.fsencode(sock)) > SUN_PATH_MAX:
        raise K.CannotRun(f"socket path is {len(os.fsencode(sock))} bytes, over the {SUN_PATH_MAX}-byte limit of a unix "
                          "socket (sun_path): pass a shorter --socket-dir")
    return sock


def run(argv=None):
    ap = argparse.ArgumentParser(description="Drive an interactive session in a dedicated tmux server.")
    K.add_common_args(ap)
    ap.add_argument("--run-id", help="short id used in the socket name (default random)")
    ap.add_argument("--socket-dir", help="directory for the server socket (default /tmp/jqa-<uid>); keep it short")
    ap.add_argument("--tmux", help="tmux binary (default: tmux on PATH)")
    ap.add_argument("--blocked-pattern", help="regex; a screen that matches means an outer policy layer blocked "
                    "the action, and the step is reported not-verified")
    ap.add_argument("--sql-command", help="command that reads a query on stdin and prints the result (sql verify)")
    ap.add_argument("--poll-ms", type=int, default=100, help="screen polling interval (default 100)")
    args = ap.parse_args(argv)
    step = K.read_step(args.step)
    ctx = K.Context(args)
    started = K.now_ms()
    launch, (cols, rows), keys, done, timeout, checks, expect_exit, cleanup = parse_step(ctx, step)
    blocked = compile_rx(args.blocked_pattern, "--blocked-pattern") if args.blocked_pattern else None
    run_id = args.run_id or pysecrets.token_hex(3)
    if not re.match(r"^[A-Za-z0-9_-]{1,32}$", run_id):
        raise K.CannotRun("--run-id must be 1-32 letters, digits, '_' or '-'")
    tmux = args.tmux or shutil.which("tmux")
    if not tmux:
        raise K.CannotRun("tmux not found; install tmux 3.2 or newer to run tmux steps")
    check_tmux(tmux)
    sock = socket_path(args, run_id)
    sock_dir = os.path.dirname(sock)
    created_dir = not os.path.isdir(sock_dir)
    os.makedirs(sock_dir, mode=0o700, exist_ok=True)
    if os.path.exists(sock):
        raise K.CannotRun(f"socket {sock} already exists; choose another --run-id")

    lang = os.environ.get("LANG", "")
    pane_cmd = ["env", "-i", f"HOME={os.environ.get('HOME', '/')}", f"PATH={os.environ.get('PATH', '/usr/bin:/bin')}",
                "TERM=xterm-256color", f"LANG={lang if 'UTF-8' in lang.upper() else 'en_US.UTF-8'}",
                f"TZ={os.environ.get('TZ', 'UTC')}", "NO_COLOR=1", "HISTFILE=/dev/null", *ctx.remote_argv(launch)]
    sess = Session(tmux, sock)
    status, exit_code, problems, check_results, cleanup_results = "done", None, [], [], []
    body, info, server_gone, pre, pastes = "", None, False, [], []

    def on_signal(signum, _frame):
        raise K.CannotRun(f"interrupted by signal {signum}")

    old_handlers = {n: signal.signal(n, on_signal) for n in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    try:
        p = sess.cmd("set-option", "-g", "exit-empty", "off", ";", "set-option", "-g", "remain-on-exit", "on", ";",
                     "set-option", "-g", "-q", "remain-on-exit-format", "", ";",
                     "set-option", "-g", "history-limit", str(HISTORY_LIMIT), ";",
                     "new-session", "-d", "-P", "-F", "#{pane_id}", "-s", "jqa", "-x", str(cols), "-y", str(rows),
                     *pane_cmd)
        sess.pane = p.stdout.decode().strip()
        deadline = time.monotonic() + timeout
        poll = max(args.poll_ms, 10) / 1000.0

        def wait(rx, label):
            """matched | timeout | blocked | exited"""
            while True:
                st = sess.state()  # read before the screen: a pane seen dead has already drawn everything
                screen = sess.capture()
                if screen is None:
                    return "exited"
                if blocked and blocked.search(screen):
                    return "blocked"
                if rx.search(screen):
                    return "matched"
                if st is None or st[0]:
                    return "exited"
                if time.monotonic() > deadline:
                    return "timeout"
                time.sleep(poll)

        outcome = "matched"
        for i, (kind, val) in enumerate(keys):
            if kind == "literal":
                sess.send_literal(val)
            elif kind == "key":
                sess.send_key(val)
            elif kind == "secret":
                piece, bad, rec = sess.paste_secret(val, ctx.secret(val), ctx)
                pre.append(piece)
                pastes.append(rec)
                problems.extend(bad)
            else:
                outcome = wait(val, f"keys[{i}]")
                if outcome != "matched":
                    problems.append(f"keys[{i}] wait_for: {outcome}")
                    break
        if outcome == "matched":
            outcome = wait(done, "done_when")
            if outcome != "matched":
                problems.append(f"done_when: {outcome}")
        if outcome == "matched" and expect_exit is not None:
            while True:  # the program must end for its exit status to be read
                st = sess.state()
                if st is None or st[0] or time.monotonic() > deadline:
                    break
                time.sleep(poll)
        st = sess.state()
        screen = sess.capture()
        if screen is None:
            screen = ""
            problems.append("tmux server ended before the screen could be read")
        dead = bool(st and st[0])
        exit_code = st[1] if dead else None
        if st and st[2] >= HISTORY_LIMIT:
            screen = K.omitted("scrollback limit reached; earliest lines lost") + screen
        body = "".join(pre) + screen
        if outcome == "blocked":
            status = "not-verified"
            problems.append("blocked by an outer policy layer; the step was not verified")
        elif outcome == "timeout":
            status = "timeout"
        elif outcome == "exited":
            status = "exited"
            problems.append("the program exited before the screen matched")
        elif dead:
            status = "exited"
        if outcome == "matched":
            if expect_exit is not None and exit_code != expect_exit:
                problems.append(f"exit {exit_code}, expected {expect_exit}")
            check_results = run_checks(ctx, checks, args.sql_command)
            problems += [f"verify[{r['index']}] {r['kind']}: {r['reason']}" for r in check_results if not r["ok"]]
            if not checks:
                check_results = [{"index": None, "kind": "none", "ok": True, "reason": "verify_note: " + step["verify_note"].strip()}]
    except K.CannotRun:
        raise
    finally:
        for n in old_handlers:  # a second signal must not cut the cleanup short
            signal.signal(n, signal.SIG_IGN)
        for c in cleanup:  # revert state the run created, on every path
            try:
                r = subprocess.run(ctx.remote_argv(c), stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
                cleanup_results.append({"ok": r.returncode == 0, "exit": r.returncode})
            except (OSError, subprocess.SubprocessError):
                cleanup_results.append({"ok": False, "exit": None})
        server_gone = sess.kill()
        if created_dir:
            try:
                os.rmdir(sock_dir)
            except OSError:
                pass
    if status == "exited" and exit_code is not None:
        trailer = f"exit={exit_code}"
    elif status in ("timeout", "not-verified"):
        trailer = f"status={status}"
    elif exit_code is not None:
        trailer = f"exit={exit_code}"
    else:
        trailer = "status=done"
    info = K.record(ctx, step["id"], launch, body, trailer, step.get("capture_as"))
    if info["capture_error"]:
        problems.append(info["capture_error"])
    if info["gates"]:
        problems.append("gate: " + ", ".join(info["gates"]))
    if info["leaks"]:
        problems.append("secret found in written files: " + ", ".join(f"{k} x{v}" for k, v in info["leaks"].items()))
    if not all(r["ok"] for r in cleanup_results):
        problems.append("cleanup failed: state the run created may remain")
    if not server_gone:
        problems.append("tmux server or socket still present after the step")
    ok = not problems and status in ("done", "exited")
    K.write_result(ctx, step, "tmux", status, exit_code, started, ok, problems, info,
                   {"checks": check_results, "cleanup": cleanup_results, "server_gone": server_gone, "secret_pastes": pastes,
                    "verified_from": "verify checks" if checks else "verify_note"})
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(K.run_cli(run))
