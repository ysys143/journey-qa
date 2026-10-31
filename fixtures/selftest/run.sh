#!/usr/bin/env bash
# Controls for roster_check.py. pass-* and the shipped example must exit 0;
# fail-* must exit 1; the example checked against a denylist that names one of
# its own invented people must exit 1 (proves the DENYLIST rule reads values).
# Perspective controls change one persona field of the shipped example, so a failure can only come
# from that field; every must-fail one also has to print a PERSPECTIVE (or SHAPE) line.
set -uo pipefail
cd "$(dirname "$0")/.."
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
n=0; bad=0
check() { # check <expected-rc> <label> <args...>
  local exp="$1" label="$2"; shift 2
  python3 roster_check.py "$@" >/dev/null 2>&1; local rc=$?
  n=$((n + 1))
  if [ "$rc" -eq "$exp" ]; then echo "OK $label rc=$rc"; else echo "MISMATCH $label expected=$exp actual=$rc"; bad=$((bad + 1)); fi
}
check 0 example roster.example.json --people 5 --admins 1 --allowed-roles admin,user
for f in selftest/pass-*.json; do check 0 "$f" "$f"; done
for f in selftest/fail-*.json; do check 1 "$f" "$f"; done
check 1 denylist-hit roster.example.json --denylist selftest/denylist.test.txt
check 1 wrong-count roster.example.json --people 4
check 1 display-label-plan roster.example.json --allowed-plans "provider-a=Plan Large"
check 1 admins-other-role roster.example.json --admins 1 --admin-role owner
check 0 privileged-roles selftest/privileged-owner.json --admins 1
check 1 privileged-roles-count selftest/privileged-owner.json --admins 2
mutated() { # mutated <expected-rc> <rule line expected in the output or -> <label> <python statement over p, the first persona> [roster_check args]
  local exp="$1" rule="$2" label="$3" stmt="$4" out rc
  shift 4
  python3 -c 'import json,sys; r=json.load(open("roster.example.json")); p=r["people"][0]["persona"]; exec(sys.argv[1]); json.dump(r, open(sys.argv[2],"w"))' "$stmt" "$tmp/m.json" || { echo "MISMATCH $label (mutation failed)"; bad=$((bad + 1)); n=$((n + 1)); return; }
  out="$(python3 roster_check.py "$tmp/m.json" "$@" 2>&1)"; rc=$?
  n=$((n + 1))
  if [ "$rc" -ne "$exp" ]; then echo "MISMATCH $label expected=$exp actual=$rc"; bad=$((bad + 1))
  elif [ "$rule" != - ] && ! printf '%s\n' "$out" | grep -q "^$rule "; then echo "MISMATCH $label rc=$rc but no $rule line"; bad=$((bad + 1))
  else echo "OK $label rc=$rc"; fi
}
mutated 0 - perspective-all-unset 'for k in ("experience","device","assistive_tech","reads_docs","language","goals","constraints"): p.pop(k)'
mutated 0 - perspective-empty-lists 'p["assistive_tech"]=[]; p["goals"]=[]; p["constraints"]=[]'
mutated 0 - perspective-every-assistive 'p["assistive_tech"]=["screen-reader","keyboard-only","zoom","high-contrast","reduced-motion","voice-control"]'
mutated 1 PERSPECTIVE perspective-experience-enum 'p["experience"]="guru"'
mutated 1 PERSPECTIVE perspective-device-enum 'p["device"]="watch"'
mutated 1 PERSPECTIVE perspective-reads-docs-enum 'p["reads_docs"]="sometimes"'
mutated 1 PERSPECTIVE perspective-assistive-enum 'p["assistive_tech"]=["screen-reader","telepathy"]'
mutated 1 PERSPECTIVE perspective-assistive-duplicate 'p["assistive_tech"]=["zoom","zoom"]'
mutated 1 PERSPECTIVE perspective-assistive-not-list 'p["assistive_tech"]="zoom"'
mutated 1 PERSPECTIVE perspective-language-pattern 'p["language"]="english"'
mutated 1 PERSPECTIVE perspective-language-type 'p["language"]=["en-US"]'
mutated 1 PERSPECTIVE perspective-goals-not-list 'p["goals"]="get started"'
mutated 1 PERSPECTIVE perspective-goals-empty-item 'p["goals"]=[""]'
mutated 1 PERSPECTIVE perspective-constraints-item-type 'p["constraints"]=[3]'
mutated 1 SHAPE perspective-persona-not-object 'r["people"][0]["persona"]="admin"'
printf 'Quillfeather\n' > "$tmp/deny.txt"   # an invented stand-in that no roster value contains
mutated 1 DENYLIST perspective-goal-hits-denylist 'p["goals"]=["Ask Quillfeather for a login"]' --denylist "$tmp/deny.txt"
mutated 1 DENYLIST perspective-constraint-hits-denylist 'p["constraints"]=["Needs the Quillfeather proxy"]' --denylist "$tmp/deny.txt"
mutated 0 - perspective-goal-clean-with-denylist 'p["goals"]=["Ask a colleague for a login"]' --denylist "$tmp/deny.txt"
echo "controls: $n, mismatches: $bad"
[ "$n" -gt 0 ] && [ "$bad" -eq 0 ]
