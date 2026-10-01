#!/usr/bin/env bash
# Runner smoke test against the invented demo app in app/ (no product, no network).
#
#   runners/selftest/smoke.sh --engine playwright --pw-module PATH [--pw-channel NAME]
#   runners/selftest/smoke.sh --engine ego --ego-space ID
#
# Five runs: a clean pass, the same again compared with the first run's shots
# as baselines, an injected failure whose evidence must be kept only after
# redaction and gating, the same failure with evidence that cannot pass, and a
# run with login state injected from the first run. Needs a browser for the
# chosen engine, so check.sh does not run it.
# Exit 0 only when every expectation holds.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
ENGINE_ARGS=("$@")
ENGINE=""
for ((i = 0; i < ${#ENGINE_ARGS[@]}; i++)); do [ "${ENGINE_ARGS[$i]}" = --engine ] && ENGINE="${ENGINE_ARGS[$((i + 1))]}"; done
[ -n "$ENGINE" ] || { echo "usage: $0 --engine playwright|ego [engine options]" >&2; exit 2; }

WORK="$(mktemp -d)"
PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
python3 -m http.server "$PORT" --bind 127.0.0.1 --directory "$HERE/app" > "$WORK/http.log" 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null; rm -rf "$WORK"' EXIT
for _ in $(seq 50); do curl -fsS "http://127.0.0.1:$PORT/login/" > /dev/null 2>&1 && break; sleep 0.1; done

fail=0
expect() { # expect <label> <wanted rc> <actual rc>
  if [ "$2" -eq "$3" ]; then echo "OK $1 rc=$3"; else echo "MISMATCH $1 expected=$2 actual=$3"; fail=1; fi
}
check() { # check <label> <python expression over r (results.json) and out (run dir)>
  if python3 - "$1" "$2" "$3" <<'PY'
import json, os, sys
label, expr, out = sys.argv[1:]
r = json.load(open(os.path.join(out, "results.json")))
sys.exit(0 if eval(expr) else 1)
PY
  then echo "OK $1"; else echo "MISMATCH $1"; fail=1; fi
}
run() { # run <out> <scenario> [extra args]
  local out="$1" scenario="$2"; shift 2
  "$REPO/runners/run.sh" "${ENGINE_ARGS[@]}" --scenario "$HERE/$scenario" --out "$WORK/$out" \
    --hooks "$HERE/hooks.mjs" --denylist "$REPO/gates/selftest/denylist.test.txt" \
    --base "app=http://127.0.0.1:$PORT" "$@" > "$WORK/$out.log" 2>&1
}

run pass smoke.json; expect pass 0 $?
check pass-shots "len(r['captures']) == 4 and all(os.path.exists(os.path.join(out, c['file'] + '.txt')) for c in r['captures'])" "$WORK/pass"
check pass-assertions "r['passed'] and not r['uncheckedAssertions']" "$WORK/pass"
python3 "$REPO/gates/png_meta.py" "$WORK/pass/shots" > /dev/null; expect pass-png-meta 0 $?
check pass-secret-masked "'\u2022' * 12 in open(os.path.join(out, 'shots', '02-code.png.txt'), encoding='utf-8').read()" "$WORK/pass"

mkdir -p "$WORK/baselines/$ENGINE/smoke" && cp "$WORK/pass/shots/"*.png "$WORK/pass/shots/"*.masks.json "$WORK/baselines/$ENGINE/smoke/"
run again smoke.json --baselines "$WORK/baselines"; expect baseline-same-engine 0 $?

run drill smoke.json --inject-failure show-code; expect drill-fails 1 $?
check drill-evidence-kept "r['failedStep'] == 'show-code' and r['steps'][-1].get('skipped') and [s for s in r['steps'] if s['id'] == 'show-code'][0].get('evidenceKept') is True" "$WORK/drill"
# Rescan what was kept, archives unpacked: it must pass on its own, without the runner's word for it.
cp -R "$WORK/drill/failure" "$WORK/kept" && for z in $(find "$WORK/kept" -name '*.zip'); do
  mkdir "$z.d" && (cd "$z.d" && unzip -q "$z") && rm "$z"
done
python3 "$REPO/gates/leakscan.py" --roster "$REPO/fixtures/roster.example.json" --denylist "$REPO/gates/selftest/denylist.test.txt" \
  --denylist "$WORK/drill/secrets/run-denylist.txt" --skip-images "$WORK/kept" > /dev/null; expect drill-evidence-rescan 0 $?
[ -z "$(find "$WORK/kept" -name '*.stacks' -o -name '*.webm' -o -name '*.jpeg')" ]; expect drill-evidence-dropped 0 $?

# Evidence that cannot pass: the failing page shows a denylisted name, so evidence.py must delete it.
printf 'Ivy Nonrealton\n' > "$WORK/deny-ivy.txt"
run drill-deny smoke.json --inject-failure show-code --denylist "$WORK/deny-ivy.txt"; expect drill-deny-fails 1 $?
check drill-deny-evidence-discarded "[s for s in r['steps'] if s['id'] == 'show-code'][0].get('evidenceKept') is False and not os.path.exists(os.path.join(out, 'failure', 'show-code'))" "$WORK/drill-deny"

run injected smoke-injected.json --state-from "$WORK/pass/secrets"; expect injected 0 $?
check injected-state "r['notes'].get('injectedState') == ['ivy', 'marcus']" "$WORK/injected"

if [ "$fail" -ne 0 ]; then
  for f in "$WORK"/*.log; do echo "--- $(basename "$f")"; tail -15 "$f"; done
fi
echo "smoke: $([ "$fail" -eq 0 ] && echo PASS || echo FAIL)"
exit "$fail"
