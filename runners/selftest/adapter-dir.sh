#!/usr/bin/env bash
# Adapter resolution selftest (no browser). check.sh runs it.
#
# 1. gates/adapter_dir.py: the search order (explicit > project > bundled), fail-closed cases.
# 2. An adapter copied to a temp directory OUTSIDE the repo passes the gate selftest, its
#    manifest and the scenario validator through the same machinery check.sh uses
#    (JQA_ADAPTER_DIRS); runners/run.sh resolves it through --adapter.
# Each must-pass case has a must-fail twin (broken copy, missing dir, tampered manifest).
# Exit 0 only when every case holds.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
WORK="$(cd "$WORK" && pwd -P)"
case "$WORK" in "$REPO"/*) echo "temp dir is inside the repo: $WORK" >&2; exit 2 ;; esac
RES="$REPO/gates/adapter_dir.py"
DENY="$REPO/gates/selftest/denylist.test.txt"
EXCL=(--exclude SPEC.md --exclude LIMITS.md --exclude README.md --exclude data-contracts.md --exclude 'findings-*.md' --exclude 'sample/*' --exclude VERIFY.md)
fail=0
expect() { # expect <label> <want rc> <got rc>
  if [ "$2" -eq "$3" ]; then echo "OK $1 rc=$3"; else echo "MISMATCH $1 want rc=$2 got rc=$3"; fail=1; fi
}
expect_out() { # expect_out <label> <want> <got>
  if [ "$2" = "$3" ]; then echo "OK $1"; else echo "MISMATCH $1 want=[$2] got=[$3]"; fail=1; fi
}
unset JQA_ADAPTER_DIR

# 1. Resolution order.
PROJ="$WORK/proj"
mkdir -p "$PROJ/.journey-qa/adapters/example-app" "$WORK/explicit" "$WORK/other/.journey-qa/adapters/solo"
got="$(python3 "$RES" --product example-app --project "$PROJ")"
expect_out "project beats bundled" "$PROJ/.journey-qa/adapters/example-app" "$got"
got="$(python3 "$RES" --product example-app --project "$WORK/nowhere")"
expect_out "bundled is last" "$REPO/adapters/example-app" "$got"
got="$(python3 "$RES" --product example-app --project "$PROJ" --adapter "$WORK/explicit")"
expect_out "--adapter beats project" "$WORK/explicit" "$got"
got="$(JQA_ADAPTER_DIR="$WORK/explicit" python3 "$RES" --product example-app --project "$PROJ")"
expect_out "JQA_ADAPTER_DIR beats project" "$WORK/explicit" "$got"
got="$(JQA_ADAPTER_DIR="$WORK/other" python3 "$RES" --product example-app --adapter "$WORK/explicit")"
expect_out "--adapter beats JQA_ADAPTER_DIR" "$WORK/explicit" "$got"
got="$(python3 "$RES" --product solo --project "$WORK/other")"
expect_out "project-only adapter" "$WORK/other/.journey-qa/adapters/solo" "$got"
got="$(cd "$PROJ" && python3 "$RES" --product example-app)"
expect_out "project defaults to the current directory" "$PROJ/.journey-qa/adapters/example-app" "$got"
python3 "$RES" --product no-such-product --project "$PROJ" > /dev/null 2>&1; expect "unknown product fails" 2 $?
python3 "$RES" --product example-app --adapter "$WORK/missing" > /dev/null 2>&1; expect "missing --adapter dir does not fall through" 2 $?
JQA_ADAPTER_DIR="$WORK/missing" python3 "$RES" --product example-app > /dev/null 2>&1; expect "missing JQA_ADAPTER_DIR does not fall through" 2 $?
python3 "$RES" --product ../adapters --project "$PROJ" > /dev/null 2>&1; expect "path traversal in product name fails" 2 $?
python3 "$RES" > /dev/null 2>&1; expect "nothing named fails" 2 $?

# 2. External copy, outside the repo, through the same commands check.sh and SKILL.md use.
COPY="$WORK/ext/example-app"; mkdir -p "$WORK/ext"; cp -R "$REPO/adapters/example-app" "$COPY"
out="$(cd "$WORK" && JQA_ADAPTER_DIRS="$COPY" "$REPO/check.sh" --list-adapters)"; rc=$?
expect "check.sh --list-adapters accepts the copy" 0 $rc
case "$out" in *"$COPY"*) echo "OK copy is listed" ;; *) echo "MISMATCH copy not listed: [$out]"; fail=1 ;; esac
JQA_ADAPTER_DIRS="$WORK/missing" "$REPO/check.sh" --list-adapters > /dev/null 2>&1; expect "check.sh rejects a missing adapter dir" 1 $?
python3 "$REPO/gates/selftest/run.py" "$REPO/gates/selftest" "$COPY/selftest" > /dev/null 2>&1; expect "selftest of the external copy" 0 $?
python3 "$REPO/gates/selftest/run.py" "$REPO/gates/selftest" "$COPY/selftest" --sabotage > /dev/null 2>&1; expect "sabotage with the external copy" 0 $?
python3 "$REPO/gates/manifest.py" verify --manifest "$COPY/MANIFEST.sha256" "${EXCL[@]}" > /dev/null 2>&1; expect "manifest of the external copy" 0 $?
for f in "$COPY"/scenarios/*.yaml; do
  uv run -q "$REPO/scenarios/validate.py" "$f" > /dev/null 2>&1; expect "scenario $(basename "$f") of the external copy" 0 $?
done

# run.sh resolves a scenario by name inside the adapter. Without an engine space it stops at the
# engine prerequisite, which is after resolution and scenario validation.
"$REPO/runners/run.sh" --engine ego --adapter "$COPY" --scenario nope --out "$WORK/o1" --denylist "$DENY" > /dev/null 2>&1
expect "run.sh: unknown scenario name fails" 2 $?
"$REPO/runners/run.sh" --engine ego --adapter "$WORK/missing" --scenario x --out "$WORK/o2" --denylist "$DENY" > /dev/null 2>&1
expect "run.sh: missing adapter dir fails" 2 $?
out="$("$REPO/runners/run.sh" --engine ego --adapter "$COPY" --scenario team-tour --out "$WORK/o3" --denylist "$DENY" 2>&1)"
case "$out" in *"--ego-space is required"*) echo "OK run.sh found team-tour inside the external adapter" ;; *) echo "MISMATCH run.sh did not reach the engine prerequisite: [$out]"; fail=1 ;; esac

# Must-fail twins: a tampered file (manifest) and a flipped control (selftest).
BAD="$WORK/bad/example-app"; mkdir -p "$WORK/bad"; cp -R "$COPY" "$BAD"
printf '\n' >> "$BAD/policy.json"
python3 "$REPO/gates/manifest.py" verify --manifest "$BAD/MANIFEST.sha256" "${EXCL[@]}" > /dev/null 2>&1; expect "tampered external manifest fails" 1 $?
BAD2="$WORK/bad2/example-app"; mkdir -p "$WORK/bad2"; cp -R "$COPY" "$BAD2"
python3 - "$BAD2/selftest/controls.tsv" <<'PY'
import sys
p = sys.argv[1]
lines = open(p, encoding="utf-8").read().split("\n")
for i, l in enumerate(lines[1:], 1):
    c = l.split("\t")
    if len(c) == 4 and c[3] == "FAIL":
        c[3] = "PASS"; lines[i] = "\t".join(c); break
open(p, "w", encoding="utf-8").write("\n".join(lines))
PY
python3 "$REPO/gates/selftest/run.py" "$REPO/gates/selftest" "$BAD2/selftest" > /dev/null 2>&1; expect "flipped external control fails selftest" 1 $?

echo "result: $([ "$fail" -eq 0 ] && echo PASS || echo FAIL)"
exit "$fail"
