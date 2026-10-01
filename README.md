# journey-qa

An agent skill for persona-journey acceptance QA. It starts from an isolated clean environment and a fictitious team, walks a product's journeys from the beginning (for example install and first privileged user, onboarding a new user, a team in operation), and leaves documentation, product and UX defects, evidence that is safe to publish, and docs screenshots.

The skill entry point is `SKILL.md`. Concepts are in `docs/concepts.md`, the procedure in `docs/workflow.md`.

## Layout

```
SKILL.md                    Entry point: mode routing, phase 0 checklist, gate commands, human gates
docs/
  concepts.md               Personas, journeys, environment, modes, gates, leak defense, human gates
  workflow.md               Phases 0-7, plan -> execute -> gate -> repair loop, orchestration rules, codify/run
  prod-access.md            Rules for reading production data
  engines.md                Which run engine where (`--engine auto` default); login state save, inject, exchange, expiry
  decisions/                Decision records (measurements behind a choice)
  rationale.md              Each rule, the general failure it prevents, where it is enforced
scenarios/
  schema.json               Scenario format
  validate.py               Schema and cross-reference validation (uv run; PyYAML as an inline dependency)
  example/onboarding.yaml   Product-neutral example
  selftest/                 Validator controls
runners/
  README.md                 Run mode: interpreter, backends, hooks, failure evidence, baselines
  run.sh                    Entry point (validate, convert, run, compare baselines)
  run-engine.mjs  lib/      Engine-neutral interpreter and in-page functions
  ego/  playwright/         Engine backends
  selftest/                 Smoke test on an invented demo app
gates/
  SPEC.md                   Gate specification (exit codes, rules, exemptions, selftest)
  LIMITS.md                 Measured limits
  leakscan.py               Text and dump leaks
  image_scan.py             Image leaks (sibling innerText + OCR)
  png_meta.py               PNG names and chunks
  docs_images.py            Docs-to-image mapping, locale twins
  img_diff.py               Masked pixel comparison with per-engine baseline images
  sql_assert.py             DB identity assertions (sqlite, psql)
  manifest.py               Gate freeze and verification
  policy.default.json       Default policy
  selftest/                 Controls, --sabotage
capture/
  SPEC.md                   Capture spec
  strip_png.py              Strip metadata using a chunk allowlist
  redact.py                 Transcript redaction
  evidence.py               Redact and gate failure evidence; delete it unless it passes
  render_terminal.py        Render a redacted transcript as a terminal image
fixtures/
  README.md                 Synthetic data order and generator interface
  roster.schema.json        Roster format
  roster.example.json       Five fictitious people
  roster_check.py           Roster validator (+ selftest/)
  reference-shape.template.md  Template describing a production reference's shape
  realism-checklist.md      Judge checklist
adapters/
  README.md                 Adapter contract
  <product>/                Bundled example adapters. Your own adapter lives in the target project:
                            <project>/.journey-qa/adapters/<product>/ (or any path via --adapter)
check.sh                    Every check in the repo (JQA_ADAPTER_DIRS adds adapters kept elsewhere)
templates/
  report.md  issue-draft.md  decision-log.md  gate-verdict.schema.json  prod-query.sql
  examples/workflow-phase.js
```

## Running the checks

```
./check.sh                                           # everything below; exit 0 means pass
JQA_DENYLIST=<real denylist outside the repo> ./check.sh   # also scan the repo against real identifiers
JQA_ADAPTER_DIRS=<dir>[:<dir>...] ./check.sh         # also check adapters kept outside the repo
```

`check.sh` runs: gate selftests (core plus every adapter context), `--sabotage`, manifest verification (gates, capture, runners, every adapter), the adapter-resolution selftest (including an adapter copied outside the repo), scenario validator controls, roster validator controls, the engine-selection selftest, a syntax check of every runner module, and a self-scan of the repo for leaks. A self-scan hit that exists only because a sentence describes a pattern is recorded with a reason in the adapter's `self-scan.accepted`; `check.sh` reads one per adapter it checks. An entry that no longer matches anything also fails.

Requirements: Python 3.10+, `tesseract` (image OCR), `rsvg-convert` (regenerating terminal images and OCR fixtures), `uv` (YAML scenario validation), the "DejaVu Sans Mono" font (terminal rendering; character width is specific to it), Node.js 18+ (runners). `psql` only when using the psql engine. Running a scenario also needs at least one engine backend: `ego-browser` (real user profile, headed capture) or a Playwright install (isolated, headless or headed, parallel-safe). `--engine auto`, the default, detects what is usable and picks by where the run executes (`docs/engines.md`).

## Core and adapters

The core (everything outside `adapters/`) knows nothing about any product. Everything that differs per product goes into an adapter.

| Core provides | Adapter provides |
|---|---|
| Text, image, metadata and docs-image gates; every rule, file classification, normalization, path checks, policy-driven exemptions | `policy.json`: extra rules, secret key names, exemptions (each with a pass and a fail control), redaction rules |
| SQL assertion runner and roster CTE; per-provider 1:1:1 example | `sql/identity.sql` assertions for the product's tables |
| Capture spec, terminal rendering, redaction, metadata stripping | Product-specific redaction rules |
| Synthetic data order, generator interface contract, realism checklist, reference-shape template | Data contracts for the product's charts and tables; optionally a generator |
| Phases, gate kinds, loop structure, workflow skeleton, decision-log format | Environment setup, product facts, findings from earlier runs |
| Roster format, validator, example | Stored-shape values (roles, plans, providers) and any `privileged_roles` |
| Scenario format and validator | Journey scenarios |
| Run-mode runner, engine backends, masks, baseline comparison, failure-evidence gating | Selector map, runner hooks (setup, product checks, cleanup), product token and host facts |

Adapter files, where an adapter lives (explicit path, then the target project, then bundled) and the procedure for writing one are in `adapters/README.md`. `adapters/example-app/` is a worked example adapter for an invented product.

## Roadmap

Not built yet:

- Terminal steps in run mode. The runner drives browsers; terminal journeys still need a pty driver.
- Codify automation. Today an agent reads an explore record and writes the scenario by hand.
- Per-product synthetic data generators. Only the interface contract is in core.

## License

MIT. See `LICENSE`.
