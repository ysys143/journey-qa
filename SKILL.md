---
name: journey-qa
description: Persona-journey acceptance QA for a product, from an isolated clean environment and a fictitious persona team. Walks journeys end to end (day-0 install and first privileged user, onboarding a new user, a team in operation), records docs/product/UX defects with evidence, and produces leak-gated docs screenshots. Modes: explore (agent drives browser/terminal), codify (turn a run into a declarative scenario), run (deterministic replay). Also use to execute install or setup docs verbatim, end to end, and record every gap between the docs and reality, or to acceptance-test a product as a newly adopting team. Use for "fresh install QA", "onboarding walkthrough", "docs screenshots", "acceptance test as a new team", "클린 설치 재현", "온보딩 검증", "문서 스크린샷", "여정 QA", "가상 팀으로 검수". Not for: verifying a code change is complete (tests, diff); a single-screen or single-command check; UI accessibility or design review; writing tests; one screenshot of one page.
---

# journey-qa

A fictitious team installs and starts using a product in a clean environment. The run walks each journey end to end and leaves two things: defects, and evidence that is safe to publish.

Read first: `docs/concepts.md` (terms, gates, human gates). The procedure is in `docs/workflow.md`.

## Choose a mode

| Situation | Mode | Start at |
|---|---|---|
| New product, or the journeys changed | explore | `docs/workflow.md`, phase 0 |
| An explore record exists and should be repeatable | codify | `scenarios/schema.json`, example `scenarios/example/onboarding.yaml` |
| A scenario and approved baseline images exist | run | `runners/run.sh` (`runners/README.md`). Engine: `--engine auto` is the default and picks by where the run executes: a real-profile engine (real user profile, headed capture a person can watch and take over) for an interactive local run, an isolated engine (fresh context per persona, headless, parallel-safe) in CI, headless, or when the real-profile engine is not usable. The choice and its reason are printed and recorded in the run output; cite them in the report (`docs/engines.md`) |

An adapter is a directory path, resolved by one rule (`gates/adapter_dir.py`). Search order, first hit wins: explicit `--adapter DIR` (or `JQA_ADAPTER_DIR`), then `<target project>/.journey-qa/adapters/<product>/`, then the bundled `adapters/<product>/`. Bundled adapters are examples; an installed copy of this skill is replaced on update, so keep your own adapter in the target project. Resolve it once and reuse the variable:

```
ADAPTER="$(python3 gates/adapter_dir.py --product <product> --project <target project>)"   # add --adapter DIR to override
```

If the adapter exists, read its README first. If not, follow `adapters/README.md` to create one in `<target project>/.journey-qa/adapters/<product>/`.

## Run directory

Place it at `.journey-qa/runs/<run-id>/` in the target product repo (git-ignored).

```
report.md  decisions.md  evidence/  shots/  verdicts/  issues/
secrets/   # mode 700. Run denylist, raw transcripts, production reference captures. Deleted at wrap-up
```

## Phase 0 checklist

- [ ] Roster: `python3 fixtures/roster_check.py <roster> --people N --admins 1 --allowed-roles ... --allowed-plans ...` (`--admins N` counts people whose role is in the roster's `privileged_roles`; `--admin-role` overrides)
- [ ] Denylist: real identifiers go in `secrets/`, never in the repo
- [ ] Adapter: resolve `$ADAPTER` as above and read its README
- [ ] Gate selftest: `python3 gates/selftest/run.py gates/selftest "$ADAPTER/selftest"`, then again with `--sabotage`
- [ ] Gate freeze: freeze gate code, redaction and render tools, the runner, and the adapter's policy, controls, SQL, scenarios, selector map and hooks. The adapter's manifest lives in the adapter directory (`$ADAPTER/MANIFEST.sha256`), wherever that is. Exclude only top-level prose docs and `capture/sample/` (excluding every `*.md` would also drop documentation fixtures)
  ```
  for r in gates capture runners "$ADAPTER"; do
    python3 gates/manifest.py freeze --root $r --out $r/MANIFEST.sha256 --exclude SPEC.md --exclude LIMITS.md --exclude README.md --exclude data-contracts.md --exclude 'findings-*.md' --exclude 'sample/*' --exclude VERIFY.md
  done
  ```
- [ ] Production reference captures (when synthetic data is used): store in `secrets/`; keep only a description of shape, using `fixtures/reference-shape.template.md`
- [ ] Chart data contracts: the adapter's `data-contracts.md`
- [ ] Capture spec: `capture/SPEC.md`
- [ ] Start the decision log: `templates/decision-log.md`

## Gate commands

| Target | Command |
|---|---|
| Text and dumps | `gates/leakscan.py --roster R --denylist D [--denylist RUN_D] [--policy "$ADAPTER/policy.json"] [--allow-uuids F] TARGET` |
| Images | `gates/image_scan.py`, same arguments; PNG plus sibling `.png.txt` |
| PNG names and metadata | `gates/png_meta.py PNG...` (to strip, use `capture/strip_png.py`) |
| Captures against baselines | `gates/img_diff.py [--threshold 0.01] baselines/<engine>/<scenario-id> <run>/shots` (same engine only) |
| Failure evidence | `capture/evidence.py <dir> --roster R --denylist D [--policy P] [--secrets RUN_D]`; the runner calls it and keeps the evidence only on exit 0 |
| Docs-to-images | `gates/docs_images.py --pages-root DIR --images-dir DIR [--page README.md] [--locale-suffix .<lang>]` |
| DB identity | `gates/sql_assert.py --engine psql --dsn DSN --roster R "$ADAPTER/sql/identity.sql"` |
| Gate tampering | `gates/manifest.py verify --manifest <root>/MANIFEST.sha256` with the same `--exclude` flags used at freeze time, for each of `gates`, `capture`, `runners`, `$ADAPTER` (`./check.sh` does this for the bundled adapters and for every directory in `JQA_ADAPTER_DIRS`) |

`./check.sh` checks the bundled adapters. To include adapters kept elsewhere: `JQA_ADAPTER_DIRS=<dir>[:<dir>...] ./check.sh` (selftest, sabotage, manifest, scenario validation, runner syntax and self-scan for each).

Exit code 0 passes, 1 is a violation, 2 means the gate could not run. 2 is not a pass. Rules and limits: `gates/SPEC.md`, `gates/LIMITS.md`. The policy default lists no secret key names; an adapter's `policy.json` adds the product's own (the suffix rule still catches `*SECRET`, `*PASSWORD`, `*TOKEN`, `*API_KEY`, `*PRIVATE_KEY`).

## Rules

- Execute public documentation exactly as written, in the written order. A command the docs do not contain is a defect.
- Never make a gate pass by editing the gate, the denylist or the policy, and never by changing what the content means.
- A passing numeric gate does not end review: the judge opens every image and describes it (`fixtures/realism-checklist.md`).
- Put one-time secrets in the run denylist before they can appear on screen.
- Read production data only under the rules in `docs/prod-access.md`.
- Never read or copy credential files. Check only that they exist.
- Pass absolute paths to tools. Write run-directory-relative paths in evidence, logs and reports, so no operator home name is recorded.
- Stop and report when the same gate fails twice in a row.
- Keep the executing agent and the judge separate. The judge is read-only and does not trust the executor's report.
- Explore mode may drive the browser and terminal with any automation the agent has. Run mode uses the runner's engine backends (`docs/engines.md`).

## Human gates

These are decided by the user, not inside the workflow: real-account login, gate exemptions and acceptance of violations, production data scope, final image approval, and any irreversible publication (push, PR merge, export, issue filing).

## Output templates

| Output | Template |
|---|---|
| Report | `templates/report.md` |
| Issue draft | `templates/issue-draft.md` |
| Decision log | `templates/decision-log.md` |
| Verdict | `templates/gate-verdict.schema.json` |
| Phase workflow (example for a runtime offering `agent()`/`phase()`) | `templates/examples/workflow-phase.js` |
| Production query wrapper | `templates/prod-query.sql` |

Write reports in the user's working language.
