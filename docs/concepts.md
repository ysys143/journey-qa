# Concepts

journey-qa is persona-journey acceptance QA. It builds a fictitious team in an isolated clean environment and walks that team through installing and starting to use a product, from the beginning to the end. It has two purposes: find documentation, product and UX defects, and leave evidence and docs screenshots that are safe to publish.

## Terms

| Term | Meaning |
|---|---|
| Persona team | Fictitious people defined by a roster (`roster.json`). Names, emails and accounts are all newly invented values |
| Journey | The ordered steps by which one or more personas reach a goal across surfaces (browser, terminal, API) |
| Clean room | A fresh environment with no trace of the product, holding only the prerequisites the public docs state |
| Scenario file | A declarative description of a journey (`scenarios/schema.json`) |
| Gate | A check that decides whether an artifact may move to the next phase. Three kinds: script, judge, human |
| Adapter | A directory holding the facts that differ per product (install method, data contracts, policy, scenarios) |
| Run directory | Everything one run leaves behind: `.journey-qa/runs/<run-id>/` in the target repo (git-ignored) |

## Persona team

- Use names that are plainly fictitious. Use only reserved domains for email (`example.com`, `.test`, and similar).
- Do not use a roster made by stripping personal data from real data. Build it from scratch. Stripped data keeps its shape and invites leaks.
- Write values in the shape the product **stores**. A display string (such as a marketing plan name) will not match the keys that price tables and charts look up.
- Identity invariants hold per provider. If a person holds external accounts, 1:1:1 (person : login email : account ID) must hold within each provider, because one person may legitimately hold accounts at two providers.
- Give each person a different usage profile (volume, main features, tool setup) so that both the heavy and the light end of every chart appear.
- Include one person with a non-ASCII name. Truncation, alignment and encoding defects show up there.
- Validate the roster with `fixtures/roster_check.py`.
- Which roles count as privileged is configurable. The roster's optional top-level `privileged_roles` (default `["admin"]`) names them; SQL assertions refer to those people as `privileged_handle`.
- `accounts` (external accounts) and `projects[].cwd` (working directories) are optional roster extensions, not core invariants.

## Journeys

The journeys below are examples that an adapter instantiates for its product. Use the ones that apply and replace the rest.

| Journey | Steps | Defects it tends to expose |
|---|---|---|
| Day-0 | Install the server, create the first privileged user | The docs hold only a placeholder where a command is needed; adjacent sections do not chain |
| Onboarding | A privileged user adds a user, hands over a temporary password, the user changes it, installs the client, first use | Step order differs from product behavior; forced redirects or permission notices are missing |
| Team in operation | Several users' data screens, permission scope, filters | Mismatched aggregates, date range not applied, truncation, permission scope leaks |

Run each journey by following the public documentation **exactly as written, in the written order**. A command the docs do not contain is itself a defect. Follow the path a real user takes to obtain the product (for example, a clone). If a difference caused by a shortcut was filed as a defect, retract it publicly.

## Environment

- **Clean-room confirmation gate.** Before installing, confirm by listing that no product images, volumes, binaries or config directories exist. Pin base images by digest.
- **Isolation includes metadata.** Set explicitly the full name field of the environment's user, the home directory name and forwarded ports, so none inherits a real value from the host.
- **Host protection.** Real-account logins happen only inside the isolated client environment. Do not open the host's config directories; compare only file listings and modification times before and after the run.
- **Ports.** If a default port is already in use, use another and record how it differs from the docs.
- **Two instances, if operating screens need demo data.** Instance A starts empty and reproduces the real flow; instance B is loaded with synthetic data to produce operating screens. Never photograph operating screens from A's data.
- Record infrastructure quirks (for example a group cache in SSH sessions, or a tool saving relative paths somewhere unexpected) separately from the defect list.

The isolated clean environment may be a VM, a container or a fresh OS account.

## Modes

| Mode | What it does | Model calls |
|---|---|---|
| explore | An agent drives the browser and terminal to find the journey. Leaves defects, evidence and a first set of captures | Every step |
| codify | Turns an explore record into a scenario file and scripts: personas, steps with expected URLs, shot list (filter, scroll, wait selector), assertions | Once, at conversion |
| run | Executes the script deterministically: seed, time anchor, viewport, locale and timezone pinned; waits on elements, not time; preparation that is not under test goes through an API | Only on a step failure or a visual difference against an approved baseline image |

Mode selection follows the routing table in `SKILL.md`. A new product starts with explore. With a scenario file and approved baseline images, use run.

## Outputs

| Output | Location | Exported |
|---|---|---|
| Defect list, report | run directory `report.md` (`templates/report.md`) | A human decides |
| Issue drafts | run directory `issues/` (`templates/issue-draft.md`) | Filed after human approval |
| Evidence (commands, real output, redacted transcripts) | run directory `evidence/` | No |
| Screenshots and sibling text | run directory `shots/` | Images only, after the gates and human approval |
| Decision log | run directory `decisions.md` (`templates/decision-log.md`) | No |
| Secrets (run denylist, raw transcripts, production reference captures) | run directory `secrets/` (mode 700, files 600) | Never. Deleted at wrap-up |

Defects fall into five groups: documentation, product, UX, infrastructure quirk (not a defect), and procedure violation. The detailed classification is in `templates/issue-draft.md`.

## Gates

There are three kinds. Use a later kind only when an earlier one cannot decide.

| Kind | Decided by | Examples |
|---|---|---|
| Script | Exit code | Leak scan, PNG metadata, docs-to-image mapping, identity SQL assertions, manifest |
| Judge | A read-only agent on the strong model tier, working from evidence files and command output | Plausibility of images, shape comparison with a production reference, verification of the docs comparison table |
| Human | User approval | See "Human gates" below |

Principles.

- Do not edit a gate, denylist or policy to make a gate pass. Fix the data, recapture, or request an exemption.
- When a gate catches a product's fixed wording, add a narrow exemption together with a must-fail control (`gates/SPEC.md`, exemptions). A human approves it.
- Do not pass a gate by changing meaning. Rewriting a docs example `<token>` as `[REDACTED]` creates the false claim that the log redacts tokens.
- Numeric gates are not enough. The judge opens every image and describes each one.
- Stop and report when the same gate fails twice in a row.
- The executing agent does not judge gates, and the judge does not trust the executing agent's report.

## Leak defense in three layers

| Layer | Target | Tool | Note |
|---|---|---|---|
| 1 | DB dumps, text transcripts | `gates/leakscan.py` | Plain-text dumps only. A compressed dump cannot be inspected and fails |
| 2 | Page `innerText` at capture time (`.png.txt` beside the image) | `gates/image_scan.py` sibling check | Exact text, so small type is caught. The practical first defense |
| 3 | Image OCR | `gates/image_scan.py` OCR | Misses text under about 16 physical px. Supplementary |

Further rules:

- A one-time secret with no fixed pattern (temporary password, install token) goes into the run denylist **before** it appears on screen. Do not dump page text from a screen that shows a secret.
- Distinguish identity IDs (account, user) from operational random IDs (session, message, tool call). Derive the operational UUID allowlist from structure (JSON keys, columns), confirm it does not overlap identity columns, the roster or the denylist, and regenerate it each time data is reloaded.
- When replacing real account values with roster values, sweep by schema catalog (all schemas, JSON columns, archive queues, aggregate keys), not by a hand-written table list. After replacing, find what leaks the values again (such as a background poller), block it at the source, and watch for new rows over a set period.
- Replacing values in the DB comes first. Replacing DOM text just before capture is for values the DB cannot reach. Image editing is the last resort and is recorded when used.

## Human gates

The agent does not decide these. Ask the user, outside the workflow.

| Gate | Why |
|---|---|
| Real-account login | The agent never reads or copies credentials; it checks only that files exist |
| Gate exemption, acceptance of a violation | Self-approval is forbidden. It may be refused, and a refused violation is recorded as permanent |
| Production data scope | A human chooses what is read (`docs/prod-access.md`) |
| Final image approval | Present as a one-page list; register hashes after approval |
| Irreversible publication | Push, PR merge, mirror export, issue filing |
| Course correction | When the user says "that is not it", revise the plan and record it in the decision log |
