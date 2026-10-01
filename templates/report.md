# journey-qa report: <product> <run ID>

- Run period:
- Target version / commit:
- Install source: (public tree, whether cloned)
- Journeys:
- Roster:
- Engine: (engine and reason, from `<run>/engine.json`; baselines are per engine; `none` for a terminal-only scenario)
- Terminal drivers used: (exec / pty / tmux, per step; tmux version when used)
- Host before/after: (directories compared by listing and modification time, and the result; tmux servers and sockets left behind: must be none)
- Run directory: (relative path only; no operator home path)

## Summary

Lead with the result: how many journeys were walked, how many defects, and the state of the gates and human gates.

| Item | Value |
|---|---|
| Journeys completed | n / n |
| Defects (docs / product / UX) | n / n / n |
| Infrastructure quirks | n |
| Procedure violations | n |
| Captured images (approved / total) | n / n |
| Human gates remaining | |

## Defects

| ID | Class | Title | Journey / step | Evidence | Issue draft |
|---|---|---|---|---|---|
| D-01 | docs / missing | | | `evidence/...` | `issues/D-01.md` |

Do not delete a retracted defect; mark it "retracted" with the reason.

## Terminal steps

| Step | Driver | Status (passed / failed / timeout / not verified) | Verdict source | Transcript |
|---|---|---|---|---|

A step an outer policy layer blocked is "not verified" and counts as neither passed nor failed. The verdict source of a tmux step is the `verify` result (file, API, database), not the screen.

### Substitution table

Every environment-specific value that a docs copy replaces with a documented default (a port, a host alias, a guest home path, a user name). The replacement changes the text after the gates ran, so each one is recorded.

| Value as it appeared | Value in the docs | Reason | Transcript |
|---|---|---|---|

## Docs comparison table

For every command run in a journey, give its location in the public docs (file:line). A command with no location is a defect candidate. The judge verifies this table separately.

| Step | Command run | Docs location | Match |
|---|---|---|---|

## Gates

Record only exit codes that were actually produced.

| Gate | Target | Command | Exit code | Note |
|---|---|---|---|---|
| Manifest verification | `gates/`, `capture/`, `adapters/<product>/` | `manifest.py verify` | | |
| Text leak | | `leakscan.py` | | |
| Image leak | | `image_scan.py` | | |
| PNG metadata | | `png_meta.py` | | |
| Docs-to-image | | `docs_images.py` | | |
| Identity assertions | | `sql_assert.py` | | |
| Judge | n images | | | Per-image descriptions in `verdicts/` |

## Exemptions and violations

| Date | What | Human decision | Control |
|---|---|---|---|

## Human gates

| Gate | Status | Decided by | Date |
|---|---|---|---|
| Real login | | | |
| Final image approval | | | |
| Publication (push, PR, export) | | | |

## Cleanup

| Item | How deletion is confirmed | Result |
|---|---|---|
| Isolated environment | Listing | |
| Credentials | Environment deleted | |
| `secrets/` | File listing | |
| Production reference captures | File listing | |
| Browser workspace (production login) | | |

## Follow-ups

## Lessons for the next run

Only what this run newly learned. Do not repeat anything already in `docs/rationale.md`.
