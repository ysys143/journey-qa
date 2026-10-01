# Rationale

Each rule below is stated with the general failure it prevents and the place that enforces it. A rule with an empty "Enforced in" cell is not yet a rule. When a new run teaches a new rule, add a row and fill that cell.

| Rule | Why (general failure mode) | Enforced in |
|---|---|---|
| Look at the real product screens before generating data | Data that passes every numeric gate can still look fake on screen and must be regenerated | `docs/workflow.md` phase 0, `fixtures/README.md` |
| Read the charts' data contracts first | Zero-filling of empty buckets, line breaks at sampling gaps and unknown price-table keys leave isolated points or empty charts | `fixtures/README.md`, `adapters/*/data-contracts.md` |
| Production data is material, not a source to copy with personal data removed | A copy with fields stripped keeps its shape and its identifiers' neighbors, and leaks through what remains | `docs/prod-access.md`, `fixtures/README.md` |
| Numeric gates are not enough; the judge opens the images | Inconsistent records, dates in the wrong locale, generator markers on screen, truncated emails and empty chart tails are visible only in the image | `fixtures/realism-checklist.md`, gate prompt in `templates/examples/workflow-phase.js` |
| Leak defense has three layers: dump, innerText at capture, OCR | OCR misses small text such as an `@` at 11px; sibling text does not | `gates/image_scan.py`, `capture/SPEC.md` |
| Never pass a gate by changing meaning | Rewriting a docs placeholder as a redaction marker states something false about the product | `docs/concepts.md`, `gates/SPEC.md` value judgment |
| Exemptions are narrow and ship with pass and fail controls | A broad exemption silently widens until it hides real leaks | `gates/SPEC.md` exemptions, enforced at policy load, selftest |
| A gate fails closed | A binary dump read as "nothing to scan", or an excluded path hiding a stale dump, turns missing inspection into a pass | `gates/SPEC.md` file classification, no exclusion globs |
| Test the gate adversarially | Encoded paths (dash, URL, JSON escape), line-wrapped emails and binary files evade a gate that was only tested on plain input | `gates/selftest/`, `--sabotage` |
| Differential-test a gate rewrite against the old version | A faster rewrite can differ on long lines (quadratic time), locales and file names containing tabs | `gates/LIMITS.md` |
| Evidence tools never emit absolute paths | A path-normalizing helper that falls back to `pwd` records the operator's login name | `gates/SPEC.md` common contract |
| Production queries get EXPLAIN and resource limits first | A read-only aggregate can still exhaust temp space or CPU on a production database | `docs/prod-access.md`, `templates/prod-query.sql` |
| Separate identity IDs from operational random IDs | Session IDs trip the identity UUID rule; the allowlist goes stale on every reload | `docs/concepts.md` leak defense, `--allow-uuids` |
| Replace identity by schema catalog; block re-leaks at the source | A hand-written table list covers only part; a client poller keeps sending real values | `docs/concepts.md`, `adapters/<product>/sql/identity.sql` header |
| Roster values use the product's stored shape | A display-string plan does not match price-table keys, so lines are not drawn | `fixtures/roster_check.py --allowed-plans` |
| 1:1:1 holds per provider | A person with accounts at two providers fails a global 1:1 check | `fixtures/roster_check.py` PER_PROVIDER, identity SQL |
| Follow the real user's acquisition path | A difference created by a shortcut (such as copying instead of cloning) gets filed as a defect and has to be retracted | `docs/concepts.md` journeys |
| Isolation includes metadata | A VM inherits the host user's real name as its full-name field | `docs/concepts.md` environment |
| Docs-comparison claims are checked against the table independently | A command absent from the docs reported as "documented" is caught only by a separate check | `templates/report.md` docs comparison table |
| Split large phases; in runtimes with this limit, workflow agents cannot call sub-agents | An executor given a whole large phase writes a design document and stops | `docs/workflow.md`, `templates/examples/workflow-phase.js` |
| Do not message a running workflow agent (in runtimes with this limit) | A second copy starts and the two overwrite each other's files | `docs/workflow.md` |
| Prove a tool failure with an isolated test and retest it in the next phase | A screenshot timeout attributed to the tool was caused by a long-lived app process | `docs/workflow.md` |
| Do not dump page text from a screen that shows a secret | Selector debugging prints a temporary password | `capture/SPEC.md` |
| Failure evidence is redacted and gated before it is kept | Browser traces and raw event logs record request bodies (typed passwords included) and the runner's own source paths | `capture/evidence.py`, `runners/lib/run.mjs` |
| Baselines are per engine | Font rasterization differs between engines, and between headed and headless modes, by more than the comparison threshold | `gates/img_diff.py`, `docs/engines.md` |
| Strip every PNG a run writes | Screenshots from a real display carry an ICC profile that names the display's maker and model | `capture/strip_png.py`, selftest `strip_png` |
| Freshness assertions follow the generator's real tail | A generator that ends its data before the anchor fails "newest event within N minutes" on every run | `fixtures/realism-checklist.md` |
| A browser that shares a profile gets its own host name per instance | Cookies ignore ports; a login on the loopback address overwrites other local servers' sessions | `docs/engines.md`, scenario `base_url` |
| Avoid truncated screens, or file them as defects | OCR misreads a truncated email and produces a false positive | `capture/SPEC.md` truncation check, scenario `no_truncate` |
| 1x images are blurry | Low-resolution captures are unreadable | `capture/SPEC.md` scale 2 |
| `exiftool -all=` does not remove every chunk | A rasterizer writes bKGD and a downscaler writes cHRM | `capture/strip_png.py`, `gates/png_meta.py` chunk allowlist |
| Report real exit codes and counts that match | A spec's control count that differs from the actual count misleads | `templates/report.md` gates table |
| A violation cannot be fixed afterward; record it and ask a human | A query sent without a read-only transaction, or run without independent review, has already acted | `docs/prod-access.md`, `templates/decision-log.md` |
