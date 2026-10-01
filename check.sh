#!/usr/bin/env bash
# check.sh — every check this repo has, in one run. Exit 0 only if all pass.
#
#   ./check.sh                      selftests, sabotage, manifests, terminal drivers (needs tmux 3.2+),
#                                   terminal-only scenario runs, runner syntax, repo self-scan
#   JQA_ORCHESTRATE_ARGS="--engine playwright --pw-module /path/to/playwright/index.mjs" ./check.sh
#                                   also runs the mixed terminal + browser scenario against the demo app
#                                   (same options as runners/selftest/smoke.sh). Without it that part is
#                                   reported as SKIPPED
#   JQA_ADAPTER_DIRS=/path/a:/path/b ./check.sh
#                                   also check adapters kept outside this repo (for example
#                                   <product repo>/.journey-qa/adapters/<product>), in addition to
#                                   the bundled adapters/*. Same checks for each: selftest context,
#                                   sabotage, manifest (MANIFEST.sha256 inside the adapter dir),
#                                   scenario validator, runner syntax, self-scan. A listed path that
#                                   is not a directory fails the run.
#   ./check.sh --list-adapters      print the adapter directories this run would check, then exit
#   JQA_DENYLIST=/path/to/private-denylist.txt ./check.sh
#                                   also scan the repo against a private denylist of real
#                                   identifiers (kept outside this repo, never committed)
#
# Repo self-scan scope: every tracked or untracked-but-not-ignored file, except test material
# that contains deliberate violations (selftest fixtures, test denylists/rosters/controls,
# the raw sample transcript, the gate manifest that lists fixture paths). Each adapter is
# scanned with its own roster and policy. Hits listed in <adapter>/self-scan.accepted are
# known self-collisions with a written reason; an accepted hit that no longer occurs also
# fails, so the lists cannot go stale.
set -uo pipefail
CALLER_PWD="$PWD"
cd "$(dirname "$0")"

fail=0
step() { printf '\n== %s\n' "$1"; }
run() { "$@"; local rc=$?; [ "$rc" -eq 0 ] || { echo "-> FAILED (exit $rc): $*"; fail=1; }; }

# Adapter directories: the bundled adapters/* plus every JQA_ADAPTER_DIRS entry (colon-separated).
ROOT="$PWD"
adapter_dirs=() external=()
for a in adapters/*/; do [ -d "$a" ] && adapter_dirs+=("${a%/}"); done
IFS=':' read -r -a _extra <<< "${JQA_ADAPTER_DIRS:-}"
for a in ${_extra[@]+"${_extra[@]}"}; do
  [ -n "$a" ] || continue
  case "$a" in /*) ;; *) a="$CALLER_PWD/$a" ;; esac   # relative entries resolve against the caller's directory
  if [ -d "$a" ]; then adapter_dirs+=("$(cd "$a" && pwd)"); external+=("$(cd "$a" && pwd)")
  else echo "-> JQA_ADAPTER_DIRS entry is not a directory: $a"; fail=1; fi
done
if [ "${1:-}" = "--list-adapters" ]; then
  printf '%s\n' ${adapter_dirs[@]+"${adapter_dirs[@]}"}
  exit "$fail"
fi
# Path label in self-scan hits and self-scan.accepted: bundled adapters keep adapters/<name>,
# external ones are <name> (their directory name), so an accepted list reads the same wherever
# the adapter is kept.
contexts=(gates/selftest)
for d in ${adapter_dirs[@]+"${adapter_dirs[@]}"}; do [ -f "$d/selftest/context.json" ] && contexts+=("$d/selftest"); done

step "gate selftest (${contexts[*]})"
run python3 gates/selftest/run.py "${contexts[@]}"
step "gate sabotage"
run python3 gates/selftest/run.py "${contexts[@]}" --sabotage
step "manifests (gates, capture, runners, adapters)"
# Top-level prose docs and capture/sample are not frozen. Patterns match the full relative
# path, so they never exclude fixture files deeper in the tree.
MANIFEST_EXCLUDES=(--exclude SPEC.md --exclude LIMITS.md --exclude README.md --exclude data-contracts.md --exclude 'findings-*.md' --exclude 'sample/*' --exclude VERIFY.md)
manifests=(gates/MANIFEST.sha256 capture/MANIFEST.sha256 runners/MANIFEST.sha256)
for a in ${adapter_dirs[@]+"${adapter_dirs[@]}"}; do manifests+=("$a/MANIFEST.sha256"); done
for m in "${manifests[@]}"; do
  if [ -f "$m" ]; then run python3 gates/manifest.py verify --manifest "$m" "${MANIFEST_EXCLUDES[@]}"
  else echo "-> missing manifest: $m (freeze it: see SKILL.md)"; fail=1; fi
done
step "scenario validator"
run env JQA_ADAPTER_DIRS="$(IFS=:; echo "${external[*]-}")" scenarios/selftest/run.sh
step "roster checker"
run fixtures/selftest/run.sh
step "runner engine selection (no browser)"
run runners/selftest/select.sh
step "terminal drivers: exec, pty, tmux, normalizer, renderer (tmux 3.2+ required; missing tmux fails)"
run runners/selftest/terminal.sh
step "scenario orchestration: terminal-only runs through run.sh with no browser engine (mixed browser part runs only with JQA_ORCHESTRATE_ARGS)"
# shellcheck disable=SC2086
run runners/selftest/orchestrate.sh ${JQA_ORCHESTRATE_ARGS:-}
step "adapter resolution and external adapter dir (temp copy outside the repo)"
run runners/selftest/adapter-dir.sh
step "runner syntax (node --check)"
if command -v node > /dev/null; then
  n=0
  while IFS= read -r f; do run node --check "$f"; n=$((n + 1)); done < <(find runners adapters ${adapter_dirs[@]+"${adapter_dirs[@]}"} -name '*.mjs' | sort -u)
  echo "modules: $n"
  [ "$n" -gt 0 ] || { echo "-> no runner modules found"; fail=1; }
else
  echo "-> node not found (runners need Node.js 18+)"; fail=1
fi

step "trigger eval set and description length"
run python3 evals/check_set.py
run evals/selftest.sh

step "repo self-scan"
denylists=(--denylist gates/selftest/denylist.test.txt)
[ -n "${JQA_DENYLIST:-}" ] && denylists+=(--denylist "$JQA_DENYLIST")
EXCLUDE='^(gates/selftest/(fixtures/|denylist\.test\.txt|roster\.test\.json|policy\.test\.json|controls\.tsv)|adapters/[^/]+/selftest/fixtures/|fixtures/selftest/|scenarios/selftest/|capture/sample/.*\.raw\.txt$|gates/MANIFEST\.sha256$)'
files="$(git -c core.quotepath=off ls-files --cached --others --exclude-standard | grep -vE "$EXCLUDE")"
hits="$(mktemp)"; trap 'rm -f "$hits"' EXIT
scan() { # scan <label> <leakscan args...> -- reads file list on stdin; exit 2 is a failure, never "no hits"
  # SCAN_CWD (default: the repo) is where the listed relative paths resolve, so hits are reported
  # with those paths. Option paths must be absolute when SCAN_CWD is set.
  local label="$1"; shift
  local list=() f rc
  while IFS= read -r f; do [ -n "$f" ] && list+=("$f"); done
  [ "${#list[@]}" -gt 0 ] || return 0
  (cd "${SCAN_CWD:-$ROOT}" && python3 "$ROOT/gates/leakscan.py" "$@" --skip-images "${list[@]}") >> "$hits"; rc=$?
  [ "$rc" -le 1 ] || { echo "-> leakscan could not run on $label (exit $rc)"; fail=1; }
}
scan core --roster fixtures/roster.example.json "${denylists[@]}" < <(printf '%s\n' "$files" | grep -v '^adapters/[^/]*/')
for a in ${adapter_dirs[@]+"${adapter_dirs[@]}"}; do
  case "$a" in
    /*) # external: scan every file except selftest fixtures; paths are reported as <dir name>/<file>
        name="$(basename "$a")"
        roster="$a/roster.json"; [ -f "$roster" ] || roster="$ROOT/fixtures/roster.example.json"
        policy=(); [ -f "$a/policy.json" ] && policy=(--policy "$a/policy.json")
        dl=(--denylist "$ROOT/gates/selftest/denylist.test.txt"); [ -n "${JQA_DENYLIST:-}" ] && dl+=(--denylist "$JQA_DENYLIST")
        SCAN_CWD="$(dirname "$a")" scan "$a" --roster "$roster" "${dl[@]}" ${policy[@]+"${policy[@]}"} \
          < <(cd "$(dirname "$a")" && find "$name" -type f ! -name .DS_Store ! -path "$name/selftest/fixtures/*" ! -path "$name/.git/*" | sort) ;;
    *)  roster="$a/roster.json"; [ -f "$roster" ] || roster=fixtures/roster.example.json
        policy=(); [ -f "$a/policy.json" ] && policy=(--policy "$a/policy.json")
        scan "$a" --roster "$roster" "${denylists[@]}" ${policy[@]+"${policy[@]}"} < <(printf '%s\n' "$files" | grep "^$a/") ;;
  esac
done
accepted_files=(); for a in ${adapter_dirs[@]+"${adapter_dirs[@]}"}; do [ -f "$a/self-scan.accepted" ] && accepted_files+=("$a/self-scan.accepted"); done
accepted=""
[ "${#accepted_files[@]}" -gt 0 ] && accepted="$(cat "${accepted_files[@]}" | grep -vE '^\s*(#|$)' | sed 's/[[:space:]]*#.*$//' | sort -u)"
found="$(sort -u "$hits")"
unexpected="$(comm -23 <(printf '%s\n' "$found" | sed '/^$/d') <(printf '%s\n' "$accepted" | sed '/^$/d'))"
stale="$(comm -13 <(printf '%s\n' "$found" | sed '/^$/d') <(printf '%s\n' "$accepted" | sed '/^$/d'))"
echo "hits: $(printf '%s\n' "$found" | sed '/^$/d' | wc -l | tr -d ' '), accepted: $(printf '%s\n' "$accepted" | sed '/^$/d' | wc -l | tr -d ' ')"
[ -z "$unexpected" ] || { echo "-> unexpected hits:"; printf '   %s\n' "$unexpected"; fail=1; }
[ -z "$stale" ] || { echo "-> accepted hits that no longer occur (update the adapter's self-scan.accepted):"; printf '   %s\n' "$stale"; fail=1; }

printf '\n== result: %s\n' "$([ "$fail" -eq 0 ] && echo PASS || echo FAIL)"
exit "$fail"
