#!/usr/bin/env python3
"""Controls for runners/explore/merge_findings.py. Builds a small run directory with three independent
persona sessions, merges it, and checks the result; every rule has a must-fail twin that changes one
thing in the passing run. Prints "OK <label>" or "MISMATCH <label>"; exit 0 only when every control holds."""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
MERGE = REPO / "runners" / "explore" / "merge_findings.py"
ROSTER = REPO / "fixtures" / "roster.example.json"


def finding(cls, detail, locus, title, evidence, **extra):
    return {"class": cls, "detail": detail, "journey": "first-run", "locus": locus, "title": title, "evidence": evidence, **extra}


def base_run() -> dict:
    """persona -> {session, findings, files}. ivy, marcus and tomas hit one shared defect; two of them a second; each one more."""
    shared = ("docs", "DOC-MISSING-STEP")
    return {
        "ivy": {
            "session": {"persona": "ivy", "session_id": "s-ivy", "browser_profile": "bp-ivy", "instance": "inst-ivy", "journeys": ["first-run"],
                        "inputs": {"profile": "ivy", "docs": "docs/index.md"}},
            "findings": [
                finding(*shared, "docs/install.md#configure", "Config file is never created", ["evidence/01-config.txt"], step="configure"),
                finding("product", "PROD-LABEL", "/settings/team", "Team page shows the project name", ["evidence/02-team.txt", "shots/02-team.png"]),
            ],
            "files": ["evidence/01-config.txt", "evidence/02-team.txt", "shots/02-team.png"],
        },
        "marcus": {
            "session": {"persona": "marcus", "session_id": "s-marcus", "browser_profile": "bp-marcus", "instance": "inst-marcus", "journeys": ["first-run"],
                        "inputs": {"profile": "marcus", "docs": "docs/index.md"}},
            "findings": [
                finding(*shared, "DOCS/Install.md#Configure?plan=b", "No step creates the config", ["evidence/01-config.txt"]),
                finding("product", "PROD-LABEL", "/settings/team/", "Wrong label on the team page", ["evidence/02-team.txt"]),
                finding("ux", "UX-TRUNCATE", "/home", "Email is cut off in the header", ["evidence/03-header.txt"]),
            ],
            "files": ["evidence/01-config.txt", "evidence/02-team.txt", "evidence/03-header.txt"],
        },
        "tomas": {
            "session": {"persona": "tomas", "session_id": "s-tomas", "browser_profile": "bp-tomas", "instance": "inst-tomas", "journeys": ["first-run"],
                        "inputs": {"profile": "tomas", "docs": "docs/index.md"}},
            "findings": [
                finding(*shared, "docs/install.md#configure  ", "Config missing", ["evidence/01-config.txt"]),
                finding(*shared, "docs/install.md#configure", "Config missing, second time", ["evidence/09-retry.txt"]),
                finding("ux", "UX-BARRIER", "/home#menu", "The menu cannot be opened with the keyboard", ["evidence/04-menu.txt"], perspective=["assistive_tech:keyboard-only"]),
            ],
            "files": ["evidence/01-config.txt", "evidence/09-retry.txt", "evidence/04-menu.txt"],
        },
    }


def write_run(root: Path, run: dict) -> Path:
    for pid, p in run.items():
        d = root / "personas" / pid
        d.mkdir(parents=True)
        (d / "session.json").write_text(json.dumps(p["session"]))
        (d / "findings.json").write_text(json.dumps({"persona": pid, "findings": p["findings"]}))
        for rel in p["files"]:
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_text("recorded output\n")
    return root


n = bad = 0


def merge(run: dict, *extra: str, raw_edit=None):
    tmp = Path(tempfile.mkdtemp(prefix="jqa-merge-"))
    try:
        write_run(tmp, run)
        if raw_edit:
            raw_edit(tmp)
        proc = subprocess.run([sys.executable, str(MERGE), "--run", str(tmp), *extra], capture_output=True, text=True)
        out = tmp / "merged-findings.json"
        merged = json.loads(out.read_text()) if out.exists() else None
        md = (tmp / "merged-findings.md").read_text() if (tmp / "merged-findings.md").exists() else None
        return proc.returncode, proc.stdout + proc.stderr, merged, md
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def control(label, cond, detail=""):
    global n, bad
    n += 1
    if cond:
        print(f"OK {label}")
    else:
        bad += 1
        print(f"MISMATCH {label} {detail}")


def must_fail(label, rule, run, *extra, raw_edit=None, rc=1):
    code, out, merged, _ = merge(run, *extra, raw_edit=raw_edit)
    control(f"MUST FAIL {label}", code == rc and merged is None and (rule is None or any(l.startswith(rule + " ") for l in out.splitlines())), f"rc={code} out={out.strip()[:200]!r}")


# ---- the passing run
code, out, merged, md = merge(base_run(), "--roster", str(ROSTER), "--expect", "ivy,marcus,tomas")
control("merge: the passing run merges", code == 0 and merged is not None, out)
if merged:
    d = {x["detail"]: x for x in merged["defects"]}
    control("merge: three defects of reach 3, 2, 1, 1 in that order", [x["reach"] for x in merged["defects"]] == [3, 2, 1, 1], str([x["reach"] for x in merged["defects"]]))
    control("merge: ids follow the order", [x["id"] for x in merged["defects"]] == ["D-01", "D-02", "D-03", "D-04"])
    control("merge: case, spaces, query and trailing slash do not split a defect", d["DOC-MISSING-STEP"]["personas"] == ["ivy", "marcus", "tomas"] and d["PROD-LABEL"]["personas"] == ["ivy", "marcus"])
    control("merge: the same defect met twice by one persona counts once for it", d["DOC-MISSING-STEP"]["reach"] == 3 and d["DOC-MISSING-STEP"]["per_persona"]["tomas"]["hits"] == 2)
    control("merge: that persona's evidence from both hits is kept", d["DOC-MISSING-STEP"]["per_persona"]["tomas"]["evidence"] == ["personas/tomas/evidence/01-config.txt", "personas/tomas/evidence/09-retry.txt"])
    control("merge: per-persona evidence stays under its own persona directory", all(p.startswith(f"personas/{pid}/") for x in merged["defects"] for pid, e in x["per_persona"].items() for p in e["evidence"]))
    control("merge: not_reached lists the personas that did not hit it", d["UX-TRUNCATE"]["not_reached"] == ["ivy", "tomas"] and d["UX-TRUNCATE"]["of"] == 3)
    control("merge: a perspective note survives", d["UX-BARRIER"]["per_persona"]["tomas"]["perspective"] == ["assistive_tech:keyboard-only"])
    control("merge: the roster perspective is attached to each persona", [p["perspective"]["device"] for p in merged["personas"]] == ["desktop", "desktop", "tablet"], str(merged["personas"]))
    control("merge: no shared instance, no near-duplicate", merged["shared_instances"] == [] and merged["near"] == [])
    control("merge: the markdown has the reach and the matrix", md is not None and "| D-01 |" in md and "3/3" in md and "## Reach matrix" in md)
code2, _, merged2, md2 = merge(base_run(), "--roster", str(ROSTER), "--expect", "ivy,marcus,tomas")
r = base_run(); r["ivy"]["findings"][0]["title"] = "Config | never created"
_, _, _, md3 = merge(r)
control("merge: a pipe in a title does not break the markdown table", md3 is not None and "Config \\| never created" in md3)
control("merge: the same input gives the same bytes", merged == merged2 and md == md2)

# ---- must-pass variants
r = base_run()
r["ivy"]["session"]["instance"] = r["marcus"]["session"]["instance"] = "inst-shared"
r["ivy"]["session"]["shared_instance_reason"] = r["marcus"]["session"]["shared_instance_reason"] = "the product allows one install per host"
code, out, merged, _ = merge(r)
control("merge: a shared instance with a reason on every sharer is allowed and listed", code == 0 and merged["shared_instances"] and merged["shared_instances"][0]["personas"] == ["ivy", "marcus"], out)

r = base_run()
r["marcus"]["findings"].append(finding("docs", "DOC-MISMATCH", "docs/install.md#configure", "Same place, other code", ["evidence/01-config.txt"]))
code, out, merged, _ = merge(r)
control("merge: same journey and locus under another detail is reported as near, not merged", code == 0 and len(merged["near"]) == 1 and len(merged["near"][0]["ids"]) == 2, out)

# ---- must-fail twins, one change each
r = base_run(); r["marcus"]["session"]["session_id"] = "s-ivy"
must_fail("two personas in one agent session", "ISOLATION", r)
r = base_run(); r["marcus"]["session"]["browser_profile"] = "bp-ivy"
must_fail("two personas on one browser profile", "ISOLATION", r)
r = base_run(); r["marcus"]["session"]["instance"] = "inst-ivy"
must_fail("a shared instance with no reason", "ISOLATION", r)
r = base_run(); r["marcus"]["session"]["instance"] = "inst-ivy"; r["marcus"]["session"]["shared_instance_reason"] = "one install per host"
must_fail("a shared instance where only one sharer gives a reason", "ISOLATION", r)
r = base_run(); r["ivy"]["findings"][0]["evidence"] = ["../marcus/evidence/01-config.txt"]
must_fail("evidence that climbs out of the persona directory", "ISOLATION", r)
r = base_run(); r["ivy"]["findings"][0]["evidence"] = ["personas/marcus/evidence/01-config.txt"]
must_fail("evidence that names another persona's directory", "ISOLATION", r)
r = base_run(); r["ivy"]["findings"][0]["evidence"] = ["/tmp/outside.txt"]
must_fail("evidence with an absolute path", "ISOLATION", r)
r = base_run(); r["ivy"]["findings"][0]["evidence"] = ["evidence/link.txt"]
def link_out(tmp):
    os.symlink(tmp / "personas" / "marcus" / "evidence" / "01-config.txt", tmp / "personas" / "ivy" / "evidence" / "link.txt")
must_fail("evidence that is a link into another persona's directory", "ISOLATION", r, raw_edit=link_out)
r = base_run(); r["ivy"]["findings"][0]["evidence"] = ["secrets/raw-transcript.txt"]; r["ivy"]["files"].append("secrets/raw-transcript.txt")
must_fail("evidence that points into secrets/", "SECRETS", r)
r = base_run(); r["ivy"]["findings"][0]["evidence"] = ["evidence/missing.txt"]
must_fail("evidence that does not exist", "EVIDENCE", r)
r = base_run(); r["ivy"]["findings"][0]["evidence"] = []
must_fail("a finding with no evidence", "SHAPE", r)
r = base_run(); r["ivy"]["findings"][0]["class"] = "cosmetic"
must_fail("an unknown class", "SHAPE", r)
r = base_run(); r["ivy"]["findings"][0]["detail"] = "missing step"
must_fail("a detail that is not a code", "SHAPE", r)
r = base_run(); del r["ivy"]["session"]["browser_profile"]
must_fail("a session without a browser profile", "SHAPE", r)
r = base_run(); r["ivy"]["session"]["browser_profile"] = "profiles/ivy"
must_fail("a browser profile given as a path", "SHAPE", r)
r = base_run(); r["ivy"]["session"]["inputs"]["profile"] = "marcus"
must_fail("a session built from another person's profile", "PERSONA", r)
r = base_run(); r["ivy"]["findings"][0]["unknown_field"] = 1
must_fail("a finding field the schema does not know", "SHAPE", r)
r = base_run()
def swap(tmp):
    f = tmp / "personas" / "ivy" / "findings.json"
    doc = json.loads(f.read_text()); doc["persona"] = "marcus"; f.write_text(json.dumps(doc))
must_fail("findings that name another persona than their directory", "PERSONA", r, raw_edit=swap)
must_fail("a persona that is expected and has no session", "PERSONA", base_run(), "--expect", "ivy,marcus,tomas,yuki")
must_fail("a session that was not expected", "PERSONA", base_run(), "--expect", "ivy,marcus")
r = base_run(); r["priya-typo"] = copy.deepcopy(r["tomas"]); r["priya-typo"]["session"].update(persona="priya-typo", session_id="s-x", browser_profile="bp-x", instance="inst-x"); r["priya-typo"]["session"]["inputs"]["profile"] = "priya-typo"
r["priya-typo"]["findings"] = []; r["priya-typo"]["files"] = []
must_fail("a persona that is not in the roster", "PERSONA", r, "--roster", str(ROSTER))
def corrupt(tmp):
    (tmp / "personas" / "ivy" / "findings.json").write_text("{not json")
must_fail("a findings file that is not JSON (cannot run)", None, base_run(), raw_edit=corrupt, rc=2)
def no_session(tmp):
    (tmp / "personas" / "ivy" / "session.json").unlink()
must_fail("a session directory without session.json (cannot run)", None, base_run(), raw_edit=no_session, rc=2)
must_fail("a run directory with no personas (cannot run)", None, {}, rc=2)

print(f"controls: {n}, mismatches: {bad}")
sys.exit(0 if n > 0 and bad == 0 else 1)
