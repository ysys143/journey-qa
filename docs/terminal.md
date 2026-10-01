# Terminal steps

A terminal step (`surface: terminal`, `action: run`) runs through one of three drivers, named by `driver` in the step (`runners/terminal/`, `runners/README.md` "Terminal drivers"). Pick the cheapest driver that still exercises what the journey is about.

## Selection rule

1. Default to **exec**. Every command that needs no interaction runs there.
2. Use **pty** when the program asks questions: known prompts, a password, a y/n.
3. Use **tmux** when the interactive session itself is under test: full-screen programs, interactive command-line clients, trust or consent dialogs, hooks or plugins that run only in an interactive session, multi-turn flows.

Move up only when the lower driver cannot reach the behavior. Each step up buys fidelity and costs determinism.

## Matrix

| | exec | pty | tmux |
|---|---|---|---|
| Use for | Default; every command without interaction | Short interactive programs with known prompts | Long-lived interactive sessions used as people use them |
| Fidelity | Low: no tty, so colours, progress output and prompts may change or hang | Medium: a real tty, but screen drawing must be interpreted | High: a real terminal |
| Completion | Exit code | Process exit and exit code; prompt matching with timeouts | No end signal: poll the screen for `done_when` with a timeout ceiling, never a fixed sleep alone; `remain-on-exit` and `pane_dead_status` when the program exits |
| Output record | Full stdout and stderr, exact | Full; contains ANSI sequences and `\r` overwrites, so it is normalized | Only what the screen shows; history depends on scrollback and the capture range |
| Secrets | Never in argv or the environment (visible in `ps` and shell history) | File (mode 600), then memory, then the pty after the prompt appears; the transcript shows a placeholder; defensive byte replace before any write; re-scan | File (mode 600), then memory, then `tmux load-buffer -b <name> -` (value on stdin, never in argv), then `paste-buffer -d -b <name>` (buffer deleted) after the prompt appears; never `send-keys` for a secret; captures get the defensive byte replace before any write, then the occurrence count; works even when the program echoes its input |
| Determinism | High | High (breaks if prompt wording changes) | Low: timing, window size and redraws |
| Verdict source | Exit code and follow-up commands | Exit code and follow-up commands | Durable state (files, API, database) through `verify`, not the screen |
| Image source | Transcript, normalize, redact, text gate, render | Same | Plain capture, normalize, redact, gate, render. Colour rendering is not supported |
| Remote (VM, ssh, container) | The whole command as ONE quoted argument for the remote shell (`<wrapper> bash -lc '<cmd>'`); an unquoted `&&`, `;`, `~` or `$VAR` would run on the host | Run the remote command with a tty flag (`ssh -t`, a VM shell with a tty) under the pty | tmux inside the remote environment, or the remote login inside a local tmux pane |

## Common behavior

- Every driver is a CLI: `python3 runners/terminal/<driver>_driver.py --step STEP.json --out DIR` (plus `--denylist`, `--secrets-dir`, `--roster`, `--policy`, `--wrapper`; `--help` lists all). Exit 0 passed, 1 failed, 2 cannot run. A scenario run through `runners/run.sh` builds the step file and calls the driver for you.
- Every driver writes the same transcript: `$ <cmd or launch>`, the normalized output, then `exit=<n>` or `status=done|timeout|not-verified`. An omission is written as `[omitted: <reason>]`, never as a summary.
- Every driver runs the same pipeline: scrub secret bytes, write the raw transcript (mode 600, under `secrets/`), `runners/terminal/normalize.py`, `capture/redact.py`, `gates/leakscan.py` with the run denylist, then count each secret in every file the run wrote (must be 0). A failing gate fails the step and the transcript is not kept.
- Adapter-local settings (the remote shell wrapper, the text a policy layer prints when it refuses an action, the database command, the socket directory) live in the hook config's `terminal` object, never in the scenario.

## exec

Fields: `cmd`, `expect_exit` (default 0), `stdout_contains`, `timeout_s` (a ceiling).

```yaml
- id: client-version
  surface: terminal
  action: run
  driver: exec
  cmd: "example-app --version"
  expect_exit: 0
  stdout_contains: ["example-app"]
```

The verdict is the exit code, then each `stdout_contains` entry against the normalized, redacted text. A command that is only a fixed wait (`sleep 30`) is rejected by the validator. exec never receives a secret.

## pty

Fields: `cmd`, `dialog`, `timeout_s` (per prompt and overall), `expect_exit`, `stdout_contains`.

```yaml
- id: client-init
  surface: terminal
  action: run
  driver: pty
  cmd: "example-app init"
  dialog:
    - { wait_for: "Server URL", send: "http://localhost:18080" }
    - { wait_for: "Password", send: "{{secret.client_password}}", secret: true }
  expect_exit: 0
  stdout_contains: ["signed in"]
```

An answer is written only after its `wait_for` matches the output seen so far. A `secret: true` answer is read from the secrets directory (mode 600) into memory and written to the pty; the transcript shows `[secret:<name>]`. A prompt that never appears ends the step with `status=timeout`. A wrong password shows up as a nonzero exit code.

## tmux

Fields: `launch`, `size` (`COLSxROWS`, default `220x50`), `keys`, `done_when` (required), `timeout_s`, `verify`, `verify_note`, `cleanup`.

```yaml
- id: ask-one-question
  surface: terminal
  action: run
  driver: tmux
  launch: "example-app chat"
  size: "200x50"
  keys:
    - { wait_for: "Trust this folder" }
    - { key: Down }              # the dialog defaults to "No, exit"
    - { key: Enter }
    - { wait_for: "Ask a question" }
    - { literal: "What is my account name?" }
    - { key: Enter }
  done_when: "Answer saved"
  timeout_s: 180
  verify:
    - { kind: file, path: /home/ivynonrealton/.example-app/sessions.log, contains: "What is my account name?" }
  cleanup:
    - "rm -f /home/ivynonrealton/.example-app/sessions.log"
```

- `keys` items: `{literal: <text>}` is typed as text, `{key: <NamedKey>}` is a named key (`Down`, `Enter`, `Escape`, `C-c`), `{paste_buffer: <secret name>}` pastes a secret through a tmux buffer, `{wait_for: <regex>}` polls the screen before the next key. Send `Enter` as its own item.
- `done_when` ends the session step. The driver polls `capture-pane -p -J -S - -E -` against it with the `timeout_s` ceiling. A `done_when` that never matches ends `status=timeout`.
- `verify` is a list of checks on durable state: `file` (`path`, with `exists`, `contains`, `equals` or `matches`; `remote: true` reads it in the isolated environment), `api` (`url`, `status`), `sql` (`query`, through the hook config's `sql_command`). The verdict comes from these. An empty `verify` needs a `verify_note` that says why.
- The driver starts a dedicated tmux server per step on a short socket (`-S`, never the default server, `-f /dev/null` so no user configuration applies), keeps the pane with `remain-on-exit on`, and kills the server on every path.
- The pane shell starts clean (`env -i`, `NO_COLOR=1`, no shell history).

## Secrets

Two classes, declared at scenario level (`secrets: {name: {class: test | real}}`).

| Class | Meaning | Driver behavior |
|---|---|---|
| `real` | A real account or production token | No driver types it. A person enters it at a human gate. The validator rejects a `real` secret on any driver step |
| `test` | A throwaway value: a fictitious persona's password, a one-time token of a disposable instance | Any driver and channel may enter it. `paste_buffer` is the recommended channel when the value must stay out of argv, scrollback and logs |

In both classes the published artifacts (transcripts, docs code blocks, terminal images) show placeholders, and every entered or captured value goes into the run denylist. A reader cannot tell a mock from a real value, and a disposable instance's credential is live until it is invalidated.

The `paste_buffer` channel: the value is read from the mode-600 file into memory, written to `tmux load-buffer -b <unique name> -` on stdin, then pasted with `paste-buffer -d -b <name> -t <target>`, then `Enter` is sent as a separate key. After every paste the driver asserts that `list-buffers` is empty and that the value is absent from the full scrollback. If the program echoed the input, the driver runs `clear-history`, records `[omitted: echoed secret cleared]` in the transcript and checks again. A `{{secret.*}}` reference inside `launch`, `cmd`, `wait_for`, `done_when`, `verify` or `cleanup` is rejected; inside a tmux `literal` it is allowed only for a `class: test` secret.

A value that a command prints (a one-time token) is captured with `secrets.capture_as: {name, pattern}` on the step: the first group of `pattern` is stored in the run's secrets directory and the run denylist before any transcript is written.

## Pitfalls

- **Remote-shell quoting.** Through a VM or ssh wrapper, pass the whole command as one quoted argument: `<wrapper> bash -lc '<cmd>'`. Unquoted `&&`, `;`, `~` and `$VAR` are interpreted by the host shell and run there, on the wrong machine. The drivers quote with `shlex.quote`.
- **Session caches keep stale state.** A long-lived login session may keep an old group membership after provisioning added one. Set `reconnect: true` on the step: the runner calls the hooks module's `reconnect(ctx, step)` before the step, and the hook restarts the wrapper session. With a configured `terminal.wrapper` the hook is required (a missing one exits 2 before the first step; a hook that throws fails the step). Without a wrapper every step already starts a fresh process or tmux server, so the flag needs no hook. Steps without the flag keep the session as it is.
- **The isolated environment's identity defaults to the host's.** A guest user's name, home directory and full-name field can copy the host's real identity. Set them to the persona's values at provisioning, and compare the transcript against the roster.
- **OCR reads `_` as a space.** The sidecar text next to a rendered image (`NN-slug.png.txt`) is authoritative, never the OCR result.
- **Identifiers split across wrapped lines.** A long token wrapped at the window edge is still one identifier. The text gate joins the wrapped fragments of an identifier before matching (`gates/SPEC.md` lists the kinds); a wide window (below) keeps most lines unwrapped. A rendered image marks every wrap with a visible continuation marker.
- **Transcripts are verbatim.** Nothing is shortened or summarized. A line that must go is replaced by `[omitted: <reason>]`. A docs code block uses the full redacted transcript, not the image.
- **Trust and consent dialogs have defaults.** The default choice may be "exit" or "no". Send the key that moves to the intended choice first (`Down` before `Enter`), and wait for the dialog text before sending anything.
- **Socket path length.** A unix socket path is limited to about 104 bytes on macOS and 108 on Linux. The tmux driver exits 2 with a message when its path would be longer. Keep `--socket-dir` short (the default is `/tmp/jqa-<uid>`).
- **Secrets in tmux go through the buffer channel, not `send-keys`.** `send-keys` puts the value on argv (visible in `ps` and logs) and in scrollback. The one exception is a `class: test` value written as a `literal`, which is still scrubbed from every file the run writes.
- **Revert the state a run created.** A step that changes the isolated environment (a file, a user, a registered client) lists `cleanup` commands. They run on every path, including after a failure, so the next run starts from the same state.
- **A policy layer may block an action.** An outer layer (an approval prompt, a sandbox) can refuse a command before the program runs. Configure `blocked_pattern` in the hook config; a step whose screen matches it, or that never reaches the step, ends `status=not-verified` and is reported as not verified. It is never a pass.
- **Judge the outcome from durable state.** The screen shows what the program chose to draw. Decide pass or fail from files, API responses or database rows through `verify`. The screen is evidence.
- **No fixed sleeps.** A wait is a screen regex or a prompt with a ceiling. A `sleep N` as the only wait is rejected by the validator, because it passes on a fast machine and fails on a slow one.
- **Use a wide window for full-screen programs.** A narrow window wraps lines, hides columns and splits identifiers. Keep the default `220x50` or set `size` to at least what the program needs.
- **`remain-on-exit`.** The driver keeps the pane after the program exits so it can read the exit status (`#{pane_dead_status}`) and the last screen. Without it, a program that exits early leaves nothing to capture.

## Terminal images

Terminal images are drawn from the redacted transcript with `capture/render_terminal.py`, never captured from a live window (`capture/SPEC.md` "Terminal images"). A shot with `terminal_from` names the step or steps whose transcripts it draws, and `wait_for: ["transcript:<text>"]` entries must occur in them. When a docs copy replaces an environment-specific value with a documented default, list each replacement in the substitution table of the report (`templates/report.md`).

## Checks

`./check.sh` runs every driver against invented fake programs (`runners/selftest/terminal.sh`) and runs terminal-only scenarios through `runners/run.sh` with no browser (`runners/selftest/orchestrate.sh`). tmux 3.2 or newer is required; a missing tmux fails the check. Examples: `scenarios/example/first-run.yaml`, `adapters/example-app/scenarios/first-run-client.yaml`.
