#!/usr/bin/env python3
"""selftest/run.py: prove that every gate still catches what it must catch.

Usage:
  run.py [CONTEXT_DIR ...] [--sabotage]

CONTEXT_DIR (default: this directory) holds context.json and controls.tsv.
context.json: {"roster": ..., "denylists": [...], "policies": [...],
               "sql_schema": ..., "sql_asserts": [...]}, paths relative to it. "${JQA_REPO}"
inside a path stands for this repository's root, so a context outside the repo (an adapter
kept in a product repo) can reference the shared test denylist.
controls.tsv: tab separated "control gate fixture expected" with a header line;
blank lines and lines starting with "#" are ignored.

Gate kinds: leakscan, leakscan-allow (adds <fixture>.allow as --allow-uuids),
image_scan (--ocr require), png_meta, docs_images (<fx>/docs, <fx>/README.md,
<fx>/docs/assets/screenshots, --locale-suffix .ko --locale-suffix .ja), sql_assert (temporary
sqlite DB from sql_schema + the fixture seed), redact (capture/redact.py, then
leakscan on the redacted copy), img_diff (<fx>/baseline against <fx>/actual, plus
one extra argument per line of <fx>/args.txt), strip_png (capture/strip_png.py on a
temporary copy of the fixture PNG, then png_meta on the result), evidence
(capture/evidence.py on a temporary copy of <fx>/evidence, with <fx>/secrets.txt as
--secrets when present and one extra argument per line of <fx>/args.txt).

Verdict per control: exit 0 = PASS, 1 = FAIL, anything else = ERROR. ERROR never
equals the expected value. A leakscan FAIL control named text-<rule>-... must
also report <RULE>, otherwise it is a mismatch ("FAIL(no-<RULE>)").
Extra checks (one MISMATCH line each when they fail): rule coverage (every
`leakscan --list-rules` id has an expected=FAIL control named text-<id>),
gate coverage (every gate kind has a PASS and a FAIL control), exemption
controls (every policy exemption's must_pass / must_fail control is listed in
the same controls.tsv with PASS / FAIL), error-path masking (leakscan and
image_scan given a missing target whose directory is a denylist item must exit
2 without printing that item).
Output: "<control> expected=<E> actual=<A> OK|MISMATCH" per control and
"controls: N, mismatches: M". Exit 0 only when M == 0 and N > 0.

--sabotage: for each gate script, copy gates/ and capture/ to a temporary
directory, replace that script by a stub that always exits 0, point
JQA_GATES_DIR at the copy and rerun the controls. Every gate must produce at
least one MISMATCH ("DETECTED"). Exit 0 only when the baseline run is clean and
every sabotage is detected.
Gate location: environment variable JQA_GATES_DIR (default: ..), capture tools:
JQA_CAPTURE_DIR (default: <gates>/../capture).
"""

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import concurrent.futures  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import sqlite3  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
GATE_KINDS = ("leakscan", "leakscan-allow", "image_scan", "png_meta",
              "docs_images", "sql_assert", "redact", "img_diff", "strip_png", "evidence")
LEAK_KINDS = ("leakscan", "leakscan-allow")
SABOTAGE_TARGETS = (
    ("gates/leakscan.py", ("leakscan", "leakscan-allow")),
    ("gates/image_scan.py", ("image_scan",)),
    ("gates/png_meta.py", ("png_meta",)),
    ("gates/docs_images.py", ("docs_images",)),
    ("gates/sql_assert.py", ("sql_assert",)),
    ("capture/redact.py", ("redact",)),
    ("gates/img_diff.py", ("img_diff",)),
    ("capture/strip_png.py", ("strip_png",)),
    ("capture/evidence.py", ("evidence",)),
)
STUB = "#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n"
TIMEOUT = 900


def expand(value):
    """Replace ${JQA_REPO} with this repository's root, so a context outside the repo can
    still reference the shared test material (for example the test denylist)."""
    return value.replace("${JQA_REPO}", REPO_ROOT) if isinstance(value, str) else value


class Context:
    def __init__(self, path, label):
        self.dir = path
        self.label = label
        self.problems = []
        self.controls = []
        cfg = {}
        if not os.path.isdir(path):
            self.problems.append("context directory not found")
            self.dir = HERE  # harmless cwd for subprocesses; no controls are loaded
            self.roster, self.denylists, self.policies = None, [], []
            self.sql_schema, self.sql_asserts = None, []
            return
        try:
            with open(os.path.join(path, "context.json"), encoding="utf-8") as fh:
                cfg = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            self.problems.append(f"context.json unreadable ({exc.__class__.__name__})")
        self.roster = expand(cfg.get("roster"))
        self.denylists = [expand(x) for x in cfg.get("denylists", [])]
        self.policies = [expand(x) for x in cfg.get("policies", [])]
        self.sql_schema = expand(cfg.get("sql_schema"))
        self.sql_asserts = [expand(x) for x in cfg.get("sql_asserts", [])]
        if not self.roster or not self.denylists:
            self.problems.append("context.json needs roster and denylists")
        self._load_controls()

    def _load_controls(self):
        try:
            with open(os.path.join(self.dir, "controls.tsv"), encoding="utf-8") as fh:
                lines = fh.read().split("\n")
        except OSError as exc:
            self.problems.append(f"controls.tsv unreadable ({exc.strerror})")
            return
        if not lines or lines[0].rstrip("\r").split("\t") != ["control", "gate", "fixture", "expected"]:
            self.problems.append("controls.tsv header must be: control<TAB>gate<TAB>fixture<TAB>expected")
            return
        seen = set()
        for n, line in enumerate(lines[1:], 2):
            line = line.rstrip("\r")
            if not line.strip() or line.startswith("#"):
                continue
            cols = line.split("\t")
            if len(cols) != 4:
                self.problems.append(f"controls.tsv:{n}: expected 4 tab-separated columns")
                continue
            name, gate, fixture, expected = cols
            if name in seen:
                self.problems.append(f"controls.tsv:{n}: duplicate control {name}")
            seen.add(name)
            self.controls.append({"name": name, "gate": gate, "fixture": fixture,
                                  "expected": expected})

    def path(self, rel):
        return os.path.join(self.dir, rel)

    def leak_args(self, absolute=False):
        fix = (lambda p: os.path.abspath(self.path(p))) if absolute else (lambda p: p)
        args = ["--roster", fix(self.roster)]
        for d in self.denylists:
            args += ["--denylist", fix(d)]
        for p in self.policies:
            args += ["--policy", fix(p)]
        return args

    def exemptions(self):
        out = []
        for p in self.policies:
            try:
                with open(self.path(p), encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, json.JSONDecodeError) as exc:
                self.problems.append(f"policy {p} unreadable ({exc.__class__.__name__})")
                continue
            out.extend(e for e in data.get("exemptions", []) if isinstance(e, dict))
        return out


class Runner:
    def __init__(self, gates_dir, capture_dir):
        self.gates = gates_dir
        self.capture = capture_dir
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", OMP_THREAD_LIMIT="1",
                        JQA_GATES_DIR=gates_dir, JQA_CAPTURE_DIR=capture_dir)

    def run_full(self, argv, cwd):
        try:
            proc = subprocess.run([sys.executable] + argv, cwd=cwd, env=self.env,
                                  capture_output=True, timeout=TIMEOUT)
        except (subprocess.TimeoutExpired, OSError):
            return None, "", ""
        return (proc.returncode, proc.stdout.decode("utf-8", "replace"),
                proc.stderr.decode("utf-8", "replace"))

    def run(self, argv, cwd):
        rc, out, _ = self.run_full(argv, cwd)
        return rc, out

    def masking_check(self, ctx):
        """Exit-2 error messages must not print a leaking path: point leakscan
        and image_scan at a missing target whose directory name is the first
        denylist item of the context, and require exit 2 with the item absent
        from stdout and stderr."""
        item = None
        for d in ctx.denylists:
            try:
                with open(ctx.path(d), encoding="utf-8-sig") as fh:
                    lines = fh.read().split("\n")
            except OSError:
                continue
            for line in lines:
                raw = line.strip()
                if raw and not raw.startswith("#") and len(raw) >= 4 and "/" not in raw:
                    item = raw
                    break
            if item:
                break
        if item is None:
            return [f"error-path-masking[{ctx.label}] expected=denylist-item actual=none MISMATCH"]
        problems = []
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(item, "missing-target.png")
            for gate in ("leakscan.py", "image_scan.py"):
                rc, out, err = self.run_full([self.gate(gate)] + ctx.leak_args(absolute=True)
                                             + [target], tmp)
                leaked = item.casefold() in (out + err).casefold()
                if rc != 2 or leaked:
                    problems.append(f"error-path-masking:{gate}[{ctx.label}] expected=exit2-masked "
                                    f"actual=exit{rc}{'-leaked' if leaked else ''} MISMATCH")
        return problems

    def gate(self, name):
        return os.path.join(self.gates, name)

    def list_rules(self, ctx):
        rc, out = self.run([self.gate("leakscan.py"), "--list-rules"]
                           + sum((["--policy", p] for p in ctx.policies), []), ctx.dir)
        return rc, [ln.strip() for ln in out.split("\n") if ln.strip()]

    def control(self, ctx, c):
        """Return (rc, stdout) for one control. rc None means ERROR before the gate ran."""
        gate, fx = c["gate"], c["fixture"]
        if not ctx.roster or not ctx.denylists:
            return None, "context incomplete"
        if not os.path.exists(ctx.path(fx)):
            return None, "fixture missing"
        if gate == "leakscan":
            return self.run([self.gate("leakscan.py")] + ctx.leak_args() + [fx], ctx.dir)
        if gate == "leakscan-allow":
            return self.run([self.gate("leakscan.py")] + ctx.leak_args()
                            + ["--allow-uuids", fx + ".allow", fx], ctx.dir)
        if gate == "image_scan":
            return self.run([self.gate("image_scan.py")] + ctx.leak_args()
                            + ["--ocr", "require", fx], ctx.dir)
        if gate == "png_meta":
            return self.run([self.gate("png_meta.py"), fx], ctx.dir)
        if gate == "docs_images":
            argv = [self.gate("docs_images.py"), "--pages-root", os.path.join(fx, "docs")]
            if os.path.isfile(ctx.path(os.path.join(fx, "README.md"))):
                argv += ["--page", os.path.join(fx, "README.md")]
            argv += ["--images-dir", os.path.join(fx, "docs", "assets", "screenshots"),
                     "--locale-suffix", ".ko", "--locale-suffix", ".ja"]
            return self.run(argv, ctx.dir)
        if gate == "sql_assert":
            if not ctx.sql_schema or not ctx.sql_asserts:
                return None, "context has no sql_schema / sql_asserts"
            with tempfile.TemporaryDirectory() as tmp:
                db = os.path.join(tmp, "selftest.db")
                try:
                    with open(ctx.path(ctx.sql_schema), encoding="utf-8") as fh:
                        schema = fh.read()
                    with open(ctx.path(fx), encoding="utf-8") as fh:
                        seed = fh.read()
                    conn = sqlite3.connect(db)
                    conn.executescript(schema + "\n" + seed)
                    conn.commit()
                    conn.close()
                except (OSError, sqlite3.Error) as exc:
                    return None, f"seed failed: {exc}"
                return self.run([self.gate("sql_assert.py"), "--engine", "sqlite", "--db", db,
                                 "--roster", ctx.roster] + ctx.sql_asserts, ctx.dir)
        if gate == "redact":
            rc, out = self.run([os.path.join(self.capture, "redact.py"), fx]
                               + sum((["--policy", p] for p in ctx.policies), []), ctx.dir)
            if rc != 0:
                return None, "redact failed"
            with tempfile.TemporaryDirectory() as tmp:
                name = os.path.basename(fx) + ".redacted.txt"
                with open(os.path.join(tmp, name), "w", encoding="utf-8") as fh:
                    fh.write(out)
                return self.run([self.gate("leakscan.py")] + ctx.leak_args(absolute=True)
                                + [name], tmp)
        if gate == "img_diff":
            argv = [self.gate("img_diff.py")]
            args_file = ctx.path(os.path.join(fx, "args.txt"))
            if os.path.isfile(args_file):
                with open(args_file, encoding="utf-8") as fh:
                    argv += [ln.strip() for ln in fh if ln.strip()]
            return self.run(argv + [os.path.join(fx, "baseline"), os.path.join(fx, "actual")], ctx.dir)
        if gate == "strip_png":
            with tempfile.TemporaryDirectory() as tmp:
                copy = os.path.join(tmp, os.path.basename(fx))
                shutil.copyfile(ctx.path(fx), copy)
                rc, out = self.run([os.path.join(self.capture, "strip_png.py"), copy], tmp)
                if rc not in (0, 1):
                    return None, "strip_png failed"
                return self.run([self.gate("png_meta.py"), os.path.basename(fx)], tmp)
        if gate == "evidence":
            with tempfile.TemporaryDirectory() as tmp:
                ev = os.path.join(tmp, "evidence")
                shutil.copytree(ctx.path(os.path.join(fx, "evidence")), ev)
                argv = [os.path.join(self.capture, "evidence.py"), "evidence"] + ctx.leak_args(absolute=True)
                secrets = ctx.path(os.path.join(fx, "secrets.txt"))
                if os.path.isfile(secrets):
                    shutil.copyfile(secrets, os.path.join(tmp, "secrets.txt"))
                    argv += ["--secrets", "secrets.txt"]
                args_file = ctx.path(os.path.join(fx, "args.txt"))
                if os.path.isfile(args_file):
                    with open(args_file, encoding="utf-8") as fh:
                        argv += [ln.strip() for ln in fh if ln.strip()]
                return self.run(argv + ["--ocr", "require"], tmp)
        return None, f"unknown gate {gate}"


def rule_prefix(rule):
    return "text-" + rule.lower().replace("_", "-")


def rule_for_control(name, rules):
    best = None
    for rule in rules:
        prefix = rule_prefix(rule)
        if name == prefix or name.startswith(prefix + "-"):
            if best is None or len(rule_prefix(best)) < len(prefix):
                best = rule
    return best


def reported_rules(stdout):
    out = set()
    for line in stdout.split("\n"):
        if line.strip():
            out.add(line.split(" ", 1)[0].split("[", 1)[0])
    return out


def run_all(context_dirs, gates_dir, capture_dir, verbose=True):
    """Run every control and extra check. Returns (controls, mismatches, records)."""
    runner = Runner(gates_dir, capture_dir)
    contexts = [Context(d, label) for d, label in context_dirs]
    lines, records, mismatches = [], [], 0

    rules, list_rule_problems = [], []
    for ctx in contexts:
        rc, ids = runner.list_rules(ctx)
        if rc != 0:
            list_rule_problems.append(f"rule-coverage:list-rules[{ctx.label}] expected=0 actual={rc} MISMATCH")
        for rid in ids:
            if rid not in rules:
                rules.append(rid)

    jobs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as pool:
        for ctx in contexts:
            for c in ctx.controls:
                jobs.append((ctx, c, pool.submit(runner.control, ctx, c)))
        for ctx, c, fut in jobs:
            rc, out = fut.result()
            actual = {0: "PASS", 1: "FAIL"}.get(rc, "ERROR")
            if c["gate"] in LEAK_KINDS and actual == "FAIL" and c["expected"] == "FAIL":
                rule = rule_for_control(c["name"], rules)
                if rule and rule not in reported_rules(out):
                    actual = f"FAIL(no-{rule})"
            ok = c["gate"] in GATE_KINDS and c["expected"] in ("PASS", "FAIL") and actual == c["expected"]
            mismatches += 0 if ok else 1
            records.append({"ctx": ctx.dir, "name": c["name"], "gate": c["gate"], "ok": ok})
            lines.append(f"{c['name']} expected={c['expected']} actual={actual} "
                         f"{'OK' if ok else 'MISMATCH'}")

    checks = list(list_rule_problems)
    for ctx in contexts:
        for problem in ctx.problems:
            checks.append(f"context[{ctx.label}] {problem} MISMATCH")
    all_controls = [c for ctx in contexts for c in ctx.controls]
    if not rules:
        checks.append("rule-coverage expected=rules actual=none MISMATCH")
    for rule in rules:
        if not any(c["gate"] in LEAK_KINDS and c["expected"] == "FAIL"
                   and rule_for_control(c["name"], [rule]) for c in all_controls):
            checks.append(f"rule-coverage:{rule} expected=FAIL-control actual=none MISMATCH")
    for kind in GATE_KINDS:
        for exp in ("PASS", "FAIL"):
            if not any(c["gate"] == kind and c["expected"] == exp for c in all_controls):
                checks.append(f"gate-coverage:{kind}:{exp} expected=control actual=none MISMATCH")
    for ctx in contexts:
        if ctx.roster and ctx.denylists:
            checks.extend(runner.masking_check(ctx))
    for ctx in contexts:
        by_name = {c["name"]: c["expected"] for c in ctx.controls}
        for ex in ctx.exemptions():
            controls = ex.get("controls") if isinstance(ex.get("controls"), dict) else {}
            for key, exp in (("must_pass", "PASS"), ("must_fail", "FAIL")):
                names = controls.get(key) or []
                if not names:
                    checks.append(f"exemption:{ex.get('id')}:{key} expected=control actual=none MISMATCH")
                for name in names:
                    if by_name.get(name) != exp:
                        checks.append(f"exemption:{ex.get('id')}:{name} expected={exp} "
                                      f"actual={by_name.get(name, 'missing')} MISMATCH")
    for line in checks:
        lines.append(line)
    mismatches += len(checks)
    total = len(all_controls)
    if verbose:
        for line in lines:
            print(line)
        print(f"controls: {total}, mismatches: {mismatches}")
        sys.stdout.flush()
    return total, mismatches, records


def sabotage(context_dirs, gates_dir, capture_dir):
    total, mism, _ = run_all(context_dirs, gates_dir, capture_dir, verbose=False)
    print(f"baseline controls={total} mismatches={mism} {'CLEAN' if mism == 0 and total else 'DIRTY'}")
    all_ok = mism == 0 and total > 0
    for rel, kinds in SABOTAGE_TARGETS:
        with tempfile.TemporaryDirectory() as tmp:
            g = os.path.join(tmp, "gates")
            c = os.path.join(tmp, "capture")
            shutil.copytree(gates_dir, g, ignore=shutil.ignore_patterns("__pycache__", "selftest"))
            shutil.copytree(capture_dir, c, ignore=shutil.ignore_patterns("__pycache__", "sample"))
            with open(os.path.join(tmp, rel), "w", encoding="utf-8") as fh:
                fh.write(STUB)
            _, _, records = run_all(context_dirs, g, c, verbose=False)
            hit = [r for r in records if r["gate"] in kinds and not r["ok"]]
            total_kind = sum(1 for r in records if r["gate"] in kinds)
            status = "DETECTED" if hit else "UNDETECTED"
            all_ok = all_ok and bool(hit)
            print(f"sabotage {rel} controls={total_kind} mismatches={len(hit)} {status}")
    print(f"sabotage: {'all detected' if all_ok else 'FAILED'}")
    return 0 if all_ok else 1


def main(argv=None):
    p = argparse.ArgumentParser(description="Gate selftest.")
    p.add_argument("contexts", nargs="*", metavar="CONTEXT_DIR")
    p.add_argument("--sabotage", action="store_true")
    args = p.parse_args(argv)
    gates_dir = os.path.abspath(os.environ.get("JQA_GATES_DIR") or os.path.dirname(HERE))
    capture_dir = os.path.abspath(os.environ.get("JQA_CAPTURE_DIR")
                                  or os.path.join(os.path.dirname(gates_dir), "capture"))
    # labels are printed; never print the absolute path (operator home directory)
    contexts = [(os.path.abspath(c), c) for c in args.contexts] or [(HERE, os.path.relpath(HERE))]
    if args.sabotage:
        return sabotage(contexts, gates_dir, capture_dir)
    total, mismatches, _ = run_all(contexts, gates_dir, capture_dir)
    return 0 if mismatches == 0 and total > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
