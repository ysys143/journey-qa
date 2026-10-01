# Decision log: <product> <run ID>

Append decisions in date order. Do not edit earlier entries. A changed decision is a new entry that points at the earlier one ("supersedes the YYYY-MM-DD entry").

Each entry records:

- Date
- Decision or fact
- Basis (evidence file, command output)
- Decider (user / main session). Gate exemptions and acceptance of violations are decided by the user only
- Impact (phases, gates, files that change)

---

## YYYY-MM-DD: <title>

- Decision:
- Basis:
- Decider:
- Impact:

<!-- Example entries (invented)

## 2030-01-15: Build the install source as a clone of the public tree

- Decision: Run git init and commit on the exported tree inside the environment so it matches a clone
- Basis: Real users clone the mirror; the missing .git directory came from our shortcut
- Decider: main session
- Impact: Phase 1 procedure. Earlier defect D-03 retracted

## 2030-01-16: Allowlist for operational UUIDs

- Decision: Add --allow-uuids. Build the list only from operational columns of the demo DB and confirm by SQL that none is an identity column
- Basis: Random UUIDs such as session IDs trip the UUID rule
- Decider: user
- Impact: Text and image leak gates. Two controls added, manifest refrozen
-->
