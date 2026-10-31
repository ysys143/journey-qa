#!/usr/bin/env bash
# Controls for runners/explore/merge_findings.py (independent persona sessions): the passing run,
# its variants, and a must-fail twin for every rule. Exit 0 only when every control holds.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$HERE/merge_controls.py"
