#!/usr/bin/env python3
"""exec_driver.py: run one non-interactive command and judge it by exit code.

Usage:
  exec_driver.py --step STEP.json --out DIR [common options]

Step fields: id, cmd (string, required), expect_exit (int, default 0), stdout_contains
([strings], checked on the normalized and redacted output), timeout_s (ceiling, default 300),
capture_as ({name, pattern}: a value extracted from the output into the run secrets and the
run denylist before any transcript is written).

There is no tty and no secret input: a secret never goes through argv or the environment,
so a {{secret.*}} template in cmd is rejected (exit 2). With --wrapper the whole command
reaches the remote shell as one argument: <wrapper> bash -lc '<cmd>'.

Transcript: "$ <cmd>", the output (stdout and stderr in arrival order), "exit=<n>" or
"status=timeout". Raw, normalized, redacted and gated as described in common.py.
Exit: 0 passed, 1 failed, 2 cannot run.
"""

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import os  # noqa: E402
import signal  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as K  # noqa: E402


def run(argv=None):
    ap = argparse.ArgumentParser(description="Run one command; judge by exit code.")
    K.add_common_args(ap)
    args = ap.parse_args(argv)
    step = K.read_step(args.step)
    ctx = K.Context(args)
    started = K.now_ms()
    cmd = step.get("cmd")
    if not isinstance(cmd, str) or not cmd.strip():
        raise K.CannotRun("exec step needs cmd (a non-empty string)")
    expect_exit = step.get("expect_exit", 0)
    contains = step.get("stdout_contains") or []
    timeout = float(step.get("timeout_s", 300))
    if not isinstance(expect_exit, int) or timeout <= 0:
        raise K.CannotRun("expect_exit must be an integer and timeout_s positive")
    cmd = ctx.template(cmd, "cmd")
    capture_as = step.get("capture_as")
    argv = ctx.remote_argv(cmd)

    status, code = "done", None
    with tempfile.TemporaryFile() as out:
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        except OSError as exc:
            raise K.CannotRun(f"cannot start {argv[0]}: {exc.strerror}") from None
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            status = "timeout"
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            proc.wait()
        if code is not None and code < 0:
            code = 128 - code  # killed by a signal: shell convention
        out.seek(0)
        body = out.read()
    trailer = f"exit={code}" if status == "done" else f"status={status}"
    info = K.record(ctx, step["id"], cmd, body, trailer, capture_as)
    ok, problems = K.evaluate(status, code, expect_exit, contains, info)
    K.write_result(ctx, step, "exec", status, code, started, ok, problems, info)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(K.run_cli(run))
