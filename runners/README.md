# Runners

Run mode executes a scenario file (`scenarios/schema.json`) without a model: steps in scenario order, wait-based expectations, captures with masks, failure evidence, and a `results.json`. Browser steps run on one of two interchangeable backends: ego-browser (a real-profile engine) and Playwright (an isolated engine). Which kind to use where, and how login state moves between them, is in `docs/engines.md`. Terminal steps run through the drivers in `terminal/` and need no browser: a scenario made only of terminal steps runs with engine `none`, and a scenario that mixes both uses the browser engine for its browser steps only.

```
runners/
  run.sh                   Entry point: validate, convert YAML to JSON, write config, run, compare baselines
  run-engine.mjs           Loads the config and the backend, runs the interpreter
  lib/run.mjs              Interpreter: personas, actions, expectations, secrets, captures, evidence, results
  lib/terminal.mjs         Terminal steps: tool preflight, one driver call per step, secret hand-off, terminal shots
  lib/page.mjs             In-page functions shared by every backend (masks, scrubbing, settle, nav log)
  lib/files.mjs            Private file writes (login state, run denylist)
  ego/backend.mjs          ego-browser backend, real-profile (runs inside `ego-browser nodejs`)
  playwright/backend.mjs   Playwright backend, isolated (Node, any Chromium channel)
  terminal/                exec_driver.py, pty_driver.py, tmux_driver.py (terminal steps), normalize.py, common.py
  selftest/                terminal.sh (terminal drivers, normalizer and renderer controls; needs tmux 3.2+; run by check.sh); orchestrate.sh (scenario runs through run.sh: terminal-only with no browser, run by check.sh; terminal + browser against the demo app when engine options are given, otherwise reported as skipped); select.sh (engine selection, no browser, run by check.sh); adapter-dir.sh (adapter resolution and an adapter copy outside the repo, run by check.sh); smoke.sh (invented demo app, needs a browser)
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

runners/run.sh --scenario <terminal-only journey> --out <run dir>/run-1 --denylist <real denylist> \
  --hook-config <local config.json> --secrets-dir <run dir>/secrets/passwords   # no engine option: none is needed
```

`--adapter DIR` (or `JQA_ADAPTER_DIR`; or `--product NAME` with `--project DIR`) names the adapter directory by the one resolution rule in `adapters/README.md`. A resolved adapter supplies `--policy <adapter>/policy.json` and `--hooks <adapter>/runner/hooks.mjs` when they exist and are not given, and a `--scenario` that is not a file is looked up as `<adapter>/scenarios/<name>[.yaml]`. Without an adapter option, `--scenario`, `--policy` and `--hooks` are plain file paths as before.

All options are listed at the top of `run.sh`. Run outputs:

| Path | Content |
|---|---|
| `results.json` | Per step: ok, time, error (one-time secrets replaced), checks; captures; assertions; unchecked assertions; injected state; notes |
| `shots/NN-slug.png` + `.png.txt` + `.masks.json` | Capture, visible text at the same moment, mask rectangles. PNG metadata already stripped. A terminal shot has `.png.txt` (the joined transcript) and `.png.render.json` (width and wrapping) instead of masks |
| `failure/<step>/` | Failure evidence, present only when it passed `capture/evidence.py` |
| `secrets/` (mode 700) | `state-<persona>.json` login state, `run-denylist.txt` one-time values. Delete at wrap-up |
| `engine.json` | Chosen engine (`none` for a terminal-only scenario), the requested value (`auto`, `ego` or `playwright`) and the reason; the reason is also the first line of `run.log` |
| `terminal/<step>.txt`, `.result.json` | Per terminal step: the normalized, redacted, gated transcript (absent when a gate or the secret count failed) and the driver's verdict. `terminal/shots/` holds the joined transcripts the terminal shots are drawn from |
| `run.log`, `config.json`, `scenario.json` | Console log, effective configuration, scenario as JSON |
| `img_diff.txt` | Baseline comparison output when `--baselines` is given |

Exit codes: 0 passed, 1 failed (a step, an assertion, an unchecked assertion, or the baseline comparison), 2 usage or configuration error before the first step (an invalid scenario, an unsupported locator, a tool a step needs that is missing, such as `tmux` for a tmux step), 3 stopped at a human gate.

## What the interpreter does

| Scenario item | Behavior |
|---|---|
| `determinism` | Viewport, device scale factor, locale, timezone and color scheme are pinned on the engine. The browser clock is not frozen; `time_anchor` applies to generated data |
| `environment.instances` | Base URLs; `--base ID=URL` overrides one |
| `setup[]` | Runs the hooks module's `setup[<id>]`. A setup item without a hook fails the run |
| Persona change | The current persona's login state is saved to `secrets/state-<persona>.json`. Switching back restores it; with `--state-from DIR`, a persona's first appearance injects `DIR/state-<persona>.json` |
| `surface: terminal` | Runs the step's `driver` (`exec` by default, `pty`, `tmux`) with the driver's own fields (see "Terminal steps in a scenario"). Persona login state is not touched |
| `goto`, `click`, `fill`, `press`, `select`, `wait` | Browser actions. Values may use `{{persona.<id>.<field>}}` and `{{secret.<name>}}` |
| `request` | A same-origin `fetch` with the page's cookies; `expect.status` checks it |
| `expect` | `url` (pathname equals), `url_pattern` (regular expression on the pathname), `visible`, `hidden`, `text`. Every expectation is a wait with a timeout, never a fixed delay |
| `secrets.to_run_denylist` | Before the action, the locators' text is made transparent; after it, the text is read, kept in memory, and replaced in the DOM. `capture_as` names the first value for later `{{secret.<name>}}` |
| `shot` | Waits for `wait_for`, applies `view_state.scroll` and `theme`, checks `no_truncate`, then captures (one file per `variants` entry). A shot with `terminal_from` is drawn after the run instead |
| `human_gates` | The run stops before the gate's step (exit 3) unless `--approved-gate <id>` says a person completed it |
| `assertions` | Checked by hooks with `ctx.assert(id, ...)`. A declared assertion that no hook checked fails the run |

Not supported, rejected before the first step (exit 2): locator forms other than `@name`, `css=`, `text=` and `url:`. CSS locators may use the subset both engines accept: plain CSS, a trailing `:has-text()` or `:text-is()`, and `>> nth=N`.

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

## Terminal drivers

`runners/terminal/` holds one driver per kind of terminal step. Each is a CLI (`--step STEP.json --out DIR`, plus `--denylist`, `--secrets-dir`, `--roster`, `--policy`, `--wrapper`) and exits 0 passed, 1 failed, 2 cannot run. Default to exec; use pty when the program asks questions; use tmux when the interactive session itself is under test. The selection rule, the decision matrix and the pitfalls are in `docs/terminal.md`.

| Driver | Step fields | Completion and verdict |
|---|---|---|
| `exec_driver.py` | `cmd`, `expect_exit`, `stdout_contains`, `timeout_s`, `capture_as` | Exit code. No tty, no secret input |
| `pty_driver.py` | `cmd`, `dialog` (`wait_for`, `send`, `secret`), `expect_exit`, `stdout_contains`, `timeout_s`, `capture_as` | Exit code. An answer is written only after its `wait_for` matches |
| `tmux_driver.py` | `launch`, `size`, `keys` (`literal`, `key`, `paste_buffer`, `wait_for`), `secret_classes`, `done_when`, `timeout_s`, `verify`, `verify_note`, `expect_exit`, `cleanup`, `capture_as` | `done_when` polled with a ceiling, then the `verify` checks (file, api, sql) on durable state. The screen is evidence, not the verdict |

All three write the same transcript: `$ <cmd or launch>`, the normalized output, and `exit=<n>` or `status=done|timeout|not-verified`. The pipeline is: scrub secret bytes from the raw output, write the raw transcript (mode 600, `<out>/secrets/terminal/`), `normalize.py`, `capture/redact.py`, `gates/leakscan.py` with the denylists and the run denylist, then a count of every secret in every file the run wrote (must be 0). A failing gate or a nonzero count fails the step and the redacted transcript is not kept. Results are in `<out>/terminal/<step>.result.json`.

- **Secrets** come from `<--secrets-dir>/<name>` (mode 600, refused otherwise), live in memory, and never reach argv, the environment or a file. pty writes them to the pty after the prompt appears. tmux loads them with `load-buffer -b <name> -` (value on stdin) and pastes with `paste-buffer -d -b <name>`; it never uses `send-keys` for a secret, so it also works when the program echoes its input. The item is `{paste_buffer: <secret name>}` (`{secret: <name>}` is an alias). After every paste the driver asserts that `list-buffers` is empty and that the value is absent from `capture-pane -p -S - -E -`; if the program echoed the value it runs `clear-history` (and resets the pane when the value is still on the visible rows), records `[omitted: echoed secret cleared]` in the transcript and checks again. A value that survives, or a buffer left behind, fails the step.
- **Secret classes**: the step's `secret_classes` (`name: test | real`, filled from the scenario's `secrets:` declaration) decides what a driver may type. A `class: real` secret is refused on every driver (a human enters it). A `class: test` secret may appear as `{{secret.<name>}}` inside a tmux `literal` item (it then travels as send-keys argv; it is still scrubbed from every written file and added to the run denylist). `{{secret.*}}` in `launch`, `cmd`, `wait_for`, `done_when`, `verify` and `cleanup` is refused for every class.
- **Remote environments**: `--wrapper` is a command from the adapter's local hook config. The step command reaches it as one argument, `<wrapper> bash -lc '<cmd>'`, so `&&`, `;`, `~` and `$VAR` run in the remote shell. pty and tmux need a wrapper that allocates a tty.
- **tmux** starts one dedicated server per step on a short socket (`-S <--socket-dir>/<--run-id>`, default `/tmp/jqa-<uid>`, `-f /dev/null`, never the default server), exits 2 when the socket path would exceed the unix limit, keeps the pane with `remain-on-exit` to read `#{pane_dead_status}`, polls `capture-pane -p -J -S - -E -` with the step's ceiling, and kills the server on every path, including SIGTERM, SIGINT and SIGHUP sent to the driver (exit 2). `--blocked-pattern` names the screen text an outer policy layer prints when it refuses an action; such a step ends `status=not-verified` and never passes. `cleanup` commands revert state the run created, also after a failure.
- **Normalizer**: `normalize.py` removes escape sequences, folds `\r` overwrites and backspaces, expands tabs; it never shortens a line.

`selftest/terminal.sh` runs every driver against invented fake programs (`selftest/terminal/`) with a must-pass and a must-fail control for each behavior.

### Terminal steps in a scenario

Steps run in scenario order. A terminal step (`surface: terminal`, `action: run`) names its driver in `driver` (default `exec`) and carries that driver's fields from `scenarios/schema.json`; `lib/terminal.mjs` writes them to a step file (mode 600, `<out>/secrets/terminal-steps/`) and calls the driver CLI. The driver's verdict (`<out>/terminal/<step>.result.json`) is the step's verdict. A failed step stops the run like a failed browser step, and its evidence is the kept transcript (no browser capture is taken for it). Browser state, including every persona's login, stays as it was while terminal steps run.

- **Preflight (exit 2 before the first step)**: `python3` 3.10+ for any terminal step; `tmux` 3.2+ for a tmux step; `rsvg-convert` when a terminal shot is used. The message names the missing tool and the step that needs it. Nothing else is checked, so a scenario with no tmux step runs without tmux.
- **No browser engine** is selected, probed or required when every step is a terminal step (`engine.json` says `none`). With one browser step the engine is selected as before.
- **Adapter-local settings** (the machine's remote shell, policy-layer text, database command, socket directory) come from the hook config's `terminal` object, never from the scenario: `wrapper` (string or argument list; the step command reaches it as one argument), `blocked_pattern`, `sql_command`, `socket_dir`, `tmux` (binary).
- **Secrets**: `{{secret.<name>}}` and `{paste_buffer: <name>}` resolve like browser secrets (captured earlier in the run, `<--secrets-dir>/<name>`, or the hooks module's `secret()`). The runner copies each value a step names into `<out>/secrets/values/<name>` (mode 600) for the driver. A value the driver captures (`secrets.capture_as: {name, pattern}`) joins this run's secrets, so later browser captures and failure evidence are gated against it; the run denylist is merged both ways around every terminal step. Declared classes (`secrets:` at scenario level) are passed to the driver; `class: real` is refused.
- **Hooks**: `before` and `after` entries for a terminal step id run as for a browser step. `reconnect: true` on a terminal step restarts the wrapper session before that step, for session caches that keep stale state such as group membership. When the adapter configures a terminal `wrapper`, the hooks module must provide `reconnect(ctx, step)`: it runs before the step's driver starts, and a missing hook is a configuration error (exit 2 before the first step, naming the step) while a hook that throws fails that step and the run (exit 1). Without a `wrapper`, each driver call already starts a fresh process (exec, pty) or a fresh dedicated tmux server (tmux), so nothing is stale and the flag needs no hook (a configured hook still runs). Without the flag the session is left as it is.
- **Terminal shots** (`terminal_from`, carried by the last step the shot lists): after the last step, in scenario order, the redacted transcripts of the listed steps are joined in the listed order, every `transcript:<text>` entry of `wait_for` must occur in them, the joined text passes the text gate with the run denylist, and `capture/render_terminal.py` draws it. A shot whose steps did not all pass with a kept transcript is not drawn and fails the run. Shots are drawn only when no step failed; a run that stops at a human gate still draws the shots of the steps before it.

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
  reconnect: async (ctx, step) => {},                  // before a terminal step with reconnect: true; required when terminal.wrapper is set
  teardown: async (ctx) => {},                         // always runs, also after a failure
};
```

`ctx` offers: `config` (the `--hook-config` JSON, including its `terminal` object), `out`, `people`, `person(id)`, `base(instance)`, `selector(ref)`, `persona(id)`, `goto(path, instance)`, `click`, `fill`, `waitFor`, `evaluate(fn, arg)`, `url()`, `settle()`, `navLog()` (pathnames this tab showed, client-side redirects included), `sensitive(fn)`, `secret(value, name)`, `note(key, value)`, `exec(file, args, {input})`, and `assert(id, cond, message)`. Machine-specific values (database command, script paths, thresholds) belong in the hook config file, not in the repository.

## Orchestration smoke test

```
runners/selftest/orchestrate.sh                                                       # terminal-only scenarios (what check.sh runs)
runners/selftest/orchestrate.sh --engine playwright --pw-module <path> [--pw-channel chrome]   # also terminal + browser in one scenario
JQA_ORCHESTRATE_ARGS="--engine playwright --pw-module <path>" ./check.sh              # the same inside check.sh
```

Part A runs scenarios made of exec, pty and tmux steps through `run.sh` with no browser engine: order, engine `none`, captured and entered secrets absent from every kept file, terminal shots drawn from the joined transcripts, and a must-fail twin for each of a wrong exit code, a transcript the text gate rejects, a shot text that is missing, an unavailable secret, an invalid step, a fixed sleep, a missing `tmux` or `rsvg-convert` (exit 2, tool named) and a scenario with a browser step and no usable engine. Part B runs terminal and browser steps in one scenario against the demo app: order across both kinds, a secret captured on the page typed into a pty prompt, browser state surviving the terminal steps, and a twin whose terminal step judges the app state wrongly. Without engine options Part B prints `SKIPPED` and does not count as run.

## Smoke test

```
runners/selftest/smoke.sh --engine playwright --pw-module <path to playwright or playwright-core index.mjs> [--pw-channel chrome]
runners/selftest/smoke.sh --engine ego --ego-space <task space id>
```

Serves `selftest/app/` (an invented app) on a free loopback port and checks: a clean pass with captures, masks and a masked one-time code; a second run compared with the first as baselines; an injected failure whose evidence is kept and passes a rescan; the same failure with a denylist that the page trips, whose evidence is deleted; and a run with injected login state. It needs a browser, so `check.sh` runs only `node --check` on the runner files and `selftest/select.sh`, which checks `--engine auto` with a stubbed `PATH`, `CI` variable and terminal state.
