#!/usr/bin/env bash
# Controls for roster_check.py. pass-* and the shipped example must exit 0;
# fail-* must exit 1; the example checked against a denylist that names one of
# its own invented people must exit 1 (proves the DENYLIST rule reads values).
set -uo pipefail
cd "$(dirname "$0")/.."
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
echo "controls: $n, mismatches: $bad"
[ "$n" -gt 0 ] && [ "$bad" -eq 0 ]
