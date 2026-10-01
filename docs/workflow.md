# Workflow

A run follows the phases below. Each phase ends with a gate, and the main session reads the result before deciding whether to continue. Skip a phase the product does not need, and record the reason in the decision log.

## Phases

| Phase | Content | Ending gate |
|---|---|---|
| 0 Foundation | Run directory, roster, denylist, policy, gate selftest and freeze, capture spec, **real product reference captures** | Selftest passes, manifest frozen, roster validated |
| 1 Environment | Prepare the clean-room server (and client); install by the public path | Clean-room confirmation, health check |
| 2 Journeys | Run the journeys in explore mode; record commands and real output | Expected state per journey, docs comparison table |
| 3 Data | Generate and load synthetic data for operating screens (instance B) | Identity SQL assertions, generator marker, offline validation, figures agree across screens |
| 4 Capture | Produce images and sibling text from the shot list | Leak scan (text, images), PNG metadata |
| 5 Judgment | The judge opens every image, describes it and compares it with the reference | Judge verdict |
| 6 Apply | Edit docs, apply images | Docs-to-image mapping, the product repo's existing checks, **human final image approval** |
| 7 Wrap-up | Report, issue drafts, cleanup (environment, credentials, `secrets/`) | Deletion checklist |

Phases 1-2 and phase 3 do not depend on each other and can run in parallel. Phase 3 applies only if operating screens need demo data.

### Required in phase 0

- **Production reference captures.** Before generating synthetic data, look at the same screens in the real product. Keep captures in `secrets/` and delete them at the end. The repo keeps only a description of shape and scale (`fixtures/reference-shape.template.md`), with no identifiers and no real figures.
- **Read the chart data contracts.** Before using a generator, confirm in code how chart components draw data: whether empty intervals are zero-filled, the gap in minutes at which a line breaks, and which price-table keys exist.
- **Gate selftest.** After `gates/selftest/run.py` and `--sabotage` pass, run `gates/manifest.py freeze` on `gates/`, `capture/`, `runners/` and the adapter directory (resolved by the rule in `adapters/README.md`: explicit `--adapter`/`JQA_ADAPTER_DIR`, then `<target project>/.journey-qa/adapters/<product>/`, then bundled `adapters/<product>/`; its manifest lives inside it) (excluding top-level prose docs and `capture/sample/`; the command is in `SKILL.md`). Run `verify` before every gate execution afterward.
- **Fix the capture spec.** Set `capture/SPEC.md` for the product: viewport, scale, theme, naming rules.

## The loop inside a phase

```text
plan (strong model) -> execute (standard model) -> gate (strong model, read-only)
                            ^                              | fail
                            +---- repair (standard model) <-+   at most 2 times, then stop and report
```

| Role | Permission | Must |
|---|---|---|
| Plan | Splits the phase spec into tasks with completion criteria and evidence paths | Every task has a completion criterion and an evidence path |
| Execute | Full tools | Does not judge gates. Leaves evidence at the given path |
| Gate | Read and run commands only; cannot modify files | Does not trust the executor's report. No evidence means fail |
| Repair | Only the items the gate flagged | Must not edit gates, denylists or policy |

The strong and standard tiers are roles, not model names. A workflow skeleton for an agent runtime that offers `agent()` and `phase()` is in `templates/examples/workflow-phase.js`; model tiers come from `args.models` (`planner`, `executor`, `gate`), and an optional `args.gateAgentType` selects the gate agent type.

## Orchestration rules

- **Split large phases.** An executor given a whole large phase tends to write a design document and stop at "the scope is large". Make implementation, loading, capture and judgment sequential phases, each receiving a summary of the previous one. In runtimes where agents inside a workflow cannot call sub-agents, say so in the prompt: "You have no sub-agent tool. Do the work yourself. Do not stop to hand it off."
- **Do not message a running workflow agent** (in runtimes with this limit). A second copy starts and the two overwrite each other's files. Write new instructions to a file, fix the spec and rerun the workflow; unchanged phases replay from cache.
- **Checks that need host infrastructure run in the main session.** The judge cannot touch host containers. If a container client points at a socket with no matching context, set it by environment variable for that run only and leave the global setting alone.
- **Production access belongs to one top-level executing agent.** Do not hand it to a sub-agent (`docs/prod-access.md`).
- **No agent reads credential files.**
- **Prove a tool failure with an isolated test.** Check whether other calls in the same session work and whether the tool also fails on an unrelated site, then switch to an alternative tool and record it. Retest the original tool in the next phase; do not carry an earlier phase's failure verdict forward. The tool is a means, not a requirement: any tool that can drive the same screens will do.
- **Absolute paths to tools, relative paths in evidence.** A tool server may resolve a relative path against its own working directory and save to the wrong place. An absolute path in a report, log or issue records the operator's home name. Write run-directory-relative paths in evidence.

## codify and run

After explore, move the record into a scenario file (`scenarios/schema.json`, example `scenarios/example/onboarding.yaml`).

| Item | Rule |
|---|---|
| Determinism | Pin `seed`, `time_anchor`, `viewport` (including scale), `locale`, `timezone` and `color_scheme` in the file |
| Selectors | Product selectors live in the adapter's selector map; steps refer to them as `@name` or `@name(arg)` |
| Waiting | A `wait_for` selector or URL. No fixed-time waits |
| Preparation | Preparation that is not under test (creating users, loading data) goes in `setup`, via API or CLI |
| Terminal steps | Name the driver per step (`driver: exec` is the default; `pty` for known prompts; `tmux` for the interactive session itself; `docs/terminal.md`). Secrets are declared with a class and read from a mode-600 file; a tmux session ends on a `done_when` screen regex with a ceiling, never a fixed sleep, and its outcome is judged by `verify` checks on durable state. Raw transcripts go in `secrets/`; gates see only the redacted transcript |
| Capture | Each shot-list entry gives the URL, screen state (filter, scroll, theme), wait selector and the selector to check for truncation |
| Assertions | URL, element visibility, text, aggregate agreement |
| Human gates | Mark steps such as real login with `human_gate`. Run mode stops there and waits for a person |
| Baseline images | Human-approved images with hashes, per engine (`baselines/<engine>/<scenario-id>/`). Run mode calls an agent only when the masked difference exceeds the threshold (`gates/img_diff.py`) |
| Product checks and cleanup | In the adapter's runner hooks: one per declared assertion, and a teardown that always runs |

In explore mode, record each terminal step's driver choice and the screen-versus-state evidence while the journey is fresh, so codify can write `done_when` and `verify` without guessing. Validate a scenario: `uv run scenarios/validate.py <file>` (YAML uses PyYAML as an inline dependency; JSON needs only the standard library).

Run a scenario with `runners/run.sh` (`runners/README.md`). The engine follows where the run executes (`docs/engines.md`): a real-profile engine on a workstation or with a person in the loop, an isolated engine in CI. A failed step leaves evidence only after `capture/evidence.py` has redacted and gated it; a failed terminal step keeps its redacted transcript. A terminal step that a policy layer blocked is reported as "not verified". A scenario with no browser step needs no browser engine.
