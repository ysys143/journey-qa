#!/usr/bin/env bash
# Controls for evals/check_set.py: the real set passes; each broken variant must fail with exit 1.
set -uo pipefail
cd "$(dirname "$0")"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
fail=0
expect() { local want="$1" name="$2"; shift 2; "$@" > /dev/null 2>&1; local rc=$?
  if [ "$rc" -eq "$want" ]; then echo "OK $name rc=$rc"; else echo "-> FAILED $name: want rc=$want got rc=$rc"; fail=1; fi; }
mut() { python3 - "$1" "$2" <<'PY'
import json, sys
d = json.load(open("trigger-queries.json", encoding="utf-8")); k = sys.argv[1]
if k == "short": d = d[:19]
elif k == "split": d[0]["expected_route"] = "not journey-qa"
elif k == "noko": d = [dict(e, query="english only %d" % i) for i, e in enumerate(d)]
elif k == "field": del d[3]["reason"]
elif k == "dup": d[1]["query"] = d[0]["query"]
json.dump(d, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False)
PY
}
expect 0 "real set passes" python3 check_set.py
for k in short split noko field dup; do mut $k "$T/$k.json"; expect 1 "broken set ($k) fails" python3 check_set.py --set "$T/$k.json"; done
printf -- '---\nname: x\ndescription: %s\n---\n' "$(python3 -c 'print("a"*1025+" Not for: x")')" > "$T/long.md"
expect 1 "description over 1024 fails" python3 check_set.py --skill "$T/long.md"
printf -- '---\nname: x\ndescription: short with no boundary\n---\n' > "$T/nonot.md"
expect 1 "description without Not for fails" python3 check_set.py --skill "$T/nonot.md"
expect 2 "missing set is exit 2" python3 check_set.py --set "$T/none.json"
exit "$fail"
