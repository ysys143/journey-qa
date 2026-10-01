#!/usr/bin/env bash
# Scenario orchestration smoke test (runners/run.sh with terminal steps).
#
#   runners/selftest/orchestrate.sh                                   Part A only
#   runners/selftest/orchestrate.sh --engine playwright --pw-module PATH [--pw-channel NAME]
#   runners/selftest/orchestrate.sh --engine ego --ego-space ID       Part A and Part B
#
# Part A (terminal-only scenarios, no browser engine; needs tmux 3.2+, node, python3, rsvg-convert)
# is what check.sh runs; a missing tool fails it. Part B (terminal and browser steps in one scenario
# against the invented demo app) runs only when engine options are given and is otherwise reported as
# SKIPPED, never as passed.
# Exit 0 only when every control that ran holds.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
command -v tmux > /dev/null || { echo "tmux not found: install tmux 3.2 or newer (tmux steps and this test need it)" >&2; exit 2; }
command -v node > /dev/null || { echo "node not found: the runners need Node.js 18+" >&2; exit 2; }
command -v rsvg-convert > /dev/null || { echo "rsvg-convert not found: terminal shots need it" >&2; exit 2; }
exec python3 "$HERE/orchestrate.py" "$@"
