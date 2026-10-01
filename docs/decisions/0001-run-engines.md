# 0001 Run engines: a real-profile engine locally, an isolated engine in CI

Status: accepted

## Context

Run mode replays a scenario without a model. It needs two things that one engine kind does not give together: runs a person can watch and take over in an existing browser profile, and runs that share nothing with any other browser or run. Two backends were measured, one of each kind: ego-browser (real-profile: drives a headed browser in an existing profile through CDP) and Playwright (isolated: its own browser with fresh contexts, headless or headed, needs a separate install). Both ran the same two journeys of a test product, written once in the scenario format and executed by one engine-neutral runner:

- a multi-page tour: a read-only tour of an operating five-person team on a demo instance with generated data: two personas, logins, four screens, then the same screens again with injected login state;
- an onboarding journey that changes data: an admin adds a temporary user, the user signs in with a one-time password, is forced to change it, the old password is rejected, and cleanup removes the user.

Each engine ran each journey three times, plus an injected-failure run per engine, state injection with state older than the access-token lifetime, and state exchanged between engines.

## Measurements

| | Pass | Mean time (min-max), 3 runs | Flaky steps |
|---|---|---|---|
| Playwright, tour | 3/3 | 56.0 s (46.6-69.4) | none |
| ego-browser, tour | 3/3 | 47.0 s (45.9-47.9) | none |
| Playwright, onboarding | 3/3 | 31.0 s (26.9-33.4) | none |
| ego-browser, onboarding | 3/3 | 32.8 s (30.0-35.5) | none |

Times are browser steps only; the data re-anchoring before the tour (46-62 s) is engine-independent and excluded. Playwright's slow steps were the first page of a fresh browser (mean 10.0 s, 4.5-19.5) and the first page with injected state (15.2 s); every other step differed by under a second between engines.

Masked pixel comparison (share of unmasked pixels whose largest RGB channel difference exceeds 24):

| Comparison | Largest share |
|---|---|
| Same engine, run 1 against runs 2 and 3 | at most 0.09%, except one screen whose content changes with every data re-anchor (1.1%) |
| Same engine, login run against injected-state run | 0.02% |
| Same engine, onboarding runs against each other | 0.00% |
| Playwright headless shell against full Chromium under Playwright | 0.01-0.04% |
| Playwright against ego-browser, same screens | 1.2-3.2% |

Login state: Playwright `storageState` and the ego export in the same format both injected cleanly; state from each engine worked in the other; state older than the access-token lifetime was refreshed silently by the product in all four combinations.

Failure evidence from the injected failure: Playwright produced a screenshot, page text, HTML, console log, a 5.1 MB trace and a 1.9 MB video; ego-browser a screenshot, page text, accessibility snapshot, page info and an event log. Both engines' raw formats carried request bodies (the trace archive even with tracing split around the login; the raw CDP event buffer), so raw evidence is never kept as recorded. Screenshots taken by ego through CDP carry the display's ICC profile.

Install: Playwright in a temporary prefix took 592 MB, 572 MB of it browsers. ego-browser was already present on the measuring machine, so its install size was not measured.

## Decision

- Keep both engines behind one scenario format and one interpreter (`runners/`); scenarios never name an engine.
- The engine follows where the run executes, not a preference for one tool. **Real-profile engine** (shipped backend: ego-browser): local runs on a workstation and any step with a person in the loop. **Isolated engine** (shipped backend: Playwright): CI, headless servers, fresh-profile runs, parallel runs.
- Neither measured backend was faster overall or more reliable (table above); either kind can be replaced by another backend that implements the same interface.
- Baselines are per engine. The cross-engine difference (1.2-3.2%) exceeds the 1% threshold while same-engine reruns stay at or below 0.09%, so a baseline approved on one engine cannot judge the other; it comes from font rasterization (headed against headless), not from the product.
- Failure evidence is redacted and gated before it is kept (`capture/evidence.py`); no video is recorded; every PNG is stripped, failure screenshots included.

## Consequences

- Moving a scenario from local runs to CI means capturing and approving a Playwright baseline set.
- Real-profile runs share a browser profile: each instance under test needs its own host name so its cookies do not overwrite other local sessions (`docs/engines.md`).
- Evidence is smaller than what the engines can record (no video, no trace screenshots, reduced event logs) in exchange for evidence that can be kept at all.
