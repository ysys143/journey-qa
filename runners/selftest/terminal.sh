#!/usr/bin/env bash
# Terminal driver smoke tests (exec, pty, tmux), the transcript normalizer and the terminal
# renderer. Needs tmux 3.2+ and Python 3; check.sh runs it and a missing tmux fails the check.
# No product is involved: every target is a fake program under runners/selftest/terminal/.
# Exit 0 only when every control holds; 1 a control failed; 2 cannot run.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
command -v tmux > /dev/null || { echo "tmux not found: install tmux 3.2 or newer (tmux steps and this test need it)" >&2; exit 2; }
command -v python3 > /dev/null || { echo "python3 not found" >&2; exit 2; }
exec python3 "$HERE/terminal_smoke.py"
