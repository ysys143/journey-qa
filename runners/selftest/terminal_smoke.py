#!/usr/bin/env python3
"""Smoke tests for runners/terminal/ (run by terminal.sh, which check.sh calls).

Every behavior has a must-pass and a must-fail control. Targets are the fake programs in
runners/selftest/terminal/. Exit 0 when all controls hold, 1 when one fails, 2 cannot run.
"""
import sys

sys.dont_write_bytecode = True

import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import secrets  # noqa: E402
import shutil  # noqa: E402
import stat  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
import urllib.parse  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DRV = os.path.join(REPO, "runners", "terminal")
FAKES = os.path.join(HERE, "terminal")
sys.path.insert(0, DRV)
from normalize import normalize  # noqa: E402

PY = sys.executable
DENYLIST = os.path.join(REPO, "gates", "selftest", "denylist.test.txt")
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("OK   " if cond else "FAIL ") + name + ("" if cond else f" :: {detail}"))
    return bool(cond)


# ------------------------------------------------------------------ workspace

WORK = tempfile.mkdtemp(prefix="jqa-term-")
SOCK_DIR = tempfile.mkdtemp(prefix="jqa", dir="/tmp")  # unix socket paths must stay short
SEC = os.path.join(WORK, "sec")
os.makedirs(SEC, mode=0o700)
for f in ("fake_cli.py", "fake_tui.py", "fake-wrap.sh", "tmux-shim.sh"):
    shutil.copy(os.path.join(FAKES, f), os.path.join(WORK, f))
os.makedirs(os.path.join(WORK, "steps"))
SHIM_DIR = os.path.join(WORK, "shim")
os.makedirs(SHIM_DIR)
shutil.copy(os.path.join(FAKES, "tmux-shim.sh"), os.path.join(SHIM_DIR, "tmux"))
os.chmod(os.path.join(SHIM_DIR, "tmux"), 0o755)
REAL_TMUX = shutil.which("tmux")
COUNTER = [0]


def new_secret(name, mode=0o600):
    value = secrets.token_hex(8)
    path = os.path.join(SEC, name)
    with open(path, "w") as fh:
        fh.write(value + "\n")
    os.chmod(path, mode)
    return value


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def forms(value):
    return [value, json.dumps(value)[1:-1], urllib.parse.quote(value, safe="")]


class R:
    pass


def drive(driver, step, *extra, env=None, timeout=120, no_denylist=False):
    COUNTER[0] += 1
    n = COUNTER[0]
    step = dict(step)
    step.setdefault("id", f"s{n}")
    sf = os.path.join(WORK, "steps", f"{step['id']}.json")
    with open(sf, "w") as fh:
        json.dump(step, fh)
    out = os.path.join(WORK, f"run{n}")
    e = dict(os.environ)
    e.update(env or {})
    t = time.monotonic()
    p = subprocess.run([PY, os.path.join(DRV, driver + "_driver.py"), "--step", sf, "--out", out,
                        "--secrets-dir", SEC, *([] if no_denylist else ["--denylist", DENYLIST]), *extra], cwd=WORK, capture_output=True, env=e, timeout=timeout)
    r = R()
    r.rc, r.stderr, r.stdout, r.out, r.id = p.returncode, p.stderr.decode(), p.stdout.decode(), out, step["id"]
    r.elapsed = time.monotonic() - t
    rp = os.path.join(out, "terminal", step["id"] + ".result.json")
    r.res = json.load(open(rp)) if os.path.exists(rp) else None
    tp = os.path.join(out, "terminal", step["id"] + ".txt")
    r.text = open(tp, encoding="utf-8").read() if os.path.exists(tp) else None
    rawp = os.path.join(out, "secrets", "terminal", step["id"] + ".raw.txt")
    r.raw = open(rawp, "rb").read() if os.path.exists(rawp) else None
    return r


def files_with(value, root, skip=()):
    """Files under root (except the secret store and run denylist) that contain value in any form."""
    hits = []
    for dirpath, _d, files in os.walk(root):
        for f in files:
            path = os.path.join(dirpath, f)
            if path in skip or f == "run-denylist.txt" or os.path.realpath(path).startswith(os.path.realpath(SEC)):
                continue
            blob = open(path, "rb").read()
            if any(x.encode() in blob for x in forms(value)):
                hits.append(os.path.relpath(path, root))
    return hits


def leak_free(r, value):
    skip = {os.path.join(r.out, "secrets", "run-denylist.txt")}
    return files_with(value, r.out, skip) == []


# ------------------------------------------------------------------ normalizer

def normalizer():
    cases = [
        ("progress overwrite", b"progress 10%\rprogress 100%\n", "progress 100%\n"),
        ("colours removed", b"\x1b[32mgreen\x1b[0m and \x1b[1;31mred\x1b[m\n", "green and red\n"),
        ("OSC title removed (BEL and ST)", b"\x1b]0;title\x07a\x1b]8;;http://x\x1b\\b\n", "ab\n"),
        ("backspace folds", b"abc\b\bX\n", "aXc\n"),
        ("tab expands to 8 columns", b"a\tb\n", "a       b\n"),
        ("erase in line", b"old text\r\x1b[2Knew\n", "new\n"),
        ("CRLF is one newline", b"a\r\nb\r\n", "a\nb\n"),
        ("trailing blank lines dropped", b"a\n\n\n", "a\n"),
        ("empty stays empty", b"", ""),
    ]
    for name, raw, want in cases:
        check(f"normalize: {name}", normalize(raw) == want, repr(normalize(raw)))
    # must-fail controls: the naive alternatives differ, so the cases above can fail
    naive = lambda b: re.sub(rb"\x1b\[[0-9;]*[A-Za-z]", b"", b).replace(b"\r", b"").decode()
    check("normalize control: naive CR removal would concatenate overwrites",
          naive(b"progress 10%\rprogress 100%\n") != normalize(b"progress 10%\rprogress 100%\n"))
    check("normalize control: naive escape strip leaves an OSC title", naive(b"\x1b]0;title\x07a\n") != "a\n")
    out = normalize(b"x" * 500 + b"\n")
    check("normalize: long lines are never cut or wrapped", out == "x" * 500 + "\n")


# ------------------------------------------------------------------ renderer

def renderer():
    import render_terminal as RT  # noqa: E402  (imported late: it needs the capture dir on the path)
    long_line = "L" * 300
    lines, wrapped = RT.wrap_lines(long_line + "\nshort\n", 100)
    check("render: a long line is wrapped, not cut (every character survives)",
          wrapped == 1 and "".join(s.rstrip(RT.MARKER) if s.endswith(RT.MARKER) else s for s in lines[:-1]).count("L")
          + 0 >= 0 and sum(s.count("L") for s in lines) == 300, lines)
    check("render: wrapped segments end with the visible continuation marker and fit the width",
          all(s.endswith(RT.MARKER) and len(s) == 100 for s in lines[:3]) and len(lines[3]) <= 100, lines)
    check("render: lines within the width are untouched", RT.wrap_lines("abc\n", 100) == (["abc"], 0))
    old_cut = [long_line[:100]]
    check("render control: the old silent cut would lose characters", sum(s.count("L") for s in old_cut) < 300)
    if not shutil.which("rsvg-convert"):
        print("SKIP render CLI controls: rsvg-convert not found (the renderer itself exits 2 without it)")
        return
    for name, text, extra, want_cols, want_wrapped in (
        ("default width follows the longest line", "a" * 150 + "\nb\n", [], 150, 0),
        ("a line over the cap is wrapped with a note", "a" * 300 + "\n", [], 240, 1),
        ("an explicit narrow width wraps instead of cutting", "a" * 150 + "\n", ["--cols", "100"], 100, 1),
    ):
        src = os.path.join(WORK, "r.txt")
        open(src, "w").write(text)
        png = os.path.join(WORK, "r.png")
        p = subprocess.run([PY, os.path.join(REPO, "capture", "render_terminal.py"), "--scale", "1", *extra, src, png],
                           capture_output=True, text=True)
        side = json.load(open(png + ".render.json")) if os.path.exists(png + ".render.json") else {}
        ok = (p.returncode == 0 and side.get("cols") == want_cols and side.get("wrapped_lines") == want_wrapped
              and f"wrapped_lines={want_wrapped}" in p.stdout and open(png + ".txt").read() == text)
        check(f"render CLI: {name}", ok, p.stdout + p.stderr + str(side))
        for f in (png, png + ".txt", png + ".render.json"):
            if os.path.exists(f):
                os.remove(f)


# ------------------------------------------------------------------ exec

def exec_tests():
    r = drive("exec", {"cmd": "echo hello; exit 3", "expect_exit": 3, "stdout_contains": ["hello"]})
    check("exec: output and a nonzero exit as declared pass; transcript format",
          r.rc == 0 and r.text == "$ echo hello; exit 3\nhello\nexit=3\n", (r.rc, r.text, r.stderr))
    check("exec: raw transcript is mode 600",
          r.raw is not None and stat.S_IMODE(os.stat(os.path.join(r.out, "secrets", "terminal", r.id + ".raw.txt")).st_mode) == 0o600)
    r = drive("exec", {"cmd": "echo hello; exit 3", "expect_exit": 0})
    check("exec control: a nonzero exit that was not declared fails", r.rc == 1 and r.res and not r.res["passed"], r.rc)
    r = drive("exec", {"cmd": "echo hello"}, no_denylist=True)
    check("exec control: a run without any denylist is refused (exit 2)", r.rc == 2 and "denylist" in r.stderr, (r.rc, r.stderr))
    r = drive("exec", {"cmd": "echo hello", "stdout_contains": ["absent-text"]})
    check("exec control: missing stdout_contains text fails", r.rc == 1, r.rc)
    r = drive("exec", {"cmd": "printf 'progress 10%%\\rprogress 100%%\\n\\033[32mgreen\\033[0m\\n'"})
    check("exec: carriage-return overwrites and colours are normalized in the transcript",
          r.rc == 0 and "\nprogress 100%\ngreen\nexit=0\n" in r.text and "\x1b" not in r.text and "\r" not in r.text, r.text)
    check("exec: the raw transcript stays verbatim", r.raw and b"\r" in r.raw and b"\x1b[32m" in r.raw)
    t = time.monotonic()
    r = drive("exec", {"cmd": "sleep 30", "timeout_s": 1})
    check("exec: a command over its ceiling ends with status=timeout", r.rc == 1 and r.res["status"] == "timeout"
          and r.text.rstrip().endswith("status=timeout") and time.monotonic() - t < 15, (r.rc, r.res))
    r = drive("exec", {"cmd": "echo contact ivy.nonrealton@example.com"})
    check("exec: an email from the roster passes the text gate", r.rc == 0 and r.text is not None, (r.rc, r.res))
    r = drive("exec", {"cmd": "echo contact someone" + "@outside-org.net"})
    check("exec control: an email outside the roster fails the gate and the transcript is not kept",
          r.rc == 1 and r.text is None and any(g.startswith("EMAIL") for g in r.res["gates"]), (r.rc, r.res))
    value = "tok-" + secrets.token_hex(8)
    r = drive("exec", {"cmd": f"echo issued: {value}", "capture_as": {"name": "issued", "pattern": r"issued: (\S+)"}})
    store = os.path.join(SEC, "issued")
    check("exec: capture_as stores the value (mode 600), adds it to the run denylist and replaces it in every transcript",
          r.rc == 0 and open(store).read().strip() == value and stat.S_IMODE(os.stat(store).st_mode) == 0o600
          and value in open(os.path.join(r.out, "secrets", "run-denylist.txt")).read()
          and "issued: [secret:issued]" in r.text and leak_free(r, value), (r.rc, r.text, files_with(value, r.out)))
    r = drive("exec", {"cmd": "echo nothing", "capture_as": {"name": "none", "pattern": r"issued: (\S+)"}})
    check("exec control: a capture_as pattern that does not match fails the step", r.rc == 1, r.rc)
    new_secret("pw")
    r = drive("exec", {"cmd": "echo {{secret.pw}}"})
    check("exec control: a secret in a command line is refused (exit 2)", r.rc == 2 and "secret" in r.stderr, (r.rc, r.stderr))

    # remote quoting through a fake wrapper
    wlog, fhome = os.path.join(WORK, "wlog"), os.path.join(WORK, "fakehome")
    os.makedirs(fhome, exist_ok=True)
    wrapper = f"{os.path.join(WORK, 'fake-wrap.sh')} {wlog} {fhome}"
    cmd = 'test ~ = "$JQA_FAKE_HOME" && test "$JQA_REMOTE_VAR" = inside && echo remote-ok'
    r = drive("exec", {"cmd": cmd}, "--wrapper", wrapper)
    argv = open(wlog, "rb").read().split(b"\0")[:-1]
    check("remote quoting: &&, ~ and $VAR run entirely inside the wrapper",
          r.rc == 0 and "remote-ok" in r.text, (r.rc, r.text, r.stderr))
    check("remote quoting: the wrapper received the command as ONE argument (bash -lc <cmd>)",
          argv == [b"bash", b"-lc", cmd.encode()], argv)
    naive = subprocess.run(f"{wrapper} {cmd}", shell=True, cwd=WORK, capture_output=True, text=True,
                           env={**os.environ, "JQA_FAKE_HOME": fhome})
    check("remote quoting control: the same command unquoted is expanded by the host and fails",
          "remote-ok" not in naive.stdout)


# ------------------------------------------------------------------ pty

def pty_steps(password_name, echo=False):
    return [{"wait_for": "Server URL:", "send": "https://app.example.com"},
            {"wait_for": "Password:", "send": "{{secret." + password_name + "}}", "secret": True},
            {"wait_for": r"Continue\? \[y/n\]", "send": "y"}]


def pty_tests():
    pw = new_secret("pw")
    for echo in (False, True):
        label = "echo on" if echo else "echo off"
        cmd = f"python3 fake_cli.py {sha(pw)}" + (" --echo" if echo else "")
        r = drive("pty", {"cmd": cmd, "dialog": pty_steps("pw"), "stdout_contains": ["ready"], "timeout_s": 30})
        t = r.text or ""
        order = [t.find(x) for x in ("Server URL:", "Password:", "Continue?", "ready")]
        check(f"pty ({label}): prompts and answers appear in order, exit 0",
              r.rc == 0 and order == sorted(order) and -1 not in order and t.rstrip().endswith("exit=0"), (r.rc, t, r.stderr))
        check(f"pty ({label}): the secret is in no written file; the raw transcript holds the placeholder",
              leak_free(r, pw) and r.raw is not None and b"[secret:pw]" in r.raw, files_with(pw, r.out))
        check(f"pty ({label}): colours and carriage returns are normalized, the wide line is intact",
              "\x1b" not in t and "\r" not in t and "progress 100%" in t and "progress 10%" not in t
              and "wide-line:" + "0123456789" * 24 in t, t)
    wrong = new_secret("pw-wrong")
    r = drive("pty", {"cmd": f"python3 fake_cli.py {sha(pw)}", "dialog": pty_steps("pw-wrong"), "timeout_s": 30})
    check("pty control: a wrong password (exit 3) is detected", r.rc == 1 and r.res["exit"] == 3
          and "authentication failed" in r.text and leak_free(r, wrong), (r.rc, r.res))
    r = drive("pty", {"cmd": f"python3 fake_cli.py {sha(pw)}", "dialog": pty_steps("pw-wrong")[:2], "expect_exit": 3,
                      "timeout_s": 30})
    check("pty: the same exit code passes when it is the declared outcome", r.rc == 0, (r.rc, r.res))
    r = drive("pty", {"cmd": f"python3 fake_cli.py {sha(pw)}",
                      "dialog": [{"wait_for": "Server URL:", "send": "x"}, {"wait_for": "NeverAppears:", "send": "y"}],
                      "timeout_s": 2})
    check("pty control: a prompt that never appears ends with status=timeout (nothing is pre-typed)",
          r.rc == 1 and r.res["status"] == "timeout" and r.text.rstrip().endswith("status=timeout") and r.elapsed < 20,
          (r.rc, r.res, r.elapsed))
    new_secret("pw-open", 0o644)
    r = drive("pty", {"cmd": "echo x", "dialog": [{"wait_for": "x", "send": "{{secret.pw-open}}", "secret": True}]})
    check("pty control: a secret file readable by group or others is refused (exit 2)", r.rc == 2 and "600" in r.stderr, r.stderr)
    r = drive("pty", {"cmd": "echo x", "dialog": [{"wait_for": "x", "send": "{{secret.pw}}"}]})
    check("pty control: a secret answer without secret: true is refused (exit 2)", r.rc == 2, (r.rc, r.stderr))
    r = drive("pty", {"cmd": "echo x", "dialog": [{"wait_for": "x", "send": "plain", "secret": True}]})
    check("pty control: secret: true with a literal answer is refused (exit 2)", r.rc == 2, (r.rc, r.stderr))
    r = drive("pty", {"cmd": "echo x", "dialog": [{"send": "plain"}]})
    check("pty control: a dialog item without wait_for is refused (exit 2)", r.rc == 2, (r.rc, r.stderr))


# ------------------------------------------------------------------ tmux

MENU = [{"wait_for": "Select an action"}, {"wait_for": r"> Exit"}, {"key": "Down"}, {"wait_for": r"> Run task"},
        {"key": "Enter"}]


def tmux_args(run_id, *extra):
    return ["--socket-dir", SOCK_DIR, "--run-id", run_id, "--poll-ms", "50", *extra]


def server_gone(run_id):
    sock = os.path.join(SOCK_DIR, run_id)
    p = subprocess.run([REAL_TMUX, "-S", sock, "list-sessions"], capture_output=True)
    return p.returncode != 0 and not os.path.exists(sock)


def shim_env():
    log = os.path.join(WORK, f"shim-{COUNTER[0] + 1}.log0")
    return log, {"PATH": SHIM_DIR + os.pathsep + os.environ["PATH"], "JQA_SHIM_LOG": log, "JQA_REAL_TMUX": REAL_TMUX}


def tmux_tests():
    state = lambda n: f"state-{n}"
    marker = os.path.join(WORK, "cleanup-marker")
    open(marker, "w").write("x")
    step = {"launch": f"python3 fake_tui.py {state('a')}", "keys": MENU, "done_when": "TASK-COMPLETE-MARKER",
            "timeout_s": 60, "verify": [{"kind": "file", "path": state("a"), "contains": "choice=run"}],
            "cleanup": [f"rm -f {marker}"]}
    r = drive("tmux", step, *tmux_args("m1", "--blocked-pattern", "BLOCKED BY POLICY"))
    t = r.text or ""
    check("tmux: Down first, then Enter; verdict from the state file; exit status read from the dead pane",
          r.rc == 0 and r.res["checks"][0]["ok"] and t.startswith("$ python3 fake_tui.py state-a\n")
          and t.rstrip().endswith("exit=0") and "TASK-COMPLETE-MARKER" in t, (r.rc, t, r.stderr, r.res))
    check("tmux: carriage-return progress and colours are normalized in the transcript",
          "loading 100%" in t and "loading 10%" not in t and "\x1b" not in t and "\r" not in t and "Pane is dead" not in t, t)
    check("tmux: server and socket are gone after a pass", server_gone("m1") and r.res["server_gone"])
    check("tmux: cleanup hooks ran", not os.path.exists(marker) and r.res["cleanup"] == [{"ok": True, "exit": 0}], r.res)

    r = drive("tmux", {**step, "keys": [{"wait_for": "Select an action"}, {"key": "Enter"}],
                       "done_when": "TASK-COMPLETE-MARKER|exited without running"}, *tmux_args("m2"))
    check("tmux control: Enter on the default choice (Exit) without Down fails the verify check, whatever the screen says",
          r.rc == 1 and not r.res["checks"][0]["ok"] and "exited without running" in r.text, (r.rc, r.res))
    check("tmux: server and socket are gone after a failed verify", server_gone("m2"))

    open(marker, "w").write("x")
    t0 = time.monotonic()
    r = drive("tmux", {**step, "keys": [], "done_when": "NEVER-ON-SCREEN", "timeout_s": 3, "verify": [],
                       "verify_note": "timeout drill: no outcome to verify"}, *tmux_args("m3"))
    check("tmux control: done_when that never appears ends at the ceiling with status=timeout (no fixed sleep)",
          r.rc == 1 and r.res["status"] == "timeout" and r.text.rstrip().endswith("status=timeout")
          and 2 < time.monotonic() - t0 < 25, (r.rc, r.res, time.monotonic() - t0))
    check("tmux: server and socket are gone after a timeout", server_gone("m3"))
    check("tmux: cleanup hooks ran on the failure path too", not os.path.exists(marker), r.res)

    long_dir = os.path.join(WORK, "d" * 100)
    r = drive("tmux", step, "--socket-dir", long_dir, "--run-id", "m4")
    check("tmux control: a socket path over the unix limit exits 2 with a clear message",
          r.rc == 2 and "socket path" in r.stderr and not os.path.exists(long_dir), (r.rc, r.stderr))

    r = drive("tmux", {**step, "launch": "python3 fake_tui.py state-b --blocked", "keys": [],
                       "done_when": "NEVER-ON-SCREEN", "timeout_s": 30,
                       "verify": [{"kind": "file", "path": "state-b", "exists": False}]},
              *tmux_args("m5", "--blocked-pattern", "BLOCKED BY POLICY"))
    check("tmux: a step stopped by a policy layer is reported not-verified, never passed",
          r.rc == 1 and r.res["status"] == "not-verified" and not r.res["passed"] and r.res["checks"] == []
          and r.text.rstrip().endswith("status=not-verified") and r.elapsed < 25, (r.rc, r.res))
    check("tmux: server and socket are gone after not-verified", server_gone("m5"))

    for code, expect, want_rc in ((7, 7, 0), (7, 0, 1)):
        r = drive("tmux", {**step, "launch": f"python3 fake_tui.py state-c --exit-code {code}", "expect_exit": expect,
                           "verify": [{"kind": "file", "path": "state-c", "contains": "choice=run"}]},
                  *tmux_args(f"m6{expect}"))
        check(f"tmux: pane exit status {code} against expect_exit {expect} -> exit {want_rc}",
              r.rc == want_rc and r.res["exit"] == code and r.text.rstrip().endswith(f"exit={code}"), (r.rc, r.res))

    # remote quoting for the pane command
    wlog, fhome = os.path.join(WORK, "wlog2"), os.path.join(WORK, "fakehome")
    wrapper = f"{os.path.join(WORK, 'fake-wrap.sh')} {wlog} {fhome}"
    launch = 'test ~ = "$JQA_FAKE_HOME" && python3 fake_tui.py state-w'
    r = drive("tmux", {**step, "launch": launch, "cleanup": [],
                       "verify": [{"kind": "file", "path": "state-w", "contains": "choice=run"}]},
              *tmux_args("m7", "--wrapper", wrapper))
    argv = open(wlog, "rb").read().split(b"\0")[:-1]
    check("tmux: the pane command reaches the wrapper as bash -lc <one argument>",
          r.rc == 0 and argv == [b"bash", b"-lc", launch.encode()], (r.rc, argv, r.stderr))

    # secret entry: only through load-buffer / paste-buffer
    for mode in ("echo-off", "echo-on"):
        tok = new_secret("tok")
        log, env = shim_env()
        sname = f"state-{mode}"
        r = drive("tmux", {**step, "launch": f"python3 fake_tui.py {sname} --ask-secret {mode}",
                           "keys": [{"wait_for": "Token:"}, {"paste_buffer": "tok"}, {"key": "Enter"}, *MENU],
                           "verify": [{"kind": "file", "path": sname, "contains": f"token_sha256={sha(tok)}"}]},
                  *tmux_args(f"s-{mode}"), env=env)
        shim = open(log, "rb").read()
        calls = [c.split(b"\0")[:-1] for c in shim.split(b"\n") if c]
        calls = [c for c in calls if c != [b"-V"]]  # the version probe is the only call without a socket
        check(f"tmux secret ({mode}): the program received the value (hash in the state file)",
              r.rc == 0 and r.res["checks"][0]["ok"], (r.rc, r.res, r.stderr))
        check(f"tmux secret ({mode}): the value is absent from every tmux argv",
              shim and not any(f.encode() in shim for f in forms(tok)))
        flat = [b" ".join(c) for c in calls]
        check(f"tmux secret ({mode}): load-buffer reads stdin ('-'), paste-buffer deletes the buffer (-d), no send-keys carried it",
              any(b"load-buffer" in c and c.endswith(b" -") for c in flat) and any(b"paste-buffer -d" in c for c in flat),
              flat[-8:])
        check(f"tmux secret ({mode}): every call targets the dedicated socket with no user config",
              calls and all(c[:2] == [b"-S", os.path.join(SOCK_DIR, f"s-{mode}").encode()] and c[2:4] == [b"-f", b"/dev/null"]
                            for c in calls), calls[:2])
        check(f"tmux secret ({mode}): no file under the run directory and no capture contains the value",
              leak_free(r, tok) and files_with(tok, WORK, {log, os.path.join(SEC, "tok")}) == [], files_with(tok, r.out))
        pastes = r.res.get("secret_pastes") or []
        check(f"tmux secret ({mode}): after the paste 0 buffers were left and the full scrollback was checked",
              len(pastes) == 1 and pastes[0]["buffers_left"] == 0 and pastes[0]["scrollback_clean"], r.res)
        check(f"tmux secret ({mode}): list-buffers ran after paste-buffer",
              any(b"list-buffers" in c for c in flat[[i for i, c in enumerate(flat) if b"paste-buffer" in c][0]:]), flat[-8:])
        cleared = any(b"clear-history" in c for c in flat)
        if mode == "echo-on":
            check("tmux secret (echo-on): the echo was found, history cleared, and the transcript records the omission, no value",
                  cleared and pastes[0]["echoed"] and "[omitted: echoed secret cleared]" in r.text
                  and "Token: [secret:tok]" in r.text and b"[secret:tok]" in r.raw, (cleared, pastes, r.text))
        else:
            check("tmux secret (echo-off): nothing of the value was on the screen, no clear-history, no omission marker",
                  "Token:" in r.text and not cleared and not pastes[0]["echoed"] and "echoed secret" not in r.text, (cleared, r.text))
        check(f"tmux secret ({mode}): server and socket are gone", server_gone(f"s-{mode}"))

    # must-fail control for the detector: a value typed with send-keys DOES show up in the argv log
    typed = secrets.token_hex(8)
    log, env = shim_env()
    r = drive("tmux", {**step, "launch": "python3 fake_tui.py state-t --ask-secret echo-on",
                       "keys": [{"wait_for": "Token:"}, {"literal": typed}, {"key": "Enter"}, *MENU],
                       "verify": [{"kind": "file", "path": "state-t", "contains": "choice=run"}]},
              *tmux_args("m8"), env=env)
    shim = open(log, "rb").read()
    check("tmux secret control: a value typed with send-keys is visible in the argv log (the detector can fail)",
          any(f.encode() in shim for f in forms(typed)))

    # must-fail controls for the post-paste assertions
    for label, fault, want in (
            ("clear-history and pane reset do nothing", {"JQA_SHIM_NOOP": "clear-history|send-keys -R"}, "still in the pane"),
            ("paste-buffer leaves its buffer behind", {"JQA_SHIM_KEEP_BUFFER": "1"}, "list-buffers shows")):
        tok = new_secret("tok")
        log, env = shim_env()
        env.update(fault)
        r = drive("tmux", {**step, "launch": "python3 fake_tui.py state-f --ask-secret echo-on",
                           "keys": [{"wait_for": "Token:"}, {"paste_buffer": "tok"}, {"key": "Enter"}, *MENU],
                           "verify": [{"kind": "file", "path": "state-f", "contains": f"token_sha256={sha(tok)}"}]},
                  *tmux_args("m11"), env=env)
        check(f"tmux control: when {label}, the step fails and says why",
              r.rc == 1 and not r.res["passed"] and any(want in p for p in r.res["problems"]), (r.rc, r.res))
        check(f"tmux control: when {label}, no file holds the value and the server is gone",
              files_with(tok, WORK, {log, os.path.join(SEC, "tok")}) == [] and server_gone("m11"))

    # secret classes: a class test secret may be typed as literal text, anything else may not
    tok = new_secret("tok")
    r = drive("tmux", {**step, "launch": "python3 fake_tui.py state-k --ask-secret echo-on",
                       "secret_classes": {"tok": "test"},
                       "keys": [{"wait_for": "Token:"}, {"literal": "{{secret.tok}}\n"}, *MENU],
                       "verify": [{"kind": "file", "path": "state-k", "contains": f"token_sha256={sha(tok)}"}]},
              *tmux_args("m12"))
    check("tmux: a {{secret.*}} declared class test is accepted in literal text and still scrubbed from every file",
          r.rc == 0 and r.res["checks"][0]["ok"] and leak_free(r, tok) and "Token: [secret:tok]" in r.text,
          (r.rc, r.res, r.stderr, files_with(tok, r.out)))
    for label, extra in (("a class real secret", {"secret_classes": {"tok": "real"}}), ("an undeclared secret", {})):
        r = drive("tmux", {**step, **extra, "keys": [{"literal": "{{secret.tok}}"}]}, *tmux_args("m13"))
        check(f"tmux control: {label} in literal text is refused (exit 2)", r.rc == 2 and "literal" in r.stderr, (r.rc, r.stderr))
    r = drive("tmux", {**step, "secret_classes": {"tok": "real"}, "keys": [{"paste_buffer": "tok"}]}, *tmux_args("m13"))
    check("tmux control: a class real secret is refused for paste_buffer too (exit 2)", r.rc == 2 and "human" in r.stderr, (r.rc, r.stderr))
    r = drive("tmux", {**step, "secret_classes": {"tok": "test"}, "launch": "echo {{secret.tok}}"}, *tmux_args("m13"))
    check("tmux control: a class test secret in launch is still refused (exit 2)", r.rc == 2, (r.rc, r.stderr))
    r = drive("tmux", {**step, "keys": [{"wait_for": "x"}, {"bogus": "tok"}]}, *tmux_args("m13"))
    check("tmux control: an unknown keys item is refused (exit 2)", r.rc == 2, (r.rc, r.stderr))

    # signalled driver: the server and socket must not survive SIGTERM or SIGINT
    import signal as _sig
    for signame in ("SIGTERM", "SIGINT"):
        COUNTER[0] += 1
        sf = os.path.join(WORK, "steps", f"sig{COUNTER[0]}.json")
        json.dump({**step, "id": f"sig{COUNTER[0]}", "launch": "sleep 30", "keys": [], "done_when": "NEVER-ON-SCREEN",
                   "timeout_s": 60, "verify": [], "verify_note": "signal drill"}, open(sf, "w"))
        p = subprocess.Popen([PY, os.path.join(DRV, "tmux_driver.py"), "--step", sf, "--out", os.path.join(WORK, f"run{COUNTER[0]}"),
                              "--secrets-dir", SEC, "--denylist", DENYLIST, *tmux_args(f"sg{signame[3:5]}")],
                             cwd=WORK, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        sock = os.path.join(SOCK_DIR, f"sg{signame[3:5]}")
        end = time.monotonic() + 15
        while time.monotonic() < end and not os.path.exists(sock):
            time.sleep(0.05)
        time.sleep(0.5)
        up = os.path.exists(sock)
        p.send_signal(getattr(_sig, signame))
        try:
            rc = p.wait(timeout=20)
        except subprocess.TimeoutExpired:
            p.kill()
            rc = None
        check(f"tmux: {signame} to the driver ends it with exit 2 and leaves no server or socket behind",
              up and rc == 2 and server_gone(f"sg{signame[3:5]}"), (up, rc))

    # must-fail control for the signal drill: SIGKILL cannot be caught, so the detector sees the leftover server
    COUNTER[0] += 1
    sf = os.path.join(WORK, "steps", f"sig{COUNTER[0]}.json")
    json.dump({**step, "id": f"sig{COUNTER[0]}", "launch": "sleep 30", "keys": [], "done_when": "NEVER-ON-SCREEN",
               "timeout_s": 60, "verify": [], "verify_note": "signal drill"}, open(sf, "w"))
    p = subprocess.Popen([PY, os.path.join(DRV, "tmux_driver.py"), "--step", sf, "--out", os.path.join(WORK, f"run{COUNTER[0]}"),
                          "--secrets-dir", SEC, "--denylist", DENYLIST, *tmux_args("sgKL")],
                         cwd=WORK, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    end = time.monotonic() + 15
    while time.monotonic() < end and not os.path.exists(os.path.join(SOCK_DIR, "sgKL")):
        time.sleep(0.05)
    time.sleep(0.5)
    p.kill()
    p.wait()
    check("tmux control: after SIGKILL the leftover server is detected (the drill can fail)", not server_gone("sgKL"))
    subprocess.run([REAL_TMUX, "-S", os.path.join(SOCK_DIR, "sgKL"), "kill-server"], capture_output=True)
    for f in os.listdir(SOCK_DIR):
        if f == "sgKL":
            try:
                os.unlink(os.path.join(SOCK_DIR, f))
            except OSError:
                pass

    # validation: each is exit 2
    bad = {
        "no done_when": {k: v for k, v in step.items() if k != "done_when"},
        "no verify": {k: v for k, v in step.items() if k != "verify"},
        "empty verify without a note": {**step, "verify": []},
        "invalid key name": {**step, "keys": [{"key": "Enter; rm -rf x"}]},
        "secret template in literal text": {**step, "keys": [{"literal": "{{secret.tok}}"}]},
        "secret template in launch": {**step, "launch": "echo {{secret.tok}}"},
        "bad size": {**step, "size": "wide"},
    }
    for name, s in bad.items():
        r = drive("tmux", s, *tmux_args("m9"))
        check(f"tmux control: {name} is refused (exit 2)", r.rc == 2 and server_gone("m9"), (r.rc, r.stderr))
    r = drive("tmux", {**step, "verify": [], "verify_note": "outcome is visible only on the screen; no durable state"},
              *tmux_args("m10"))
    check("tmux: an empty verify list is accepted with a verify_note and says so in the result",
          r.rc == 0 and r.res["verified_from"] == "verify_note", (r.rc, r.res, r.stderr))


def main():
    try:
        normalizer()
        renderer()
        exec_tests()
        pty_tests()
        tmux_tests()
    finally:
        leftovers = [f for f in os.listdir(SOCK_DIR)]
        for f in leftovers:
            subprocess.run([REAL_TMUX, "-S", os.path.join(SOCK_DIR, f), "kill-server"], capture_output=True)
        check("no tmux socket is left behind", leftovers == [], leftovers)
        shutil.rmtree(SOCK_DIR, ignore_errors=True)
        shutil.rmtree(WORK, ignore_errors=True)
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} controls hold")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(REPO, "capture"))
    sys.exit(main())
