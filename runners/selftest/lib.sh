#!/usr/bin/env bash
# Controls for the runner library that need no browser (emulation rules; the interpreter through a
# stub backend). Exit 0 only when every control holds and at least one ran.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
command -v node > /dev/null || { echo "node not found: the runners need Node.js 18+" >&2; exit 2; }
exec node "$HERE/controls.mjs"
