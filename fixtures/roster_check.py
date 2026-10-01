#!/usr/bin/env python3
"""Validate a journey-qa roster (fixtures/roster.schema.json).

Usage:
    roster_check.py ROSTER.json [--people N] [--admins N] [--admin-role ROLE]
                    [--allowed-roles admin,user] [--allowed-plans PROVIDER=v1,v2 ...]
                    [--denylist FILE ...]

Rules (each failure prints one line "RULE <detail>" and never prints denylist entries):
  SHAPE          required fields present, ids/handles match their patterns
  COUNT          --people and --admins, when given. --admins counts people whose role is
                 privileged: --admin-role when given, else the roster's privileged_roles
                 (default ["admin"])
  ROLE           role in --allowed-roles, when given
  UNIQUE         person id, handle and email unique; account_id unique across the roster
  EMAIL_DOMAIN   every email and login_email on a reserved domain
                 (example.com/.org/.net or a subdomain, or a .test/.example/.invalid/.localhost TLD)
  ACCOUNT_UUID   account_id is a lowercase UUID
  PER_PROVIDER   if a person holds external accounts (optional accounts[]), uniqueness holds
                 per provider: at most one account per person per provider, and per provider
                 login_email -> account_id is 1:1 (a person may hold several providers)
  PLAN           account plan is a stored-shape value from --allowed-plans for its provider
  TEAM           person team is listed in teams[] when teams[] exists
  PROJECT        project owner exists; a working directory (optional projects[].cwd) lives
                 under /home/<handle>/ or /Users/<handle>/
  DENYLIST       no roster string contains a denylist entry (case-insensitive, NFKC)

Exit: 0 all rules pass, 1 any failure, 2 usage error or unreadable input.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

RESERVED_DOMAINS = ("example.com", "example.org", "example.net")
RESERVED_TLDS = ("test", "example", "invalid", "localhost")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
HANDLE_RE = re.compile(r"^[a-z][a-z0-9_.-]*$")


def reserved_email(addr: str) -> bool:
    if addr.count("@") != 1:
        return False
    domain = addr.rsplit("@", 1)[1].lower()
    if any(domain == d or domain.endswith("." + d) for d in RESERVED_DOMAINS):
        return True
    return domain.rsplit(".", 1)[-1] in RESERVED_TLDS


def norm(s: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", s).casefold().split())


def roster_strings(roster: dict):
    for p in roster.get("people", []):
        for k in ("id", "name", "email", "handle", "team"):
            if isinstance(p.get(k), str):
                yield f"people[{p.get('id')}].{k}", p[k]
        for a in p.get("accounts", []) or []:
            if isinstance(a.get("login_email"), str):
                yield f"people[{p.get('id')}].accounts.login_email", a["login_email"]
    for pr in roster.get("projects", []) or []:
        for k in ("name", "repo", "cwd"):
            if isinstance(pr.get(k), str):
                yield f"projects[{pr.get('repo')}].{k}", pr[k]
        for b in pr.get("branches", []) or []:
            yield f"projects[{pr.get('repo')}].branch", b


def check(roster: dict, args, denylists: list[tuple[str, list[tuple[int, str]]]]) -> list[str]:
    out: list[str] = []
    people = roster.get("people")
    if not isinstance(people, list) or not people:
        return ["SHAPE people[] missing or empty"]

    allowed_roles = set(args.allowed_roles.split(",")) if args.allowed_roles else None
    plans: dict[str, set[str]] = {}
    for spec in args.allowed_plans or []:
        prov, _, vals = spec.partition("=")
        plans[prov] = set(v for v in vals.split(",") if v)

    seen = defaultdict(set)
    handles = {}
    admins = 0
    if args.admin_role:
        privileged = {args.admin_role}
    else:
        pr = roster.get("privileged_roles", ["admin"])
        if not isinstance(pr, list) or not pr or not all(isinstance(r, str) and r for r in pr):
            out.append("SHAPE privileged_roles must be a non-empty list of strings")
            pr = ["admin"]
        privileged = set(pr)
    acct_ids: set[str] = set()
    login_to_acct: dict[tuple[str, str], str] = {}
    acct_to_login: dict[str, str] = {}

    for i, p in enumerate(people):
        pid = p.get("id", f"#{i}")
        for k in ("id", "name", "email", "handle", "role"):
            if not isinstance(p.get(k), str) or not p.get(k):
                out.append(f"SHAPE people[{pid}] missing {k}")
        if isinstance(p.get("id"), str) and not ID_RE.match(p["id"]):
            out.append(f"SHAPE people[{pid}].id pattern")
        if isinstance(p.get("handle"), str) and not HANDLE_RE.match(p["handle"]):
            out.append(f"SHAPE people[{pid}].handle pattern")
        for k in ("id", "handle", "email"):
            v = p.get(k)
            if isinstance(v, str):
                key = v.lower()
                if key in seen[k]:
                    out.append(f"UNIQUE people[{pid}].{k} duplicated")
                seen[k].add(key)
        if isinstance(p.get("handle"), str):
            handles[pid] = p["handle"]
        if p.get("role") in privileged:
            admins += 1
        if allowed_roles is not None and p.get("role") not in allowed_roles:
            out.append(f"ROLE people[{pid}].role not in --allowed-roles")
        if isinstance(p.get("email"), str) and not reserved_email(p["email"]):
            out.append(f"EMAIL_DOMAIN people[{pid}].email")
        if roster.get("teams") is not None and p.get("team") not in roster.get("teams", []):
            out.append(f"TEAM people[{pid}].team not in teams[]")

        providers_seen: set[str] = set()
        for j, a in enumerate(p.get("accounts", []) or []):
            where = f"people[{pid}].accounts[{j}]"
            prov, aid, login = a.get("provider"), a.get("account_id"), a.get("login_email")
            if not prov or not aid:
                out.append(f"SHAPE {where} missing provider/account_id")
                continue
            if not UUID_RE.match(aid):
                out.append(f"ACCOUNT_UUID {where}.account_id")
            if aid in acct_ids:
                out.append(f"UNIQUE {where}.account_id duplicated")
            acct_ids.add(aid)
            if prov in providers_seen:
                out.append(f"PER_PROVIDER {where} second account for provider {prov}")
            providers_seen.add(prov)
            if login is not None:
                if not reserved_email(login):
                    out.append(f"EMAIL_DOMAIN {where}.login_email")
                key = (prov, login.lower())
                if key in login_to_acct and login_to_acct[key] != aid:
                    out.append(f"PER_PROVIDER {where} login_email maps to two account_ids for {prov}")
                login_to_acct[key] = aid
                if aid in acct_to_login and acct_to_login[aid] != login.lower():
                    out.append(f"PER_PROVIDER {where} account_id maps to two login_emails")
                acct_to_login[aid] = login.lower()
            if prov in plans and a.get("plan") not in plans[prov]:
                out.append(f"PLAN {where}.plan not a stored-shape value for {prov}")

    if args.people is not None and len(people) != args.people:
        out.append(f"COUNT people={len(people)} expected={args.people}")
    if args.admins is not None and admins != args.admins:
        out.append(f"COUNT admins={admins} expected={args.admins}")

    ids = {p.get("id") for p in people}
    for k, pr in enumerate(roster.get("projects", []) or []):
        owner = pr.get("owner")
        if owner not in ids:
            out.append(f"PROJECT projects[{k}].owner unknown")
            continue
        cwd = pr.get("cwd")
        h = handles.get(owner)
        if cwd and h and not (cwd.startswith(f"/home/{h}/") or cwd.startswith(f"/Users/{h}/")):
            out.append(f"PROJECT projects[{k}].cwd not under the owner's home")

    for dl_name, entries in denylists:
        normed = [(n, norm(e)) for n, e in entries]
        for where, value in roster_strings(roster):
            nv = norm(value)
            for lineno, entry in normed:
                if entry and entry in nv:
                    out.append(f"DENYLIST {where} matches {dl_name}#{lineno}")
    return out


def load_denylist(path: Path) -> list[tuple[int, str]]:
    entries = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        s = line.strip()
        if s and not s.startswith("#") and len(s) >= 4:
            entries.append((n, s))
    return entries


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Validate a journey-qa roster.")
    ap.add_argument("roster", type=Path)
    ap.add_argument("--people", type=int)
    ap.add_argument("--admins", type=int)
    ap.add_argument("--admin-role", help="role counted by --admins (default: roster privileged_roles)")
    ap.add_argument("--allowed-roles")
    ap.add_argument("--allowed-plans", action="append", metavar="PROVIDER=v1,v2")
    ap.add_argument("--denylist", action="append", type=Path, default=[])
    args = ap.parse_args(argv)
    try:
        roster = json.loads(args.roster.read_text(encoding="utf-8"))
        denylists = [(d.name, load_denylist(d)) for d in args.denylist]
    except (OSError, json.JSONDecodeError) as exc:
        print(f"roster_check: cannot read input ({exc.__class__.__name__})", file=sys.stderr)
        return 2
    problems = check(roster, args, denylists)
    for line in problems:
        print(line)
    if not problems:
        print("roster_check: all rules passed")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
