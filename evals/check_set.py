#!/usr/bin/env python3
"""Validate evals/trigger-queries.json shape and the SKILL.md description. Exit 0 ok, 1 invalid, 2 cannot run.
Usage: check_set.py [--set FILE] [--skill FILE]"""
import json, re, sys, os
here = os.path.dirname(os.path.abspath(__file__))
SET = os.path.join(here, "trigger-queries.json"); SKILL = os.path.join(here, "..", "SKILL.md")
args = sys.argv[1:]
while args:
    if args[0] == "--set" and len(args) > 1: SET = args[1]; args = args[2:]
    elif args[0] == "--skill" and len(args) > 1: SKILL = args[1]; args = args[2:]
    else: print("bad args"); sys.exit(2)
import json, re, sys
try:
    items = json.load(open(SET, encoding="utf-8"))
    m = re.search(r"^description: (.*)$", open(SKILL, encoding="utf-8").read(), re.M)
    errs = []
    if not m: errs.append("SKILL.md has no description line")
    elif len(m.group(1)) > 1024: errs.append("description is %d chars (max 1024)" % len(m.group(1)))
    elif "Not for:" not in m.group(1): errs.append("description has no 'Not for:' clause")
    if not isinstance(items, list) or len(items) != 20: errs.append("need a list of 20 entries")
    else:
        for i, e in enumerate(items):
            if not isinstance(e, dict) or set(e) != {"query", "expected_route", "reason"} or not all(isinstance(v, str) and v.strip() for v in e.values()):
                errs.append("entry %d: need non-empty string fields query, expected_route, reason" % i); continue
            if e["expected_route"] not in ("journey-qa", "not journey-qa"): errs.append("entry %d: bad expected_route" % i)
        pos = [e for e in items if isinstance(e, dict) and e.get("expected_route") == "journey-qa"]
        neg = [e for e in items if isinstance(e, dict) and e.get("expected_route") == "not journey-qa"]
        if len(pos) != 10 or len(neg) != 10: errs.append("need a 10/10 split, got %d/%d" % (len(pos), len(neg)))
        ko = lambda e: bool(re.search(r"[\u3131-\u318e\uac00-\ud7a3]", e.get("query", "")))
        for name, grp in (("should-trigger", pos), ("near-miss", neg)):
            if not any(ko(e) for e in grp) or all(ko(e) for e in grp): errs.append(name + ": need both Korean and English queries")
        if len({e.get("query") for e in items if isinstance(e, dict)}) != 20: errs.append("duplicate queries")
    for x in errs: print("  " + x)
    print("eval set: %s" % ("FAIL" if errs else "ok"))
    sys.exit(1 if errs else 0)
except Exception as ex:
    print("  cannot validate: %r" % ex); sys.exit(2)
