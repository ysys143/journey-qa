# Runners

Run mode executes a scenario file (`scenarios/schema.json`) without a model: steps, wait-based expectations, captures with masks, failure evidence, and a `results.json`. One engine-neutral interpreter drives one of two interchangeable backends: ego-browser (a real-profile engine) and Playwright (an isolated engine). Which kind to use where, and how login state moves between them, is in `docs/engines.md`.

```
runners/
  run.sh                   Entry point: validate, convert YAML to JSON, write config, run, compare baselines
  run-engine.mjs           Loads the config and the backend, runs the interpreter
  lib/run.mjs              Interpreter: personas, actions, expectations, secrets, captures, evidence, results
  lib/page.mjs             In-page functions shared by every backend (masks, scrubbing, settle, nav log)
  lib/files.mjs            Private file writes (login state, run denylist)
  ego/backend.mjs          ego-browser backend, real-profile (runs inside `ego-browser nodejs`)
  playwright/backend.mjs   Playwright backend, isolated (Node, any Chromium channel)
  selftest/                select.sh (engine selection, no browser, run by check.sh); adapter-dir.sh (adapter resolution and an adapter copy outside the repo, run by check.sh); smoke.sh (invented demo app, needs a browser)
```

The runner knows no product. Product knowledge comes from three adapter files: the scenario, its selector map (`selectors` in the scenario), and an optional hooks module.

## Running

```
runners/run.sh --engine auto --pw-module <prefix>/node_modules/playwright/index.mjs --ego-space <task space id> \
  --adapter "$ADAPTER" --scenario <journey> ...   # default: picks the engine and prints why (docs/engines.md "Engine selection")

runners/run.sh --engine ego --ego-space <task space id> \
  --adapter "$ADAPTER" --scenario <journey> --out <run dir>/run-1 \
  --denylist <real denylist> --hook-config <local config.json> \
  --secrets-dir <run dir>/secrets/passwords

runners/run.sh --engine playwright --pw-module <prefix>/node_modules/playwright/index.mjs ...
```

`--adapter DIR` (or `JQA_ADAPTER_DIR`; or `--product NAME` with `--project DIR`) names the adapter directory by the one resolution rule in `adapters/README.md`. A resolved adapter supplies `--policy <adapter>/policy.json` and `--hooks <adapter>/runner/hooks.mjs` when they exist and are not given, and a `--scenario` that is not a file is looked up as `<adapter>/scenarios/<name>[.yaml]`. Without an adapter option, `--scenario`, `--policy` and `--hooks` are plain file paths as before.

All options are listed at the top of `run.sh`. Run outputs:

| Path | Content |
|---|---|
| `results.json` | Per step: ok, time, error (one-time secrets replaced), checks; captures; assertions; unchecked assertions; injected state; notes |
| `shots/NN-slug.png` + `.png.txt` + `.masks.json` | Capture, visible text at the same moment, mask rectangles. PNG metadata already stripped |
| `failure/<step>/` | Failure evidence, present only when it passed `capture/evidence.py` |
| `secrets/` (mode 700) | `state-<persona>.json` login state, `run-denylist.txt` one-time values. Delete at wrap-up |
| `engine.json` | Chosen engine, the requested value (`auto`, `ego` or `playwright`) and the reason; the reason is also the first line of `run.log` |
| `run.log`, `config.json`, `scenario.json` | Console log, effective configuration, scenario as JSON |
| `img_diff.txt` | Baseline comparison output when `--baselines` is given |

Exit codes: 0 passed, 1 failed (a step, an assertion, an unchecked assertion, or the baseline comparison), 2 usage or configuration error before the browser started, 3 stopped at a human gate.

## What the interpreter does

| Scenario item | Behavior |
|---|---|
| `determinism` | Viewport, device scale factor, locale, timezone and color scheme are pinned on the engine. The browser clock is not frozen; `time_anchor` applies to generated data |
| `environment.instances` | Base URLs; `--base ID=URL` overrides one |
| `setup[]` | Runs the hooks module's `setup[<id>]`. A setup item without a hook fails the run |
| Persona change | The current persona's login state is saved to `secrets/state-<persona>.json`. Switching back restores it; with `--state-from DIR`, a persona's first appearance injects `DIR/state-<persona>.json` |
| `goto`, `click`, `fill`, `press`, `select`, `wait` | Browser actions. Values may use `{{persona.<id>.<field>}}` and `{{secret.<name>}}` |
| `request` | A same-origin `fetch` with the page's cookies; `expect.status` checks it |
| `expect` | `url` (pathname equals), `url_pattern` (regular expression on the pathname), `visible`, `hidden`, `text`. Every expectation is a wait with a timeout, never a fixed delay |
| `secrets.to_run_denylist` | Before the action, the locators' text is made transparent; after it, the text is read, kept in memory, and replaced in the DOM. `capture_as` names the first value for later `{{secret.<name>}}` |
| `shot` | Waits for `wait_for`, applies `view_state.scroll` and `theme`, checks `no_truncate`, then captures (one file per `variants` entry) |
| `human_gates` | The run stops before the gate's step (exit 3) unless `--approved-gate <id>` says a person completed it |
| `assertions` | Checked by hooks with `ctx.assert(id, ...)`. A declared assertion that no hook checked fails the run |

Not supported, rejected before the browser starts (exit 2): terminal steps (they need a pty driver; terminal images come from `capture/render_terminal.py`), and locator forms other than `@name`, `css=`, `text=` and `url:`. CSS locators may use the subset both engines accept: plain CSS, a trailing `:has-text()` or `:text-is()`, and `>> nth=N`.

### Locators

A locator is `@name` or `@name(arg)` from the selector map, `css=<selector>`, `text=<text>` (page text contains it), or `url:<path prefix>` (in `wait_for`). The selector map is JSON:

```json
{
  "selectors": { "login.email": "input#email", "user-menu": "header button:has-text(\"{arg}\")" },
  "mask": { "selectors": [".chart-plot"], "text": ["\\d+ items left"] }
}
```

`{arg}` is replaced by the reference's argument, after `{{persona.*}}` templates in it resolve. `mask` adds regions and text patterns to the default masks (canvas, video, iframe, times, dates, durations, counters). `scenarios/validate.py` checks every reference.

### Secrets

`{{secret.<name>}}` resolves, in order, to a value captured earlier in the run (`capture_as`), the file `<--secrets-dir>/<name>`, or the hooks module's `secret(name, ctx)`. Every resolved value is a one-time secret for the rest of the run: no capture is written while the page text contains one, error messages have them replaced, and failure evidence is redacted and gated against them. A fill that uses a secret, and the click or key press right after it, run outside the engine's trace and event buffer.

### Captures

Before each capture the pointer moves to the lower-left corner (a pointer left over a chart opens a tooltip), transitions and animations are disabled, and the runner waits until the main region's text and height stop changing (short evaluates polled from Node; some engines limit how long one evaluate may run). It then takes the screenshot, saves `innerText` with no action in between, collects masks, and strips the PNG with `capture/strip_png.py`. Captures are not gated by the runner; phase 4 gates them (`docs/workflow.md`).

### Failure evidence

The first failing step collects evidence while the page still shows the failure, following `capture/SPEC.md` "Failure evidence": password inputs are cleared and one-time secrets masked, session cookie values (16+ characters) from the current and saved login states become run secrets, the backend writes its raw evidence (no video), and `capture/evidence.py` redacts and gates it with the run's roster, denylists, policies and run denylist. Unless that exits 0, the directory is deleted and `results.json` says so. `--inject-failure <step>` replaces every CSS locator inside that step with one that matches nothing, to drill this path.

| Backend | Raw evidence |
|---|---|
| ego | Screenshot, page text, accessibility snapshot, page info, CDP events reduced to method, path and status |
| Playwright | Screenshot, page text, page HTML (only after scrubbing succeeded), console log, URL, trace archive without screenshots (its call-stack file, which records the runner's own paths, is dropped) |

### Baselines

With `--baselines ROOT`, a passing run is compared with `ROOT/<engine>/<scenario-id>/` by `gates/img_diff.py`, threshold `baseline.max_diff_ratio` (default 0.01). Baselines are per engine; see `capture/SPEC.md` "Masks and baseline comparison".

## Hooks

An adapter's hooks module default-exports an object; every key is optional.

```js
export default {
  engine: { cookiePaths: ['/'], stateLoadPath: '/' }, // ego: extra cookie paths to clear and export; a non-app path to set localStorage on
  capture: { pointer: { x: 2, y: 896 } },              // where the pointer rests during captures
  setup: { '<setup id>': async (ctx) => {} },          // one per scenario setup item
  before: { '<step id>': async (ctx) => {} },          // before the step's action
  after: { '<step id>': async (ctx) => ({}) },         // after its expectations, before its shot; returned object goes to checks
  secret: async (name, ctx) => undefined,              // values no file provides (for example a generated new password)
  teardown: async (ctx) => {},                         // always runs, also after a failure
};
```

`ctx` offers: `config` (the `--hook-config` JSON), `out`, `people`, `person(id)`, `base(instance)`, `selector(ref)`, `persona(id)`, `goto(path, instance)`, `click`, `fill`, `waitFor`, `evaluate(fn, arg)`, `url()`, `settle()`, `navLog()` (pathnames this tab showed, client-side redirects included), `sensitive(fn)`, `secret(value, name)`, `note(key, value)`, `exec(file, args, {input})`, and `assert(id, cond, message)`. Machine-specific values (database command, script paths, thresholds) belong in the hook config file, not in the repository.

## Smoke test

```
runners/selftest/smoke.sh --engine playwright --pw-module <path to playwright or playwright-core index.mjs> [--pw-channel chrome]
runners/selftest/smoke.sh --engine ego --ego-space <task space id>
```

Serves `selftest/app/` (an invented app) on a free loopback port and checks: a clean pass with captures, masks and a masked one-time code; a second run compared with the first as baselines; an injected failure whose evidence is kept and passes a rescan; the same failure with a denylist that the page trips, whose evidence is deleted; and a run with injected login state. It needs a browser, so `check.sh` runs only `node --check` on the runner files and `selftest/select.sh`, which checks `--engine auto` with a stubbed `PATH`, `CI` variable and terminal state.
