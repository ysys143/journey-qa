#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml"]
# ///
"""Validate journey-qa scenario files.

Usage:
    uv run scenarios/validate.py FILE...        # YAML or JSON
    python3 scenarios/validate.py FILE.json...  # JSON needs no third-party package

Checks schema.json (a JSON Schema subset: type, const, enum, required,
properties, additionalProperties, items, minItems, pattern, minimum, maximum)
and then cross-references: persona refs exist in the roster, step/shot/gate
ids are unique and resolve, {{persona.<id>.<field>}} templates resolve, every
@<name> locator resolves through the scenario's selector map (with an argument
exactly when the mapped value has {arg}), and every browser wait names an
element or URL (the schema has no timer field).

Terminal steps (surface terminal) carry driver: exec (default), pty or tmux, and
the fields of that driver only. Required: exec cmd; pty cmd and dialog (every item
needs wait_for); tmux launch, done_when, and verify or a verify_note. A fixed sleep
is never a wait (a cmd or launch made only of sleeps, a tmux keys list whose only
wait is a sleep literal). Secrets: {{secret.<name>}} appears only as a pty answer
(secret: true, exactly the reference) or inside a tmux literal key, only when
declared class test in the scenario's secrets; a tmux step pastes a secret with
{paste_buffer: <name>}; class real is rejected on every terminal step (a human gate
enters it); a secret reference anywhere else (cmd, launch, wait_for, done_when,
verify, cleanup) is rejected for every class. A terminal shot (terminal_from) waits
on transcript: texts and is carried by the last step it lists.

Output: one line per problem, "<file>: <path>: <message>".
Exit: 0 valid, 1 problems found, 2 usage error or unreadable input.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "schema.json"
TEMPLATE_RE = re.compile(r"\{\{\s*([a-z]+)\.([A-Za-z0-9_-]+)(?:\.([A-Za-z0-9_]+))?\s*\}\}")
PERSONA_FIELDS = {"name", "email", "handle", "id", "role", "team"}
SECRET_RE = re.compile(r"\{\{\s*secret\.([A-Za-z0-9_-]+)\s*\}\}")
SECRET_ANY_RE = re.compile(r"\{\{\s*secret\.")
SLEEP_RE = re.compile(r"^\s*sleep\s+\S+\s*$")
CMD_SPLIT_RE = re.compile(r"&&|\|\||;|\n")
DRIVER_FIELDS = {
    "exec": {"cmd", "expect_exit", "stdout_contains", "timeout_s"},
    "pty": {"cmd", "dialog", "expect_exit", "stdout_contains", "timeout_s"},
    "tmux": {"launch", "size", "keys", "done_when", "verify", "verify_note", "expect_exit", "timeout_s", "cleanup"},
}
ALL_DRIVER_FIELDS = set().union(*DRIVER_FIELDS.values())
BROWSER_ONLY_FIELDS = {"target", "value", "request", "expect"}
SELECTOR_REF_RE = re.compile(r"^@([a-z0-9]+(?:[.-][a-z0-9]+)*)(?:\((.+)\))?$")
TYPE_MAP = {
    "object": dict, "array": list, "string": str, "integer": int,
    "number": (int, float), "boolean": bool, "null": type(None),
}


def load(path: Path):
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError:
            raise SystemExit(f"{path}: PyYAML not installed; run with `uv run scenarios/validate.py` or use JSON")
        return yaml.safe_load(text)
    return json.loads(text)


def type_ok(value, expected) -> bool:
    names = expected if isinstance(expected, list) else [expected]
    for name in names:
        py = TYPE_MAP[name]
        if name in ("integer", "number") and isinstance(value, bool):
            continue
        if isinstance(value, py):
            return True
    return False


def check_schema(value, schema: dict, path: str, out: list[str]) -> None:
    if "const" in schema and value != schema["const"]:
        out.append(f"{path}: must equal {schema['const']!r}")
        return
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{path}: must be one of {schema['enum']}")
        return
    if "type" in schema and not type_ok(value, schema["type"]):
        out.append(f"{path}: expected {schema['type']}")
        return
    if isinstance(value, str) and "pattern" in schema and not re.search(schema["pattern"], value):
        out.append(f"{path}: does not match {schema['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            out.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            out.append(f"{path}: above maximum {schema['maximum']}")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                out.append(f"{path}: missing required '{key}'")
        props = schema.get("properties", {})
        for key, sub in value.items():
            if key in props:
                check_schema(sub, props[key], f"{path}.{key}", out)
            elif isinstance(schema.get("additionalProperties"), dict):
                check_schema(sub, schema["additionalProperties"], f"{path}.{key}", out)
            elif schema.get("additionalProperties") is False:
                out.append(f"{path}: unknown key '{key}'")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            out.append(f"{path}: needs at least {schema['minItems']} item(s)")
        if "items" in schema:
            for i, item in enumerate(value):
                check_schema(item, schema["items"], f"{path}[{i}]", out)


def dupes(items: list, key: str, where: str, out: list[str]) -> set:
    seen: set = set()
    for item in items:
        k = item.get(key) if isinstance(item, dict) else None
        if k is None:
            continue
        if k in seen:
            out.append(f"{where}: duplicate {key} '{k}'")
        seen.add(k)
    return seen


def walk_strings(node, path: str):
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from walk_strings(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk_strings(v, f"{path}[{i}]")


def only_sleeps(cmd: str) -> bool:
    parts = [x for x in CMD_SPLIT_RE.split(cmd) if x.strip()]
    return bool(parts) and all(SLEEP_RE.match(x) for x in parts)


def regex_problem(text: str, groups: int = 0):
    try:
        rx = re.compile(text)
    except re.error as exc:
        return f"does not compile ({exc})"
    if groups and rx.groups < groups:
        return "needs a capture group"
    return None


def secret_class(name: str, path: str, declared: dict, out: list[str]) -> None:
    cls = declared.get(name)
    if cls == "real":
        out.append(f"{path}: secret '{name}' is class real; a real credential is entered by a person at a human gate, never by a driver")
    elif cls != "test":
        out.append(f"{path}: secret '{name}' is not declared (add it to the scenario's secrets with class: test)")


def check_terminal_step(s: dict, where: str, declared: dict, out: list[str]) -> None:
    """Driver-specific rules of one terminal step (docs/terminal.md)."""
    driver = s.get("driver", "exec")
    if driver not in DRIVER_FIELDS:
        return  # the schema reports it
    own = DRIVER_FIELDS[driver]
    for key in sorted(ALL_DRIVER_FIELDS - own):
        if key in s:
            out.append(f"{where}.{key}: not a field of the {driver} driver")
    for key in sorted(BROWSER_ONLY_FIELDS):
        if key in s:
            out.append(f"{where}.{key}: not a terminal step field (use the {driver} driver's fields)")
    sec = s.get("secrets", {})
    if isinstance(sec, dict):
        if "to_run_denylist" in sec:
            out.append(f"{where}.secrets.to_run_denylist: locators belong to browser steps; a terminal step uses secrets.capture_as {{name, pattern}}")
        cap = sec.get("capture_as")
        if cap is not None and not isinstance(cap, dict):
            out.append(f"{where}.secrets.capture_as: a terminal step needs {{name, pattern}}")
        elif isinstance(cap, dict) and isinstance(cap.get("pattern"), str):
            problem = regex_problem(cap["pattern"], 1)
            if problem:
                out.append(f"{where}.secrets.capture_as.pattern: {problem}")

    def need(field: str) -> bool:
        if not isinstance(s.get(field), str) or not s[field].strip():
            out.append(f"{where}: the {driver} driver needs '{field}'")
            return False
        return True

    if driver in ("exec", "pty"):
        if need("cmd") and only_sleeps(s["cmd"]):
            out.append(f"{where}.cmd: a fixed sleep is not a wait; wait on output, an exit code or a check on state")
    if driver == "pty":
        if "dialog" not in s:
            out.append(f"{where}: the pty driver needs 'dialog' (a program with no prompts is an exec step)")
        for i, item in enumerate(s.get("dialog") or []):
            if not isinstance(item, dict):
                continue
            w = f"{where}.dialog[{i}]"
            if isinstance(item.get("wait_for"), str):
                problem = regex_problem(item["wait_for"])
                if problem:
                    out.append(f"{w}.wait_for: {problem}")
            send = item.get("send")
            if not isinstance(send, str):
                continue
            if item.get("secret"):
                m = SECRET_RE.fullmatch(send.strip())
                if not m:
                    out.append(f"{w}.send: a secret answer is exactly {{{{secret.<name>}}}}")
                else:
                    secret_class(m.group(1), w + ".send", declared, out)
            elif SECRET_RE.search(send):
                out.append(f"{w}.send: {{{{secret.*}}}} needs secret: true")
    if driver == "tmux":
        need("launch")
        need("done_when")
        if isinstance(s.get("launch"), str) and only_sleeps(s["launch"]):
            out.append(f"{where}.launch: a fixed sleep is not a session under test")
        if isinstance(s.get("done_when"), str):
            problem = regex_problem(s["done_when"])
            if problem:
                out.append(f"{where}.done_when: {problem}")
        if not s.get("verify") and not (isinstance(s.get("verify_note"), str) and s["verify_note"].strip()):
            out.append(f"{where}: the tmux driver needs 'verify' (checks on durable state) or a 'verify_note' saying why there is none")
        keys = s.get("keys") or []
        waits = [k for k in keys if isinstance(k, dict) and "wait_for" in k]
        for i, item in enumerate(keys):
            if not isinstance(item, dict):
                continue
            w = f"{where}.keys[{i}]"
            kinds = [k for k in ("literal", "key", "paste_buffer", "secret", "wait_for") if k in item]
            if len(kinds) != 1:
                out.append(f"{w}: exactly one of literal, key, paste_buffer, wait_for (found {kinds or 'none'})")
            if isinstance(item.get("wait_for"), str):
                problem = regex_problem(item["wait_for"])
                if problem:
                    out.append(f"{w}.wait_for: {problem}")
            for k in ("paste_buffer", "secret"):
                if isinstance(item.get(k), str):
                    secret_class(item[k], f"{w}.{k}", declared, out)
            lit = item.get("literal")
            if isinstance(lit, str):
                for name in SECRET_RE.findall(lit):
                    secret_class(name, f"{w}.literal", declared, out)
                if SLEEP_RE.match(lit) and not waits:
                    out.append(f"{w}.literal: a fixed sleep is not a wait; add a wait_for key that polls the screen")
    # A secret reference is allowed only where the rules above place it.
    allowed = set()
    if driver == "pty":
        allowed = {f"{where}.dialog[{i}].send" for i in range(len(s.get("dialog") or []))}
    if driver == "tmux":
        allowed = {f"{where}.keys[{i}].literal" for i in range(len(s.get("keys") or []))}
    for path, text in walk_strings(s, where):
        if SECRET_ANY_RE.search(text) and path not in allowed:
            out.append(f"{path}: a secret reference is not allowed here (secrets are entered only through a pty answer or a tmux key)")


def check_refs(doc: dict, file: Path, out: list[str]) -> None:
    roster_path = (file.parent / doc["roster"]).resolve()
    try:
        roster = json.loads(roster_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        out.append(f"$.roster: cannot read roster ({exc.__class__.__name__})")
        return
    people = {p.get("id"): p for p in roster.get("people", []) if isinstance(p, dict)}

    declared_secrets = {k: (v.get("class") if isinstance(v, dict) else None) for k, v in (doc.get("secrets") or {}).items()}
    persona_ids = set()
    for i, p in enumerate(doc.get("personas", [])):
        if p.get("ref") not in people:
            out.append(f"$.personas[{i}].ref: '{p.get('ref')}' is not a roster people[].id")
        persona_ids.add(p.get("ref"))

    instances = {x.get("id") for x in doc.get("environment", {}).get("instances", [])}
    steps = doc.get("steps", [])
    shots = doc.get("shots", [])
    step_ids = dupes(steps, "id", "$.steps", out)
    shot_ids = dupes(shots, "id", "$.shots", out)
    dupes(shots, "file", "$.shots", out)
    dupes(doc.get("human_gates", []), "id", "$.human_gates", out)
    dupes(doc.get("assertions", []), "id", "$.assertions", out)

    for i, s in enumerate(steps):
        where = f"$.steps[{i}]"
        if s.get("persona") not in persona_ids:
            out.append(f"{where}.persona: '{s.get('persona')}' is not declared in personas")
        if "instance" in s and instances and s["instance"] not in instances:
            out.append(f"{where}.instance: unknown instance '{s['instance']}'")
        surface, action = s.get("surface"), s.get("action")
        if surface == "terminal" and action != "run":
            out.append(f"{where}: terminal steps use action 'run'")
        if action == "run" and surface != "terminal":
            out.append(f"{where}: action 'run' belongs to terminal steps")
        if surface == "terminal":
            check_terminal_step(s, where, declared_secrets, out)
        else:
            stray = sorted((ALL_DRIVER_FIELDS | {"driver", "reconnect"}) & set(s))
            if stray:
                out.append(f"{where}: {stray} are terminal step fields (surface is '{surface}')")
            if isinstance(s.get("secrets", {}).get("capture_as"), dict):
                out.append(f"{where}.secrets.capture_as: a name only on a {surface} step ({{name, pattern}} is for terminal steps)")
        if action == "request" and not s.get("request"):
            out.append(f"{where}: action 'request' needs 'request'")
        if action in ("goto", "click", "fill", "select", "press", "wait") and not s.get("target"):
            out.append(f"{where}: action '{action}' needs 'target' (wait on an element or URL, never a timer)")
        if action in ("fill", "select", "press") and "value" not in s:
            out.append(f"{where}: action '{action}' needs 'value'")
        if "shot" in s and s["shot"] not in shot_ids:
            out.append(f"{where}.shot: unknown shot '{s['shot']}'")
        inval = s.get("secrets", {}).get("invalidate_after")
        if inval and inval not in step_ids:
            out.append(f"{where}.secrets.invalidate_after: unknown step '{inval}'")

    for i, g in enumerate(doc.get("human_gates", [])):
        if g.get("before_step") not in step_ids:
            out.append(f"$.human_gates[{i}].before_step: unknown step '{g.get('before_step')}'")

    terminal_steps = {x.get("id") for x in steps if isinstance(x, dict) and x.get("surface") == "terminal"}
    terminal_shots = {}
    for i, sh in enumerate(shots):
        tf = sh.get("terminal_from")
        refs = [tf] if isinstance(tf, str) else list(tf or [])
        for ref in refs:
            if ref not in step_ids:
                out.append(f"$.shots[{i}].terminal_from: unknown step '{ref}'")
            elif ref not in terminal_steps:
                out.append(f"$.shots[{i}].terminal_from: step '{ref}' is not a terminal step")
        if tf is not None:
            terminal_shots[sh.get("id")] = refs
        waits = [w for w in sh.get("wait_for", []) if isinstance(w, str)]
        if tf is not None and any(not w.startswith("transcript:") or w == "transcript:" for w in waits):
            out.append(f"$.shots[{i}].wait_for: a terminal shot waits on 'transcript:<text>' entries only")
        if tf is None and any(w.startswith("transcript:") for w in waits):
            out.append(f"$.shots[{i}].wait_for: 'transcript:' entries need terminal_from")
        if tf is not None and any(k in sh for k in ("url", "view_state", "no_truncate", "variants", "instance")):
            out.append(f"$.shots[{i}]: a terminal shot has no url, view_state, no_truncate, variants or instance")
    for i, s in enumerate(steps):
        sid = s.get("shot")
        if sid in shot_ids:
            if s.get("surface") == "terminal" and sid not in terminal_shots:
                out.append(f"$.steps[{i}].shot: terminal step names browser shot '{sid}'")
            elif s.get("surface") != "terminal" and sid in terminal_shots:
                out.append(f"$.steps[{i}].shot: '{sid}' is a terminal shot; only a terminal step carries it")
            elif sid in terminal_shots and terminal_shots[sid] and terminal_shots[sid][-1] != s.get("id"):
                out.append(f"$.steps[{i}].shot: terminal shot '{sid}' is carried by the last step it lists ('{terminal_shots[sid][-1]}')")
    for i, sh in enumerate(shots):
        if "instance" in sh and instances and sh["instance"] not in instances:
            out.append(f"$.shots[{i}].instance: unknown instance '{sh['instance']}'")

    check_selectors(doc, file, out)

    for path, text in walk_strings(doc, "$"):
        for m in TEMPLATE_RE.finditer(text):
            ns, ident, field = m.group(1), m.group(2), m.group(3)
            if ns == "persona":
                if ident not in persona_ids:
                    out.append(f"{path}: template persona '{ident}' is not declared in personas")
                elif field not in PERSONA_FIELDS:
                    out.append(f"{path}: template field '{field}' not in {sorted(PERSONA_FIELDS)}")
            elif ns != "secret":
                out.append(f"{path}: unknown template namespace '{ns}'")


def load_selector_map(doc: dict, file: Path, out: list[str]):
    if "selectors" not in doc:
        return None
    try:
        data = json.loads((file.parent / doc["selectors"]).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        out.append(f"$.selectors: cannot read selector map ({exc.__class__.__name__})")
        return None
    sel = data.get("selectors") if isinstance(data, dict) else None
    if not isinstance(sel, dict) or not all(isinstance(v, str) and v for v in sel.values()):
        out.append("$.selectors: map needs a 'selectors' object of non-empty strings")
        return None
    mask = data.get("mask", {})
    if not isinstance(mask, dict) or any(
            not isinstance(mask.get(k, []), list) or not all(isinstance(x, str) for x in mask.get(k, []))
            for k in ("selectors", "text")):
        out.append("$.selectors: 'mask' needs lists of strings under 'selectors' and 'text'")
    return sel


def check_selectors(doc: dict, file: Path, out: list[str]) -> None:
    """Every string that starts with @ is a selector reference into the map."""
    refs = [(p, t) for p, t in walk_strings(doc, "$") if t.startswith("@")]
    sel = load_selector_map(doc, file, out)
    if refs and "selectors" not in doc:
        out.append(f"{refs[0][0]}: @ locator used but the scenario declares no 'selectors' map")
        return
    if sel is None:
        return
    for path, text in refs:
        m = SELECTOR_REF_RE.match(text)
        if not m:
            out.append(f"{path}: malformed selector reference (use @name or @name(arg))")
        elif m.group(1) not in sel:
            out.append(f"{path}: selector '{m.group(1)}' is not in the selector map")
        elif ("{arg}" in sel[m.group(1)]) != (m.group(2) is not None):
            out.append(f"{path}: selector '{m.group(1)}' takes an argument exactly when its value has {{arg}}")


def validate(file: Path, schema: dict) -> list[str]:
    try:
        doc = load(file)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"{file}: cannot read ({exc.__class__.__name__}: {exc})")
    out: list[str] = []
    check_schema(doc, schema, "$", out)
    if isinstance(doc, dict) and not out:
        check_refs(doc, file, out)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("files", nargs="+", type=Path)
    args = ap.parse_args(argv)
    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"validate: cannot read schema: {exc}", file=sys.stderr)
        return 2
    problems = 0
    for f in args.files:
        if not f.is_file():
            print(f"validate: not found: {f}", file=sys.stderr)
            return 2
        try:
            msgs = validate(f, schema)
        except SystemExit as exc:
            print(exc, file=sys.stderr)
            return 2
        for m in msgs:
            print(f"{f}: {m}")
        problems += len(msgs)
        if not msgs:
            print(f"{f}: OK")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
