#!/usr/bin/env python3
"""Merge the findings of independent persona sessions (docs/workflow.md, "Independent persona sessions").

Usage:
    merge_findings.py --run RUN_DIR [--roster ROSTER.json] [--expect ID[,ID...]]
                      [--out FILE] [--markdown FILE]

Reads RUN_DIR/personas/<persona-id>/session.json and findings.json of every persona session
(templates/persona-session.schema.json, templates/persona-findings.schema.json), checks that the
sessions were independent, removes duplicates across personas by a stable key, and records for each
defect how many personas hit it and which ones (its reach), with each persona's own evidence.

Key of a finding: class | detail | journey | locus, each casefolded and trimmed; a locus also loses its
query string and trailing slash. Two sessions that never talk to each other arrive at the same key
because every part of it comes from material they share: the detail code (templates/issue-draft.md),
the journey id, and the public docs location (file#anchor) or product route. The same defect found
twice by one persona counts once for that persona and its evidence lists are joined.

Rules (each failure prints one line "RULE detail"; nothing is written when any fails):
  SHAPE      a session.json or findings.json does not fit its schema
  PERSONA    the persona named inside a file, or session.inputs.profile, differs from the directory;
             a persona is not in --roster; a persona in --expect has no session directory, or a
             session directory is not in --expect
  ISOLATION  two sessions share a session_id or a browser_profile; two sessions share an instance and
             one of them gives no shared_instance_reason; evidence points outside its own persona
             directory (an absolute path, "..", another persona's directory, a link that leaves it)
  SECRETS    evidence points into the persona's secrets/ directory (raw transcripts, never exported)
  EVIDENCE   a piece of evidence that does not exist in the persona directory

Output: RUN_DIR/merged-findings.json (or --out) and RUN_DIR/merged-findings.md (or --markdown).
Defects are ordered by reach (most personas first), then class, then key; ids D-01... follow that order
and are provisional until the report assigns its own. `near` lists defects that share a journey and a
locus but differ in class or detail: probably one defect filed under two names, for the judge to decide.
Exit: 0 merged, 1 a rule failed, 2 usage error or unreadable input.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # importing scenarios/validate.py must not leave a cache in the repo

import argparse  # noqa: E402
import importlib.util  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
from collections import defaultdict  # noqa: E402
from pathlib import Path, PurePosixPath  # noqa: E402

REPO = Path(__file__).resolve().parent.parent.parent
CLASS_ORDER = ["product", "docs", "ux", "infra", "procedure"]


def load_check_schema():
    spec = importlib.util.spec_from_file_location("jqa_validate", REPO / "scenarios" / "validate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.check_schema


def norm(text: str) -> str:
    return " ".join(str(text).casefold().split())


def norm_locus(text: str) -> str:
    return re.sub(r"\?[^#]*", "", norm(text)).rstrip("/")


def key_of(f: dict) -> str:
    return "|".join([norm(f["class"]), norm(f["detail"]), norm(f["journey"]), norm_locus(f["locus"])])


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def evidence_problem(rel: str, pdir: Path) -> str | None:
    """The rule an evidence path breaks (ISOLATION, SECRETS, EVIDENCE), or None."""
    p = PurePosixPath(rel.replace("\\", "/"))
    if p.is_absolute() or re.match(r"^[A-Za-z]:", rel) or ".." in p.parts or p.parts[:1] == ("personas",):
        return "ISOLATION"
    if p.parts[:1] == ("secrets",):
        return "SECRETS"
    target = (pdir / rel).resolve()
    if pdir.resolve() not in target.parents:
        return "ISOLATION" if target.exists() else "EVIDENCE"
    return None if target.is_file() else "EVIDENCE"


def merge(run: Path, roster: dict | None, expect: list[str] | None, check_schema, schemas: dict):
    problems: list[str] = []
    dirs = sorted(d for d in (run / "personas").iterdir() if d.is_dir())
    ids = [d.name for d in dirs]
    if expect is not None:
        problems += [f"PERSONA {pid} is expected and has no session directory" for pid in expect if pid not in ids]
        problems += [f"PERSONA {pid} has a session directory and is not in --expect" for pid in ids if pid not in expect]
    people = {p.get("id"): p for p in roster.get("people", [])} if roster else None
    sessions: dict[str, dict] = {}
    found: dict[str, dict[str, dict]] = {}  # key -> persona -> entry
    for d in dirs:
        pid = d.name
        if people is not None and pid not in people:
            problems.append(f"PERSONA {pid} is not a person in the roster")
        docs, shape_ok = {}, True
        for name in ("session", "findings"):
            try:
                docs[name] = read_json(d / f"{name}.json")
            except (OSError, ValueError):
                raise SystemExit(f"merge_findings: cannot read {pid}/{name}.json")
            errs: list[str] = []
            check_schema(docs[name], schemas[name], f"{pid}/{name}.json", errs)
            problems += [f"SHAPE {e}" for e in errs]
            shape_ok = shape_ok and not errs
            if not errs and docs[name].get("persona") != pid:
                problems.append(f"PERSONA {pid}/{name}.json names a different persona than its directory")
        if not shape_ok:
            continue
        if docs["session"]["inputs"]["profile"] != pid:
            problems.append(f"PERSONA {pid}/session.json was given another person's profile")
        sessions[pid] = docs["session"]
        for f in docs["findings"]["findings"]:
            whys = {evidence_problem(rel, d) for rel in f["evidence"]} - {None}
            for rel in f["evidence"]:
                why = evidence_problem(rel, d)
                if why:
                    problems.append(f"{why} {pid}: evidence path {rel!r} " + {"ISOLATION": "leaves the persona directory", "SECRETS": "points into secrets/", "EVIDENCE": "does not exist"}[why])
            if whys:
                continue
            ev = sorted({f"personas/{pid}/{rel}" for rel in f["evidence"]})
            mine = found.setdefault(key_of(f), {})
            if pid in mine:
                mine[pid]["evidence"] = sorted(set(mine[pid]["evidence"]) | set(ev))
                mine[pid]["perspective"] = sorted(set(mine[pid]["perspective"]) | set(f.get("perspective", [])))
                mine[pid]["hits"] += 1
            else:
                mine[pid] = {"title": f["title"], "step": f.get("step"), "perspective": sorted(set(f.get("perspective", []))), "evidence": ev, "hits": 1,
                             "_f": {"class": norm(f["class"]), "detail": f["detail"].strip(), "journey": f["journey"].strip(), "locus": norm_locus(f["locus"])}}

    # Independence of the sessions.
    for field in ("session_id", "browser_profile"):
        owners: dict[str, list[str]] = defaultdict(list)
        for pid, s in sessions.items():
            owners[s[field]].append(pid)
        problems += [f"ISOLATION {', '.join(p)} share a {field}" for _, p in sorted(owners.items()) if len(p) > 1]
    by_instance: dict[str, list[str]] = defaultdict(list)
    for pid, s in sessions.items():
        by_instance[s["instance"]].append(pid)
    shared = []
    for inst, pids in sorted(by_instance.items()):
        if len(pids) < 2:
            continue
        lacking = [p for p in pids if not str(sessions[p].get("shared_instance_reason", "")).strip()]
        if lacking:
            problems.append(f"ISOLATION {', '.join(pids)} share an instance and {', '.join(lacking)} give no shared_instance_reason")
        else:
            shared.append({"instance": inst, "personas": pids, "reasons": {p: sessions[p]["shared_instance_reason"] for p in pids}})
    if problems:
        return None, problems

    n = len(sessions)
    defects = []
    for k, per in found.items():
        first = per[sorted(per)[0]]
        defects.append({
            "key": k, **first["_f"], "title": first["title"],
            "reach": len(per), "of": n, "personas": sorted(per), "not_reached": sorted(set(sessions) - set(per)),
            "per_persona": {pid: {kk: vv for kk, vv in e.items() if not kk.startswith("_")} for pid, e in sorted(per.items())},
        })
    order = {c: i for i, c in enumerate(CLASS_ORDER)}
    defects.sort(key=lambda x: (-x["reach"], order.get(x["class"], len(order)), x["key"]))
    for i, x in enumerate(defects, 1):
        x["id"] = f"D-{i:02d}"
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for x in defects:
        groups[(x["journey"], x["locus"])].append(x)
    near = [{"journey": j, "locus": loc, "ids": [x["id"] for x in xs], "keys": [x["key"] for x in xs]}
            for (j, loc), xs in sorted(groups.items()) if len(xs) > 1]
    personas = []
    for pid in sorted(sessions):
        entry = {"id": pid, "session_id": sessions[pid]["session_id"], "browser_profile": sessions[pid]["browser_profile"], "instance": sessions[pid]["instance"]}
        if people is not None:
            profile = people[pid].get("persona") or {}
            entry["perspective"] = {k: profile[k] for k in ("experience", "device", "assistive_tech", "reads_docs", "language") if k in profile}
        entry["defects"] = sum(1 for x in defects if pid in x["personas"])
        personas.append(entry)
    return {"persona_count": n, "personas": personas, "shared_instances": shared, "defects": defects, "near": near}, []


def markdown(m: dict) -> str:
    n = m["persona_count"]
    ids = [p["id"] for p in m["personas"]]
    esc = lambda t: str(t).replace("|", "\\|")
    out = ["# Merged findings", "", f"{len(m['defects'])} defects from {n} independent persona sessions.", "",
           "| ID | Class | Detail | Title | Journey | Locus | Reach | Personas |", "|---|---|---|---|---|---|---|---|"]
    for x in m["defects"]:
        out.append(f"| {x['id']} | {x['class']} | {x['detail']} | {esc(x['title'])} | {x['journey']} | `{esc(x['locus'])}` | {x['reach']}/{n} | {', '.join(x['personas'])} |")
    out += ["", "## Reach matrix", "", "| ID | " + " | ".join(ids) + " |", "|---|" + "---|" * len(ids)]
    for x in m["defects"]:
        out.append(f"| {x['id']} | " + " | ".join("hit" if p in x["personas"] else "-" for p in ids) + " |")
    out += ["", "## Evidence per persona", ""]
    for x in m["defects"]:
        out.append(f"- {x['id']}")
        out += [f"  - {pid}: " + ", ".join(f"`{p}`" for p in e["evidence"]) for pid, e in x["per_persona"].items()]
    if m["near"]:
        out += ["", "## Same journey and locus, different key", "", "One defect under two names, or two defects? The judge decides.", ""]
        out += [f"- {', '.join(g['ids'])} at `{esc(g['locus'])}` ({g['journey']})" for g in m["near"]]
    if m["shared_instances"]:
        out += ["", "## Shared instances", ""]
        out += [f"- {s['instance']}: {', '.join(s['personas'])}" for s in m["shared_instances"]]
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Merge the findings of independent persona sessions.")
    ap.add_argument("--run", required=True, type=Path)
    ap.add_argument("--roster", type=Path)
    ap.add_argument("--expect")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--markdown", type=Path)
    args = ap.parse_args(argv)
    try:
        schemas = {n: read_json(REPO / "templates" / f"persona-{n}.schema.json") for n in ("session", "findings")}
        check_schema = load_check_schema()
        roster = read_json(args.roster) if args.roster else None
        if not (args.run / "personas").is_dir():
            raise OSError("no personas directory")
    except (OSError, ValueError) as exc:
        print(f"merge_findings: cannot read input ({exc.__class__.__name__})", file=sys.stderr)
        return 2
    expect = [x for x in args.expect.split(",") if x] if args.expect else None
    try:
        merged, problems = merge(args.run, roster, expect, check_schema, schemas)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 2
    for line in problems:
        print(line)
    if problems:
        return 1
    (args.out or args.run / "merged-findings.json").write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    (args.markdown or args.run / "merged-findings.md").write_text(markdown(merged), encoding="utf-8")
    print(f"merge_findings: {len(merged['defects'])} defects from {merged['persona_count']} personas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
