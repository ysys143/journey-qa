#!/usr/bin/env python3
"""pty_driver.py: run a short interactive program under a real tty and answer its prompts.

Usage:
  pty_driver.py --step STEP.json --out DIR [common options]

Step fields: id, cmd (required), dialog (ordered list of {wait_for: <regex>, send: <text>,
secret: bool}), expect_exit (int, default 0), stdout_contains, timeout_s (ceiling for each
prompt and for the whole step, default 60 per prompt and 300 overall when absent),
capture_as.

An answer is written only after its wait_for matches the output seen since the previous
answer (never pre-typed). send may use {{persona.<id>.<field>}}. A secret answer is exactly
{{secret.<name>}}: the value is read from a mode 600 file into memory, written to the pty
master and never to argv, the environment or any file. The transcript shows the terminal's
own echo, or "[secret:<name>]" when the program turned echo off; every secret is also
replaced byte-wise before anything is written, and the written files are scanned for the
values afterwards (count must be 0).

Transcript: "$ <cmd>", normalized terminal output, "exit=<n>" or "status=timeout".
Exit: 0 passed, 1 failed (wrong exit code, dialog not satisfied, timeout, gate), 2 cannot run.
"""

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import errno  # noqa: E402
import fcntl  # noqa: E402
import os  # noqa: E402
import pty  # noqa: E402
import re  # noqa: E402
import select  # noqa: E402
import signal  # noqa: E402
import struct  # noqa: E402
import termios  # noqa: E402
import time  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as K  # noqa: E402
from normalize import normalize  # noqa: E402

SECRET_ANSWER = re.compile(r"^\{\{\s*secret\.([A-Za-z0-9_-]+)\s*\}\}$")


def parse_dialog(ctx, dialog):
    items = []
    if not isinstance(dialog, list):
        raise K.CannotRun("dialog must be a list")
    for i, d in enumerate(dialog):
        if not isinstance(d, dict) or not isinstance(d.get("wait_for"), str) or not isinstance(d.get("send"), str):
            raise K.CannotRun(f"dialog[{i}] needs wait_for (regex) and send (text)")
        try:
            rx = re.compile(d["wait_for"], re.M)
        except re.error as exc:
            raise K.CannotRun(f"dialog[{i}].wait_for does not compile: {exc}") from None
        m = SECRET_ANSWER.match(d["send"])
        if d.get("secret"):
            if not m:
                raise K.CannotRun(f"dialog[{i}]: a secret answer must be exactly {{{{secret.<name>}}}}")
            if ctx.secret_classes.get(m.group(1)) == "real":
                raise K.CannotRun(f"dialog[{i}]: secret {m.group(1)} is class real; a real credential is entered by a human, not a driver")
            items.append((rx, ctx.secret(m.group(1)), m.group(1)))
        else:
            if "{{secret." in d["send"].replace(" ", ""):
                raise K.CannotRun(f"dialog[{i}]: a {{{{secret.*}}}} answer needs secret: true")
            items.append((rx, ctx.template(d["send"], f"dialog[{i}].send"), None))
    return items


def echo_on(fd):
    try:
        return bool(termios.tcgetattr(fd)[3] & termios.ECHO)
    except termios.error:
        return True


def run(argv=None):
    ap = argparse.ArgumentParser(description="Run an interactive program under a pty.")
    K.add_common_args(ap)
    args = ap.parse_args(argv)
    step = K.read_step(args.step)
    ctx = K.Context(args)
    started = K.now_ms()
    cmd = step.get("cmd")
    if not isinstance(cmd, str) or not cmd.strip():
        raise K.CannotRun("pty step needs cmd (a non-empty string)")
    expect_exit = step.get("expect_exit", 0)
    contains = step.get("stdout_contains") or []
    overall = float(step.get("timeout_s", 300))
    per_prompt = float(step.get("timeout_s", 60))
    if not isinstance(expect_exit, int) or overall <= 0:
        raise K.CannotRun("expect_exit must be an integer and timeout_s positive")
    ctx.secret_classes = K.secret_classes(step)
    dialog = parse_dialog(ctx, step.get("dialog", []))
    cmd = ctx.template(cmd, "cmd")
    argv_run = ctx.remote_argv(cmd)

    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR")}
    env.update(TERM="xterm", NO_COLOR="1")
    pid, master = pty.fork()
    if pid == 0:  # child: exec inside the new session with the pty as controlling tty
        try:
            os.execvpe(argv_run[0], argv_run, env)
        except OSError:
            os._exit(127)
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 200, 0, 0))

    buf = bytearray()
    synthetic = []  # (offset in buf, text) markers for secret answers typed with echo off
    status, code, reason = "done", None, None
    deadline = time.monotonic() + overall
    prompt_deadline = time.monotonic() + per_prompt
    idx, offset, text, dirty = 0, 0, "", False
    eof = False
    try:
        while True:
            now = time.monotonic()
            if now > deadline or (idx < len(dialog) and now > prompt_deadline):
                status = "timeout"
                reason = f"waiting for dialog[{idx}]" if idx < len(dialog) else "process did not exit"
                break
            ready, _, _ = select.select([master], [], [], 0.05)
            if ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError as exc:
                    if exc.errno != errno.EIO:
                        raise
                    chunk = b""
                if not chunk:
                    eof = True
                else:
                    buf.extend(chunk)
                    dirty = True
            if dirty:
                text, dirty = _view(buf, synthetic), False
            while idx < len(dialog):
                rx, value, name = dialog[idx]
                m = rx.search(text, offset)
                if not m:
                    break
                offset = m.end()
                answer = value.encode("utf-8") + b"\n"
                if name is not None and not echo_on(master):
                    synthetic.append((len(buf), f"[secret:{name}]"))
                    dirty = True
                os.write(master, answer)
                idx += 1
                prompt_deadline = time.monotonic() + per_prompt
            if eof:
                break
        if status == "done":
            _, wst = os.waitpid(pid, 0)
            pid = None
            code = os.waitstatus_to_exitcode(wst)
            code = code if code >= 0 else 128 - code
            if idx < len(dialog):
                reason = f"process exited before dialog[{idx}] matched"
    finally:
        if pid:
            try:
                os.killpg(pid, signal.SIGKILL)
            except OSError:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            try:
                _, wst = os.waitpid(pid, 0)
            except OSError:
                pass
        os.close(master)
    body = _compose(buf, synthetic)
    trailer = f"exit={code}" if status == "done" else f"status={status}"
    info = K.record(ctx, step["id"], cmd, body, trailer, step.get("capture_as"))
    ok, problems = K.evaluate(status, code, expect_exit, contains, info)
    if reason:
        problems.insert(0, reason)
        ok = False
    K.write_result(ctx, step, "pty", status, code, started, ok, problems, info)
    return 0 if ok else 1


def _compose(buf, synthetic):
    """Raw bytes with the secret-answer markers inserted where they were typed."""
    out, last = bytearray(), 0
    for off, marker in synthetic:
        out += buf[last:off] + marker.encode("utf-8")
        last = off
    return bytes(out + buf[last:])


def _view(buf, synthetic):
    return normalize(_compose(buf, synthetic))


if __name__ == "__main__":
    sys.exit(K.run_cli(run))
