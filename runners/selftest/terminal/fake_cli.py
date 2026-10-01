#!/usr/bin/env python3
"""Invented interactive program for the pty driver smoke test.

Asks for a server URL, a password and a y/n confirmation, prints coloured output, a progress
line that overwrites itself with carriage returns and one line wider than a terminal.
A password whose sha256 differs from argv[1] ends the program with exit 3.
  fake_cli.py EXPECTED_SHA256 [--echo]     --echo reads the password with the terminal echo on
"""
import getpass
import hashlib
import sys

expected = sys.argv[1]
echo = "--echo" in sys.argv[2:]
url = input("Server URL: ")
password = input("Password: ") if echo else getpass.getpass("Password: ")
if hashlib.sha256(password.encode()).hexdigest() != expected:
    print("\x1b[31mauthentication failed\x1b[0m")
    sys.exit(3)
answer = input("Continue? [y/n] ")
for pct in (10, 50, 100):
    sys.stdout.write(f"\rprogress {pct}%")
    sys.stdout.flush()
print()
print("\x1b[32mconnected to " + url + "\x1b[0m")
print("wide-line:" + "0123456789" * 24)
print("ready" if answer.strip().lower() == "y" else "cancelled")
