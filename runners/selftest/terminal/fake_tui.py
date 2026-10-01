#!/usr/bin/env python3
"""Invented full-screen program for the tmux driver smoke test.

Main screen: a coloured banner, a progress line that overwrites itself with carriage returns,
optionally a "Token:" prompt (--ask-secret echo-off|echo-on) whose answer is only hashed.
Alternate screen: a menu whose default choice is "Exit"; Down moves to "Run task", Enter selects.
After leaving the alternate screen it prints a done marker and writes the state file
(choice=..., token_sha256=...), then exits with --exit-code.
  fake_tui.py STATE_FILE [--ask-secret MODE] [--blocked] [--exit-code N] [--hang]
"""
import hashlib
import os
import sys
import termios
import time


def out(text):
    sys.stdout.write(text)
    sys.stdout.flush()


state_file = sys.argv[1]
args = sys.argv[2:]
mode = args[args.index("--ask-secret") + 1] if "--ask-secret" in args else None
exit_code = int(args[args.index("--exit-code") + 1]) if "--exit-code" in args else 0

if "--blocked" in args:
    out("ACTION BLOCKED BY POLICY LAYER: the command was denied\n")
    time.sleep(60)
    sys.exit(9)

out("\x1b[32mtask-runner\x1b[0m starting\n")
for pct in (10, 40, 100):
    out(f"\rloading {pct}%")
    time.sleep(0.03)
out("\n")

token_hash = ""
if mode:
    out("Token: ")
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    if mode == "echo-off":
        new = termios.tcgetattr(fd)
        new[3] &= ~termios.ECHO
        termios.tcsetattr(fd, termios.TCSADRAIN, new)
    line = sys.stdin.readline().rstrip("\n")
    termios.tcsetattr(fd, termios.TCSADRAIN, old)
    token_hash = hashlib.sha256(line.encode()).hexdigest()
    out("\ntoken received\n")

if "--hang" in args:
    time.sleep(60)

items = ["Exit", "Run task", "Cancel"]
sel = 0
out("\x1b[?1049h\x1b[2J")
fd = sys.stdin.fileno()
old = termios.tcgetattr(fd)
raw = termios.tcgetattr(fd)
raw[3] &= ~(termios.ECHO | termios.ICANON)
raw[6][termios.VMIN], raw[6][termios.VTIME] = 1, 0
termios.tcsetattr(fd, termios.TCSADRAIN, raw)
try:
    while True:
        out("\x1b[H\x1b[2K\x1b[1mSelect an action:\x1b[0m\n")
        for i, name in enumerate(items):
            out("\x1b[2K" + ("\x1b[7m> " if i == sel else "  ") + name + "\x1b[0m\n")
        data = os.read(fd, 16)
        if data in (b"\x1b[B", b"\x1bOB"):
            sel = min(sel + 1, len(items) - 1)
        elif data in (b"\x1b[A", b"\x1bOA"):
            sel = max(sel - 1, 0)
        elif data in (b"\r", b"\n"):
            break
    if items[sel] == "Run task":
        for pct in range(0, 101, 25):
            out(f"\x1b[6;1H\x1b[2Kprogress {pct}%")
            time.sleep(0.03)
finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, old)
    out("\x1b[?1049l")

choice = "run" if items[sel] == "Run task" else "exit"
with open(state_file, "w") as fh:
    fh.write(f"choice={choice}\ntoken_sha256={token_hash}\n")
out("TASK-COMPLETE-MARKER\n" if choice == "run" else "exited without running\n")
sys.exit(exit_code)
