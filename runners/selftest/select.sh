#!/usr/bin/env bash
# Engine-selection selftest: runs `runners/run.sh --engine auto --select-only` with a stubbed
# PATH, CI variable and terminal override. No browser, no scenario. check.sh runs it.
#
# Every case pairs a context with the exit code and engine it must produce. Must-pass cases
# select the documented engine; must-fail cases (nothing usable) must exit 2 and name what to
# install. A selector that always picks one engine fails at least one case.
# Exit 0 only when every case holds.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN="$(cd "$HERE/.." && pwd)/run.sh"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

# Two PATHs: one with a stub ego-browser, one without. Both carry only what run.sh needs.
for d in with without; do
  mkdir -p "$WORK/$d"
  for t in python3 dirname; do ln -s "$(command -v "$t")" "$WORK/$d/$t"; done
done
printf '#!/bin/sh\nexit 0\n' > "$WORK/with/ego-browser"; chmod +x "$WORK/with/ego-browser"
: > "$WORK/pw-index.mjs"

fail=0
# case <label> <ego on PATH: yes|no> <CI value or -> <tty: 0|1> <pw: yes|no> <ego-space: yes|no> <want rc> <want engine or "-"> [stderr must contain]
case_() {
  local label="$1" ego="$2" ci="$3" tty="$4" pw="$5" space="$6" want_rc="$7" want_engine="$8" want_err="${9:-}"
  local path="$WORK/without" args=(--engine auto --select-only) out err rc
  [ "$ego" = yes ] && path="$WORK/with"
  [ "$pw" = yes ] && args+=(--pw-module "$WORK/pw-index.mjs")
  [ "$space" = yes ] && args+=(--ego-space 1)
  local envs=(PATH="$path" JQA_TTY="$tty")
  [ "$ci" = - ] || envs+=(CI="$ci")
  err="$WORK/err"
  out="$(env -u CI "${envs[@]}" "$(command -v bash)" "$RUN" "${args[@]}" 2> "$err")"; rc=$?
  local got="-"; case "$out" in "engine: ego "*) got=ego ;; "engine: playwright "*) got=playwright ;; esac
  local ok=1
  [ "$rc" -eq "$want_rc" ] || ok=0
  [ "$got" = "$want_engine" ] || ok=0
  [ -z "$want_err" ] || grep -qi "$want_err" "$err" || ok=0
  [ "$want_rc" -ne 0 ] || [ -n "$out" ] || ok=0
  if [ "$ok" -eq 1 ]; then echo "OK $label rc=$rc engine=$got"; else echo "MISMATCH $label want rc=$want_rc engine=$want_engine got rc=$rc engine=$got out=[$out] err=[$(cat "$err")]"; fail=1; fi
}
#     label                         ego  ci    tty pw   space rc engine      stderr
case_ "interactive, both usable"     yes  -     1   yes  yes   0  ego
case_ "interactive, ego only"        yes  -     1   no   yes   0  ego
case_ "interactive, no ego"          no   -     1   yes  yes   0  playwright
case_ "interactive, no ego space"    yes  -     1   yes  no    0  playwright
case_ "CI set, both usable"          yes  true  1   yes  yes   0  playwright
case_ "CI=1 wins over a terminal"    yes  1     1   yes  yes   0  playwright
case_ "CI=false is not CI"           yes  false 1   yes  yes   0  ego
case_ "no terminal, both usable"     yes  -     0   yes  yes   0  playwright
case_ "neither usable, interactive"  no   -     1   no   no    2  -           "install"
case_ "CI, only ego usable"          yes  true  1   no   yes   2  -           "playwright"
case_ "pw module path missing"       no   -     1   no   yes   2  -           "install"

# The reason is part of the output, and explicit engines bypass detection.
out="$(env -u CI PATH="$WORK/without" JQA_TTY=1 "$(command -v bash)" "$RUN" --engine auto --select-only --pw-module "$WORK/pw-index.mjs" 2>&1)"
case "$out" in *"engine: playwright ("*"ego-browser"*) echo "OK reason printed" ;; *) echo "MISMATCH reason printed: [$out]"; fail=1 ;; esac
out="$(env -u CI PATH="$WORK/without" JQA_TTY=1 "$(command -v bash)" "$RUN" --engine ego --select-only 2>&1)"; rc=$?
case "$out" in "engine: ego (explicit --engine ego)") [ "$rc" -eq 0 ] && echo "OK explicit ego kept" || { echo "MISMATCH explicit ego rc=$rc"; fail=1; } ;; *) echo "MISMATCH explicit ego: [$out]"; fail=1 ;; esac
env -u CI "$(command -v bash)" "$RUN" --engine bogus --select-only > /dev/null 2>&1; rc=$?
[ "$rc" -eq 2 ] && echo "OK unknown engine rc=2" || { echo "MISMATCH unknown engine rc=$rc"; fail=1; }

echo "result: $([ "$fail" -eq 0 ] && echo PASS || echo FAIL)"
exit "$fail"
