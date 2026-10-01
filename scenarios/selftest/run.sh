#!/usr/bin/env bash
# Runs validate.py against every scenarios/selftest fixture and the shipped
# examples. pass-*.json and shipped examples must exit 0; fail-*.json must exit 1.
# Exit 0 only when every control matches and at least one control ran.
set -uo pipefail
cd "$(dirname "$0")/.."
n=0; bad=0
check() { # check <file> <expected-rc>
  local rc
  if [[ "$1" == *.yaml ]]; then uv run -q validate.py "$1" >/dev/null 2>&1; rc=$?
  else python3 validate.py "$1" >/dev/null 2>&1; rc=$?; fi
  n=$((n + 1))
  if [ "$rc" -eq "$2" ]; then echo "OK $1 rc=$rc"; else echo "MISMATCH $1 expected=$2 actual=$rc"; bad=$((bad + 1)); fi
}
extra=()
IFS=':' read -r -a _dirs <<< "${JQA_ADAPTER_DIRS:-}"
for d in ${_dirs[@]+"${_dirs[@]}"}; do [ -d "$d" ] && for f in "$d"/scenarios/*.yaml; do extra+=("$f"); done; done
for f in selftest/pass-*.json example/*.yaml ../adapters/*/scenarios/*.yaml ${extra[@]+"${extra[@]}"} ../runners/selftest/smoke*.json; do [ -f "$f" ] && check "$f" 0; done
for f in selftest/fail-*.json; do check "$f" 1; done
echo "controls: $n, mismatches: $bad"
[ "$n" -gt 0 ] && [ "$bad" -eq 0 ]
