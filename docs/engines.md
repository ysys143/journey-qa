# Run engines

Run mode drives a browser through one of two kinds of engine behind one scenario format (`scenarios/schema.json`) and one interpreter (`runners/`). A scenario never names an engine; `runners/run.sh --engine auto` (the default) chooses per run by where the run executes, and `--engine ego|playwright` pins one. The measurements behind this split are in `docs/decisions/0001-run-engines.md`.

## Engine kinds

| Kind | What it offers | Use it for |
|---|---|---|
| **Real-profile engine** | Drives a headed browser in an existing browser profile through CDP. A person can watch the window and take over the same session | Local runs on a workstation, and any step with a person in the loop (real-account login, a handoff to a person) |
| **Isolated engine** | Launches its own browser, headless or headed, with a fresh context per persona. Nothing is shared with any other browser or run | CI, headless servers, fresh-profile runs, several runs in parallel |

Two backends ship, one of each kind, and they are interchangeable behind the runner: **ego-browser** (`--engine ego`, real-profile) and **Playwright** (`--engine playwright`, isolated). A backend for another tool of either kind exports the same `createBackend(cfg, { determinism })` as `runners/playwright/backend.mjs` and `runners/ego/backend.mjs` and is registered in `runners/run.sh` and `runners/run-engine.mjs`.

## Engine selection

`--engine auto` is the default. It first detects what is usable: the real-profile engine needs `ego-browser` on `PATH` and `--ego-space`; the isolated engine needs `--pw-module` to name an existing Playwright entry module. Then it picks by where the run executes:

| Context | Result |
|---|---|
| Interactive (no `CI` variable, stdin and stdout are terminals) and the real-profile engine is usable | `ego` |
| CI or headless (`CI` set to a value other than empty, `0` or `false`, or no terminal) and the isolated engine is usable | `playwright` |
| Real-profile engine not usable and the isolated engine is usable | `playwright` |
| No usable engine for the context | exit 2, naming what to install |

In CI or headless without a usable isolated engine, the run stops instead of starting a real-profile engine that needs a desktop session; pass `--engine ego` to override. The chosen engine and the reason are printed, written to `<run>/engine.json` and to the first line of `<run>/run.log`, and recorded in the report's "Engine" line, because baselines are per engine. `runners/selftest/select.sh` checks the selection without a browser.

Both engines pass the same journeys with the same scenario. What differs is isolation, the failure evidence each engine can produce, and font rasterization, which is why baseline images are per engine (below).

## Engine notes

**ego-browser** (real-profile)

- A task space shares an existing browser profile. The runner clears only the scenario origins' cookies and localStorage when it switches persona; it never clears the profile.
- Cookies ignore ports. A login to a server on the loopback address overwrites the session cookies of every other local server on that address. Give the instance under test its own host name, for example a `*.localhost` alias (Chromium resolves `*.localhost` to the loopback address), and put that name in the scenario's `base_url`.
- Viewport, scale, locale, timezone and color scheme are set through CDP emulation at the start of every run. `navigator.language` follows only the `acceptLanguage` of the user-agent override.
- `page.screenshot()` returns CSS-pixel size; the backend captures through CDP `Page.captureScreenshot` to honor the device scale factor.
- The `ego-browser nodejs` runtime has no `process.argv` or `process.env`; `runners/run.sh` prepends the configuration as a global.
- One `evaluate` call has a time limit. Poll from Node with short evaluates; never loop inside the page.
- A page that polls the server never reaches network idle. Wait for elements and for the main region to stop changing, never for network idle.
- Raw CDP events carry request bodies and Set-Cookie headers. The backend keeps only method, path and status in evidence and drains the event buffer after sensitive steps.

**Playwright** (isolated)

- Install it into a temporary prefix (`npm install --prefix <dir> playwright`, browsers under `PLAYWRIGHT_BROWSERS_PATH`), not globally. `--pw-channel` can use an installed browser channel instead of a downloaded one.
- Each persona gets a fresh context; determinism values are context options.
- The trace records DOM snapshots and network bodies, including a login request even when tracing was split into chunks around it. The backend stops the trace chunk during sensitive steps, records no screenshots in the trace and no video, and the evidence step redacts and gates the archive (`capture/SPEC.md` "Failure evidence").
- Its first page load in a fresh browser is slower and less steady than later ones (empty cache, trace recording); budget timeouts for it.

## Login state

A run saves each persona's login when it switches away from it and at the end: `<run>/secrets/state-<persona>.json`, mode 600, in Playwright `storageState` format (`{cookies: [...], origins: [{origin, localStorage: [...]}]}`). It contains live session cookies: keep it under `secrets/` and delete it at wrap-up.

| | Save | Inject |
|---|---|---|
| Playwright | `context.storageState()` | `browser.newContext({ storageState })` |
| ego-browser | Cookies of the scenario origins through CDP `Network.getCookies` (host-only cookies of those hosts); localStorage through `page.evaluate` on an open page of the origin | Clear the origins' cookies and localStorage, `Network.setCookie` for each cookie, then open a non-app path on the origin (the adapter's `stateLoadPath`) and set localStorage with `page.evaluate` |

**Exchange between engines.** Because ego writes the Playwright format, a state file saved by either engine can be injected into the other: `runners/run.sh --state-from <other run>/secrets`. A persona's first appearance in the run then starts logged in, and a scenario written for injected state skips the login steps.

**Session expiry.** Injected state can be older than the product's access-token lifetime.

1. Detect: after injection, read the tab's navigation log (`ctx.navLog()` in hooks). A login path in it means the state did not hold.
2. If the product refreshes access silently from a longer-lived refresh credential, an expired access token is not a failure; record that the refresh happened.
3. If the state is rejected, log in again through the scenario's login steps (or a fresh run without `--state-from`) and save a new state. Never extend a session by editing cookie expiry.

The adapter records the product's token lifetimes and refresh behavior.

## Baselines

Captures are compared with human-approved baselines of the **same engine**: `baselines/<engine>/<scenario-id>/`. Fonts rasterize differently between engines and between headed and headless modes, by more than the default 1% threshold, so a cross-engine comparison says nothing about the product. Changing the run engine means capturing and approving a new baseline set. Method and threshold: `gates/SPEC.md` "img_diff.py".
