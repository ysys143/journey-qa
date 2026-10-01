#!/usr/bin/env python3
"""sql_assert.py: data assertions (gates/SPEC.md "sql_assert.py").

Usage:
  sql_assert.py --engine sqlite --db FILE  [--roster R] ASSERT.sql...
  sql_assert.py --engine psql   --dsn DSN  [--roster R] [--timeout 60s] ASSERT.sql...

An assertion file is split into blocks by "-- assert: <name>" lines. Each block
is one SELECT (or WITH ... SELECT) and passes only when it returns 0 rows.
With --roster, every block is run as
  WITH roster_identity(kind, value) AS (VALUES ...) SELECT count(*) FROM (<block>) AS q
kinds: handle, email, login_email, account_id, provider_account
("provider|account_id"), privileged_handle (handles whose role is in the
roster's privileged_roles, default ["admin"]).
sqlite opens the file read-only (URI mode=ro). psql runs BEGIN READ ONLY,
SET LOCAL statement_timeout, the count query, ROLLBACK, with
"-v ON_ERROR_STOP=1 -X -q -t -A".
Output: "ASSERT_FAIL <name> rows=<n>" per failing block. Row contents are never
printed. A file with no block, a block that is not a single SELECT, SQL text
before the first block, or any SQL error is exit 2.
Exit: 0 all pass, 1 assertion failed, 2 usage / SQL / tool error.
"""

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import sqlite3  # noqa: E402
import subprocess  # noqa: E402
import urllib.parse  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402

ASSERT_LINE = re.compile(r"^--\s*assert:\s*(\S.*?)\s*$")
TIMEOUT_RE = re.compile(r"^[0-9]+(ms|s|min)?$")


def _strip_sql_comments(sql):
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return "\n".join(line.split("--", 1)[0] for line in sql.split("\n"))


def parse_assert_file(path):
    text = C.read_text_file(path, "assert file")
    blocks, name, buf, preamble = [], None, [], []
    for line in text.split("\n"):
        m = ASSERT_LINE.match(line.strip())
        if m:
            if name is not None:
                blocks.append((name, "\n".join(buf)))
            name, buf = m.group(1), []
        elif name is None:
            preamble.append(line)
        else:
            buf.append(line)
    if name is not None:
        blocks.append((name, "\n".join(buf)))
    if _strip_sql_comments("\n".join(preamble)).strip():
        raise C.GateError(f"{path}: SQL text before the first '-- assert:' line")
    if not blocks:
        raise C.GateError(f"{path}: no '-- assert: <name>' block")
    seen = set()
    out = []
    for name, sql in blocks:
        if name in seen:
            raise C.GateError(f"{path}: duplicate assert name {name}")
        seen.add(name)
        body = sql.strip()
        while body.endswith(";"):
            body = body[:-1].rstrip()
        code = _strip_sql_comments(body).strip()
        if not code:
            raise C.GateError(f"{path}: assert {name} is empty")
        if ";" in code:
            raise C.GateError(f"{path}: assert {name} holds more than one statement")
        if not re.match(r"^(SELECT|WITH|VALUES)\b", code, re.I):
            raise C.GateError(f"{path}: assert {name} is not a SELECT")
        out.append((name, body))
    return out


def _quote(value):
    return "'" + value.replace("'", "''") + "'"


def roster_cte(roster):
    rows = []
    rows += [("handle", h) for h in roster.handles]
    rows += [("email", e) for e in roster.emails]
    rows += [("login_email", e) for e in roster.login_emails]
    rows += [("account_id", a) for a in roster.account_ids]
    rows += [("provider_account", pa) for pa in roster.provider_accounts]
    rows += [("privileged_handle", h) for h in roster.privileged_handles]
    values = ",\n  ".join(f"({_quote(k)}, {_quote(v)})" for k, v in rows)
    return f"WITH roster_identity(kind, value) AS (VALUES\n  {values}\n)\n"


def build_query(body, roster):
    prefix = roster_cte(roster) if roster is not None else ""
    return f"{prefix}SELECT count(*) FROM (\n{body}\n) AS q"


def run_sqlite(db, queries):
    if not os.path.isfile(db):
        raise C.GateError(f"database not found: {db}")
    uri = "file:" + urllib.parse.quote(db) + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise C.GateError(f"cannot open {db} read-only: {exc}") from None
    results = []
    try:
        for name, query in queries:
            try:
                (count,) = conn.execute(query).fetchone()
            except sqlite3.Error as exc:
                raise C.GateError(f"assert {name}: SQL error: {exc}") from None
            results.append((name, int(count)))
    finally:
        conn.close()
    return results


def run_psql(dsn, timeout, queries):
    if shutil.which("psql") is None:
        raise C.GateError("psql not found")
    results = []
    for name, query in queries:
        script = (
            "BEGIN READ ONLY;\n"
            f"SET LOCAL statement_timeout = {_quote(timeout)};\n"
            f"{query};\n"
            "ROLLBACK;\n"
        )
        try:
            proc = subprocess.run(
                ["psql", "-v", "ON_ERROR_STOP=1", "-X", "-q", "-t", "-A", "-d", dsn],
                input=script.encode("utf-8"), capture_output=True, timeout=3600)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise C.GateError(f"assert {name}: psql did not finish ({exc.__class__.__name__})") from None
        if proc.returncode != 0:
            msg = proc.stderr.decode("utf-8", "replace").strip().splitlines()
            raise C.GateError(f"assert {name}: psql exited {proc.returncode}: "
                              f"{msg[0] if msg else 'no message'}")
        lines = [ln.strip() for ln in proc.stdout.decode("utf-8", "replace").splitlines() if ln.strip()]
        if len(lines) != 1 or not lines[0].isdigit():
            raise C.GateError(f"assert {name}: unexpected psql output shape")
        results.append((name, int(lines[0])))
    return results


def main(argv=None):
    C.setup_stdio()
    p = argparse.ArgumentParser(description="Run zero-row SQL assertions.")
    p.add_argument("--engine", choices=("sqlite", "psql"), required=True)
    p.add_argument("--db")
    p.add_argument("--dsn")
    p.add_argument("--roster")
    p.add_argument("--timeout", default="60s")
    p.add_argument("asserts", nargs="+", metavar="ASSERT.sql")
    args = p.parse_args(argv)
    try:
        if args.engine == "sqlite" and not args.db:
            raise C.GateError("--engine sqlite needs --db")
        if args.engine == "psql" and not args.dsn:
            raise C.GateError("--engine psql needs --dsn")
        if not TIMEOUT_RE.match(args.timeout):
            raise C.GateError("--timeout must look like 60s, 500ms or 2min")
        roster = C.load_roster(args.roster) if args.roster else None
        queries = []
        for path in args.asserts:
            for name, body in parse_assert_file(path):
                queries.append((name, build_query(body, roster)))
        if args.engine == "sqlite":
            results = run_sqlite(args.db, queries)
        else:
            results = run_psql(args.dsn, args.timeout, queries)
        failed = False
        for name, count in results:
            if count != 0:
                print(f"ASSERT_FAIL {name} rows={count}")
                failed = True
        return C.EXIT_FAIL if failed else C.EXIT_PASS
    except C.GateError as exc:
        sys.stdout.flush()
        C.error(exc)
        return C.EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
