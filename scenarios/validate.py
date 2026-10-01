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


def check_refs(doc: dict, file: Path, out: list[str]) -> None:
    roster_path = (file.parent / doc["roster"]).resolve()
    try:
        roster = json.loads(roster_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        out.append(f"$.roster: cannot read roster ({exc.__class__.__name__})")
        return
    people = {p.get("id"): p for p in roster.get("people", []) if isinstance(p, dict)}

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
        if action == "run" and not s.get("command"):
            out.append(f"{where}: action 'run' needs 'command'")
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

    for i, sh in enumerate(shots):
        tf = sh.get("terminal_from")
        for ref in ([tf] if isinstance(tf, str) else tf or []):
            if ref not in step_ids:
                out.append(f"$.shots[{i}].terminal_from: unknown step '{ref}'")
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
