#!/usr/bin/env bash
# Run one scenario on one engine (runners/README.md).
#
#   runners/run.sh [--engine auto|ego|playwright] --scenario FILE --out DIR --denylist FILE [options]
#
#   --engine auto          default. Pick by where the run executes (see "Engine selection" below)
#   --select-only          print the engine auto would choose and why, then exit (no scenario needed)
#   --adapter DIR          adapter directory (default: JQA_ADAPTER_DIR). With --product, searched in order:
#                          --adapter/JQA_ADAPTER_DIR > <project>/.journey-qa/adapters/<product>/ > bundled adapters/<product>/
#   --product NAME         adapter name to resolve when no explicit adapter is given
#   --project DIR          target product repo for the project-side search (default: current directory)
#                          A resolved adapter supplies defaults: --policy <adapter>/policy.json and
#                          --hooks <adapter>/runner/hooks.mjs (when present and not given), and a --scenario
#                          that is not a file is looked up as <adapter>/scenarios/<name>[.yaml]
#   --denylist FILE        repeatable; at least one (the real denylist, kept outside the repo)
#   --policy FILE          repeatable; the adapter's policy.json
#   --roster FILE          default: the scenario's roster
#   --hooks FILE           the adapter's hooks module (.mjs)
#   --hook-config FILE     JSON handed to the hooks as ctx.config
#   --secrets-dir DIR      one file per {{secret.<name>}}, mode 600, outside the repo
#   --base ID=URL          override an environment.instances[].base_url; repeatable
#   --state-from DIR       inject state-<persona>.json from an earlier run's secrets/
#   --inject-failure STEP  use a selector that matches nothing inside STEP (evidence drill)
#   --approved-gate ID     a person completed this human gate; repeatable
#   --baselines DIR        compare shots with DIR/<engine>/<scenario-id>/ (gates/img_diff.py)
#   --evidence-ocr MODE    require (default) or off, passed to capture/evidence.py
#   --timeout MS           wait timeout for actions and expectations (default 5000)
#   --pw-module PATH       Playwright entry module (playwright or playwright-core index.mjs)
#   --pw-channel NAME      Playwright browser channel (for example chrome)
#   --headed               Playwright with a visible window
#   --ego-space ID         ego-browser task space id
#
# Engine selection (--engine auto, the default). An engine is usable when ego-browser is on PATH
# and --ego-space is given (real-profile), or --pw-module names an existing file (isolated).
#   interactive run (no CI variable, stdin and stdout are terminals) and real-profile engine usable -> ego
#   CI or headless (CI set to a non-empty value other than 0/false, or no terminal)
#     and isolated engine usable                                                               -> playwright
#   real-profile engine not usable and isolated engine usable                                  -> playwright
#   no usable engine for this context                                                          -> exit 2, names what to install
# The choice and the reason are printed, written to <out>/engine.json and the first line of
# <out>/run.log. JQA_TTY=0|1 overrides terminal detection (selftests).
#
# Steps run in scenario order. Terminal steps (driver exec, pty or tmux) go to runners/terminal/*_driver.py;
# browser steps go to the selected engine. A scenario whose steps are all terminal steps needs no engine:
# it runs with engine "none", --engine and the engine options are ignored, and nothing about a browser
# is probed. A tool a step needs (tmux for a tmux step, rsvg-convert for a terminal shot, node) that is
# missing exits 2 and names it, before the first step.
#
# Exit: 0 passed, 1 failed (steps, assertions or baseline comparison), 2 usage or
# configuration error (including a missing tool a step needs), 3 stopped at a human gate.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"
usage() { echo "usage: $0 [--engine auto|ego|playwright] --scenario FILE --out DIR --denylist FILE [options]" >&2; exit 2; }
abs() { python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$1"; }

ENGINE=auto REQUESTED="" SELECT_ONLY=false SCENARIO="" OUT="" ROSTER="" HOOKS="" HOOK_CONFIG="" SECRETS_DIR="" STATE_FROM="" INJECT=""
BASELINES="" OCR="require" TIMEOUT=5000 PW_MODULE="" PW_CHANNEL="" HEADED=false EGO_SPACE=""
DENY=() POLICY=() BASES=() GATES=() ADAPTER_ARG="" PRODUCT="" PROJECT="" SCENARIO_ARG="" ADAPTER=""
while [ $# -gt 0 ]; do
  [ $# -ge 2 ] || case "$1" in --headed|--select-only) ;; *) usage ;; esac
  case "$1" in
    --engine) ENGINE="$2"; shift 2 ;;
    --scenario) SCENARIO_ARG="$2"; shift 2 ;;
    --adapter) ADAPTER_ARG="$2"; shift 2 ;;
    --product) PRODUCT="$2"; shift 2 ;;
    --project) PROJECT="$2"; shift 2 ;;
    --out) OUT="$(abs "$2")"; shift 2 ;;
    --denylist) DENY+=("$(abs "$2")"); shift 2 ;;
    --policy) POLICY+=("$(abs "$2")"); shift 2 ;;
    --roster) ROSTER="$(abs "$2")"; shift 2 ;;
    --hooks) HOOKS="$(abs "$2")"; shift 2 ;;
    --hook-config) HOOK_CONFIG="$(abs "$2")"; shift 2 ;;
    --secrets-dir) SECRETS_DIR="$(abs "$2")"; shift 2 ;;
    --base) BASES+=("$2"); shift 2 ;;
    --state-from) STATE_FROM="$(abs "$2")"; shift 2 ;;
    --inject-failure) INJECT="$2"; shift 2 ;;
    --approved-gate) GATES+=("$2"); shift 2 ;;
    --baselines) BASELINES="$(abs "$2")"; shift 2 ;;
    --evidence-ocr) OCR="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --pw-module) PW_MODULE="$(abs "$2")"; shift 2 ;;
    --pw-channel) PW_CHANNEL="$2"; shift 2 ;;
    --headed) HEADED=true; shift ;;
    --select-only) SELECT_ONLY=true; shift ;;
    --ego-space) EGO_SPACE="$2"; shift 2 ;;
    *) usage ;;
  esac
done
case "$ENGINE" in auto|ego|playwright) ;; *) usage ;; esac
REQUESTED="$ENGINE" ENGINE_REASON="explicit --engine $ENGINE"
# Engine selection (--engine auto). Sets ENGINE and ENGINE_REASON, or exits 2 naming what to install.
select_engine() {
  EGO_OK=false PW_OK=false INTERACTIVE=true WHY=""
  { command -v ego-browser > /dev/null && [ -n "$EGO_SPACE" ]; } && EGO_OK=true
  { [ -n "$PW_MODULE" ] && [ -f "$PW_MODULE" ]; } && PW_OK=true
  case "${CI:-}" in ''|0|false|FALSE|False) ;; *) INTERACTIVE=false; WHY="CI environment variable is set" ;; esac
  if [ "$INTERACTIVE" = true ]; then
    case "${JQA_TTY:-}" in
      0) INTERACTIVE=false ;;
      1) ;;
      *) { [ -t 0 ] && [ -t 1 ]; } || INTERACTIVE=false ;;
    esac
    [ "$INTERACTIVE" = true ] || WHY="no terminal (headless)"
  fi
  if [ "$INTERACTIVE" = true ] && [ "$EGO_OK" = true ]; then
    ENGINE=ego ENGINE_REASON="interactive local run and the real-profile engine (ego-browser) is usable"
  elif [ "$PW_OK" = true ]; then
    ENGINE=playwright
    if [ "$INTERACTIVE" = false ]; then ENGINE_REASON="$WHY; using the isolated engine (Playwright)"
    else ENGINE_REASON="real-profile engine (ego-browser) not usable (needs ego-browser on PATH and --ego-space); using the isolated engine (Playwright)"; fi
  else
    if [ "$INTERACTIVE" = false ]; then
      echo "no usable engine: $WHY, so the isolated engine is needed. Install Playwright and pass --pw-module <prefix>/node_modules/playwright/index.mjs (or pass --engine ego explicitly to use ego-browser)." >&2
    else
      echo "no usable engine: install ego-browser (and pass --ego-space <id>) or install Playwright (and pass --pw-module <prefix>/node_modules/playwright/index.mjs)." >&2
    fi
    exit 2
  fi
}
if [ "$SELECT_ONLY" = true ]; then
  [ "$ENGINE" = auto ] && select_engine
  echo "engine: $ENGINE ($ENGINE_REASON)"
  exit 0
fi

# 0. Adapter resolution (gates/adapter_dir.py is the one rule). Only when an adapter is named.
if [ -n "$ADAPTER_ARG" ] || [ -n "$PRODUCT" ] || [ -n "${JQA_ADAPTER_DIR:-}" ]; then
  ARGS=(); [ -n "$ADAPTER_ARG" ] && ARGS+=(--adapter "$ADAPTER_ARG"); [ -n "$PRODUCT" ] && ARGS+=(--product "$PRODUCT"); [ -n "$PROJECT" ] && ARGS+=(--project "$PROJECT")
  ADAPTER="$(python3 "$REPO/gates/adapter_dir.py" --why ${ARGS[@]+"${ARGS[@]}"})" || exit 2
  if [ -n "$SCENARIO_ARG" ] && [ ! -f "$SCENARIO_ARG" ]; then
    for c in "$ADAPTER/scenarios/$SCENARIO_ARG" "$ADAPTER/scenarios/$SCENARIO_ARG.yaml"; do [ -f "$c" ] && { SCENARIO_ARG="$c"; break; }; done
  fi
  [ "${#POLICY[@]}" -eq 0 ] && [ -f "$ADAPTER/policy.json" ] && POLICY+=("$ADAPTER/policy.json")
  [ -z "$HOOKS" ] && [ -f "$ADAPTER/runner/hooks.mjs" ] && HOOKS="$ADAPTER/runner/hooks.mjs"
fi
[ -n "$SCENARIO_ARG" ] && SCENARIO="$(abs "$SCENARIO_ARG")"
[ -n "$SCENARIO" ] && [ -f "$SCENARIO" ] && [ -n "$OUT" ] || usage
[ "${#DENY[@]}" -gt 0 ] || { echo "at least one --denylist is required (failure evidence is gated with it)" >&2; exit 2; }
case "$OCR" in require|off) ;; *) usage ;; esac
case "$TIMEOUT" in ''|*[!0-9]*) usage ;; esac

# 1. Validate, then convert to JSON for the runner.
mkdir -p "$OUT/secrets" && chmod 700 "$OUT/secrets" || exit 2
case "$SCENARIO" in
  *.json) python3 "$REPO/scenarios/validate.py" "$SCENARIO" > "$OUT/validate.txt" 2>&1 || { cat "$OUT/validate.txt" >&2; exit 2; }
          cp "$SCENARIO" "$OUT/scenario.json" ;;
  *) uv run -q "$REPO/scenarios/validate.py" "$SCENARIO" > "$OUT/validate.txt" 2>&1 || { cat "$OUT/validate.txt" >&2; exit 2; }
     uv run -q --with pyyaml python3 -c 'import json,sys,yaml; json.dump(yaml.safe_load(open(sys.argv[1], encoding="utf-8")), sys.stdout)' \
       "$SCENARIO" > "$OUT/scenario.json" || exit 2 ;;
esac

# 1b. Engine. Steps run in scenario order; a browser engine is needed only when a step is not a
# terminal step. A terminal-only scenario runs with engine "none" (no browser, no engine option).
NEEDS_BROWSER="$(python3 -c 'import json,sys; print("true" if any(s.get("surface") != "terminal" for s in json.load(open(sys.argv[1]))["steps"]) else "false")' "$OUT/scenario.json")" || exit 2
if [ "$NEEDS_BROWSER" = false ]; then
  ENGINE=none ENGINE_REASON="terminal-only scenario: no browser engine needed"
  command -v node > /dev/null || { echo "node not found: the runner needs Node.js 18+ (terminal steps are orchestrated by it)" >&2; exit 2; }
elif [ "$ENGINE" = auto ]; then
  select_engine
fi
echo "engine: $ENGINE ($ENGINE_REASON)"
echo "[run] engine: $ENGINE ($ENGINE_REASON)" > "$OUT/run.log"
JQA_OUT_DIR="$OUT" JQA_E="$ENGINE" JQA_R="$REQUESTED" JQA_W="$ENGINE_REASON" python3 -c 'import json,os; json.dump({"engine": os.environ["JQA_E"], "requested": os.environ["JQA_R"], "reason": os.environ["JQA_W"]}, open(os.environ["JQA_OUT_DIR"] + "/engine.json", "w"), indent=1)' || exit 2

# 2. Engine prerequisites.
PW_VERSION="" EGO_VERSION=""
if [ "$ENGINE" = none ]; then
  :
elif [ "$ENGINE" = playwright ]; then
  [ -n "$PW_MODULE" ] || { echo "--pw-module is required for the playwright engine" >&2; exit 2; }
  PW_VERSION="$(python3 -c 'import json,os,sys; print(json.load(open(os.path.join(os.path.dirname(sys.argv[1]), "package.json")))["version"])' "$PW_MODULE" 2>/dev/null || echo '?')"
else
  [ -n "$EGO_SPACE" ] || { echo "--ego-space is required for the ego engine" >&2; exit 2; }
  command -v ego-browser > /dev/null || { echo "ego-browser not found" >&2; exit 2; }
  EGO_VERSION="$(ego-browser -v 2>/dev/null | tr '\n' ' ' | sed 's/  */ /g')"
fi

# 3. Config (absolute paths only; the runner writes run-relative paths in results).
CFG="$OUT/config.json"
join() { local IFS=$'\x1f'; echo "$*"; }
JQA_ENGINE="$ENGINE" JQA_REPO="$REPO" JQA_OUT="$OUT" JQA_SCENARIO="$SCENARIO" JQA_ROSTER="$ROSTER" \
JQA_HOOKS="$HOOKS" JQA_HOOK_CONFIG="$HOOK_CONFIG" JQA_SECRETS_DIR="$SECRETS_DIR" JQA_STATE_FROM="$STATE_FROM" \
JQA_INJECT="$INJECT" JQA_BASES="$(join ${BASES[@]+"${BASES[@]}"})" JQA_GATES="$(join ${GATES[@]+"${GATES[@]}"})" \
JQA_DENY="$(join "${DENY[@]}")" JQA_POLICY="$(join ${POLICY[@]+"${POLICY[@]}"})" JQA_OCR="$OCR" \
JQA_PYTHON="$(command -v python3)" JQA_PW_MODULE="$PW_MODULE" JQA_PW_VERSION="$PW_VERSION" \
JQA_PW_CHANNEL="$PW_CHANNEL" JQA_HEADED="$HEADED" JQA_TIMEOUT="$TIMEOUT" JQA_EGO_SPACE="$EGO_SPACE" JQA_EGO_VERSION="$EGO_VERSION" \
python3 - "$CFG" <<'PY' || exit 2
import json, os, sys
e = os.environ
lst = lambda k: [x for x in e[k].split("\x1f") if x]
opt = lambda k: e[k] or None
scenario_json = os.path.join(e["JQA_OUT"], "scenario.json")
scenario = json.load(open(scenario_json, encoding="utf-8"))
scenario_dir = os.path.dirname(e["JQA_SCENARIO"])
space = e["JQA_EGO_SPACE"]
cfg = {
    "engine": e["JQA_ENGINE"], "repo": e["JQA_REPO"], "out": e["JQA_OUT"],
    "scenario": scenario_json, "scenarioDir": scenario_dir,
    "roster": opt("JQA_ROSTER"),
    "rosterPath": e["JQA_ROSTER"] or os.path.abspath(os.path.join(scenario_dir, scenario["roster"])),
    "hooks": opt("JQA_HOOKS"),
    "hookConfig": json.load(open(e["JQA_HOOK_CONFIG"], encoding="utf-8")) if e["JQA_HOOK_CONFIG"] else {},
    "secretsDir": opt("JQA_SECRETS_DIR"), "stateFrom": opt("JQA_STATE_FROM"), "injectFailure": opt("JQA_INJECT"),
    "bases": dict(b.split("=", 1) for b in lst("JQA_BASES")), "approvedGates": lst("JQA_GATES"),
    "denylists": lst("JQA_DENY"), "policies": lst("JQA_POLICY"), "evidenceOcr": e["JQA_OCR"],
    "python": e["JQA_PYTHON"], "playwrightModule": opt("JQA_PW_MODULE"), "playwrightVersion": e["JQA_PW_VERSION"],
    "pwChannel": opt("JQA_PW_CHANNEL"), "headed": e["JQA_HEADED"] == "true",
    "egoSpace": int(space) if space.isdigit() else (space or None), "egoVersion": e["JQA_EGO_VERSION"],
    "actionTimeout": int(e["JQA_TIMEOUT"]), "navTimeout": max(20000, int(e["JQA_TIMEOUT"])),
}
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump(cfg, fh, indent=1)
PY

# 4. Run.
if [ "$ENGINE" = playwright ] || [ "$ENGINE" = none ]; then
  node "$HERE/run-engine.mjs" "$CFG" 2>&1 | tee -a "$OUT/run.log"
else
  { printf 'globalThis.JQA_CONFIG = %s;\n' "$(cat "$CFG")"; printf "await import('%s/run-engine.mjs');\n" "$HERE"; } \
    | ego-browser nodejs 2>&1 | tee -a "$OUT/run.log"
fi
rc="$(sed -n 's/^\[run\] exit \([0-9]\)$/\1/p' "$OUT/run.log" | tail -1)"
[ -n "$rc" ] || { echo "runner ended without an exit line" >&2; exit 1; }
[ "$rc" -eq 0 ] || exit "$rc"

# 5. Baseline comparison (same engine only).
if [ -n "$BASELINES" ]; then
  ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$OUT/scenario.json")"
  THRESHOLD="$(python3 -c 'import json,sys; print((json.load(open(sys.argv[1])).get("baseline") or {}).get("max_diff_ratio", 0.01))' "$OUT/scenario.json")"
  BDIR="$BASELINES/$ENGINE/$ID"
  [ -d "$BDIR" ] || { echo "NO_BASELINE_DIR $ENGINE/$ID" | tee "$OUT/img_diff.txt"; exit 1; }
  REL="$(python3 -c 'import os,sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))' "$BDIR" "$OUT")"
  (cd "$OUT" && python3 "$REPO/gates/img_diff.py" --threshold "$THRESHOLD" "$REL" shots) > "$OUT/img_diff.txt" 2>&1
  drc=$?
  cat "$OUT/img_diff.txt"
  [ "$drc" -eq 0 ] || exit "$drc"
fi
exit 0
