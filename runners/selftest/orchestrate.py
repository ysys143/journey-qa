#!/usr/bin/env python3
"""Scenario orchestration smoke tests (run by orchestrate.sh).

Part A needs no browser: scenarios made only of terminal steps run through runners/run.sh with
engine "none" (exec, pty and tmux drivers against the invented fake programs in selftest/terminal/),
terminal shots are drawn after the run from the redacted transcripts, and every behavior has a
must-fail twin (a failing step, a missing tool named at preflight, an invalid step, a failed text
gate, a transcript that lacks the shot's text, an unavailable secret).

Part B (a scenario that mixes terminal and browser steps against the invented demo app in app/)
needs a browser engine: pass the same options as smoke.sh (--engine playwright --pw-module PATH, or
--engine ego --ego-space ID). Without them Part B is reported as SKIPPED and does not count as run.

Exit 0 when every control that ran holds, 1 when one fails, 2 cannot run.
"""
import sys

sys.dont_write_bytecode = True

import copy  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import secrets  # noqa: E402
import shutil  # noqa: E402
import socket  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
RUN_SH = os.path.join(REPO, "runners", "run.sh")
FAKES = os.path.join(HERE, "terminal")
DENYLIST = os.path.join(REPO, "gates", "selftest", "denylist.test.txt")
ROSTER = os.path.join(REPO, "fixtures", "roster.example.json")
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("OK   " if cond else "FAIL ") + name + ("" if cond else f" :: {detail}"))
    return bool(cond)


WORK = os.path.realpath(tempfile.mkdtemp(prefix="jqa-orch-"))
for _f in ("fake_cli.py", "fake_tui.py", "fake-session-wrap.sh", "reconnect-hooks.mjs"):  # commands name them relative to the run's working directory
    shutil.copy(os.path.join(FAKES, _f), os.path.join(WORK, _f))
SOCKS = tempfile.mkdtemp(prefix="jqa", dir="/tmp")  # short unix socket paths
SEC = os.path.join(WORK, "sec")
os.makedirs(SEC, mode=0o700)
COUNTER = [0]


def new_secret(name):
    value = secrets.token_hex(8)
    path = os.path.join(SEC, name)
    with open(path, "w") as fh:
        fh.write(value + "\n")
    os.chmod(path, 0o600)
    return value


def sha(v):
    return hashlib.sha256(v.encode()).hexdigest()


def base_scenario(sid, steps, shots=None, **top):
    d = {
        "schema_version": 1, "id": sid, "title": "orchestration smoke", "roster": os.path.relpath(ROSTER, WORK),
        "determinism": {"seed": 1, "time_anchor": "2030-01-15T14:00:00+00:00",
                        "viewport": {"width": 800, "height": 600, "device_scale_factor": 1},
                        "locale": "en-US", "timezone": "UTC", "color_scheme": "light"},
        "personas": [{"ref": "ivy", "as": "admin"}], "steps": steps,
    }
    if shots:
        d["shots"] = shots
    d.update(top)
    return d


def T(sid, driver, **kw):
    s = {"id": sid, "persona": "ivy", "surface": "terminal", "action": "run", "driver": driver}
    s.update(kw)
    return s


def run_sh(scenario, *extra, env=None, path=None, timeout=300, hook_cfg=None):
    COUNTER[0] += 1
    n = COUNTER[0]
    sf = os.path.join(WORK, f"scenario-{n}.json")
    with open(sf, "w") as fh:
        json.dump(scenario, fh)
    out = os.path.join(WORK, f"run-{n}")
    e = dict(os.environ)
    e.pop("CI", None)
    e.update(env or {})
    if path is not None:
        e["PATH"] = path
    p = subprocess.run(["bash", RUN_SH, "--scenario", sf, "--out", out, "--denylist", DENYLIST,
                        "--secrets-dir", SEC, "--hook-config", hook_cfg or hook_config(), *extra],
                       capture_output=True, text=True, env=e, timeout=timeout, cwd=WORK)
    r = type("R", (), {})()
    r.rc, r.stdout, r.stderr, r.out = p.returncode, p.stdout, p.stderr, out
    rp = os.path.join(out, "results.json")
    r.res = json.load(open(rp)) if os.path.exists(rp) else None
    return r


def hook_config():
    path = os.path.join(WORK, "hook-config.json")
    if not os.path.exists(path):
        with open(path, "w") as fh:
            json.dump({"terminal": {"socket_dir": SOCKS}}, fh)
    return path


def files_with(value, root, skip=()):
    hits = []
    for dirpath, _d, files in os.walk(root):
        for f in files:
            path = os.path.join(dirpath, f)
            rel = os.path.relpath(path, root)
            if rel.startswith(os.path.join("secrets", "values")) or rel == os.path.join("secrets", "run-denylist.txt") or rel in skip:
                continue
            try:
                blob = open(path, "rb").read()
            except OSError:
                continue
            if value.encode() in blob:
                hits.append(rel)
    return hits


def stripped_path(*without):
    """A PATH holding only what run.sh needs, minus the named tools."""
    d = os.path.join(WORK, "path-" + "-".join(without))
    os.makedirs(d, exist_ok=True)
    for tool in ("bash", "python3", "node", "dirname", "tee", "sed", "tail", "cat", "mkdir", "chmod", "cp", "env", "uv",
                 "rm", "ls", "tr", "head", "sh", "grep", "rsvg-convert", "tmux", "date", "sleep", "mktemp", "cut", "wc"):
        if tool in without:
            continue
        src = shutil.which(tool)
        if src and not os.path.exists(os.path.join(d, tool)):
            os.symlink(src, os.path.join(d, tool))
    return d


# ---------------------------------------------------------------------------- Part A

def part_a():
    print("-- Part A: terminal-only scenarios (no browser engine)")
    pw = new_secret("pw")
    state = "state-a"
    steps = [
        T("exec-version", "exec", cmd="echo tool 1.2.3", expect_exit=0, stdout_contains=["tool 1.2.3"]),
        T("exec-token", "exec", cmd="echo issued token: tok_$$_abcdef", expect_exit=0,
          secrets={"capture_as": {"name": "issued", "pattern": r"issued token: (\S+)"}}),
        T("pty-login", "pty", cmd=f"python3 fake_cli.py {sha(pw)}", timeout_s=30, expect_exit=0, stdout_contains=["ready"],
          dialog=[{"wait_for": "Server URL:", "send": "https://app.example.com"},
                  {"wait_for": "Password:", "send": "{{secret.pw}}", "secret": True},
                  {"wait_for": r"Continue\? \[y/n\]", "send": "y"}], shot="login-shot"),
        T("tmux-turn", "tmux", launch=f"python3 fake_tui.py {state} --ask-secret echo-off", size="160x40", timeout_s=60,
          keys=[{"wait_for": "Token:"}, {"paste_buffer": "pw"}, {"key": "Enter"}, {"wait_for": "Select an action"},
                {"key": "Down"}, {"key": "Enter"}],
          done_when="TASK-COMPLETE-MARKER", verify=[{"kind": "file", "path": state, "contains": "choice=run"}], shot="turn-shot"),
        T("exec-state", "exec", cmd=f"cat {state}", stdout_contains=["choice=run"]),
    ]
    shots = [
        {"id": "login-shot", "file": "01-login.png", "terminal_from": ["exec-version", "pty-login"], "wait_for": ["transcript:ready"]},
        {"id": "turn-shot", "file": "02-turn.png", "terminal_from": "tmux-turn", "wait_for": ["transcript:TASK-COMPLETE-MARKER"]},
    ]
    secrets_decl = {"pw": {"class": "test"}, "issued": {"class": "test"}}
    good = base_scenario("term-only", steps, shots, secrets=secrets_decl)

    # Everything below runs without any engine option and with CI set: nothing here may need a browser.
    r = run_sh(good, env={"CI": "1"})
    check("terminal-only: passes with no browser engine configured", r.rc == 0 and r.res and r.res["passed"], (r.rc, r.stdout[-600:], r.stderr[-300:]))
    eng = json.load(open(os.path.join(r.out, "engine.json"))) if os.path.exists(os.path.join(r.out, "engine.json")) else {}
    check("terminal-only: engine none, reason recorded", eng.get("engine") == "none" and "terminal-only" in eng.get("reason", "")
          and r.res and r.res["engine"] == "none", eng)
    ids = [s["id"] for s in (r.res or {"steps": []})["steps"] if not s["id"].startswith("shot:")]
    check("steps run in scenario order", ids == [s["id"] for s in steps], ids)
    shot_ids = [s["id"] for s in (r.res or {"steps": []})["steps"] if s["id"].startswith("shot:")]
    check("terminal shots are drawn after the steps, in order", shot_ids == ["shot:login-shot", "shot:turn-shot"]
          and [c["file"] for c in r.res["captures"]] == ["shots/01-login.png", "shots/02-turn.png"], (shot_ids, r.res and r.res["captures"]))
    sidecar = os.path.join(r.out, "shots", "01-login.png.txt")
    text = open(sidecar).read() if os.path.exists(sidecar) else ""
    t1 = open(os.path.join(r.out, "terminal", "exec-version.txt")).read() if r.res else ""
    t2 = open(os.path.join(r.out, "terminal", "pty-login.txt")).read() if r.res else ""
    check("shot text is the two redacted transcripts joined in the listed order", text != "" and text == t1 + t2, text[:200])
    check("shot has its render note and no metadata", os.path.exists(os.path.join(r.out, "shots", "01-login.png.render.json")))
    issued = open(os.path.join(r.out, "secrets", "values", "issued")).read().strip() if os.path.exists(os.path.join(r.out, "secrets", "values", "issued")) else ""
    check("capture_as: the captured value is kept in the run secrets (mode 600) and nowhere else",
          issued.startswith("tok_") and files_with(issued, r.out) == [] and oct(os.stat(os.path.join(r.out, "secrets", "values", "issued")).st_mode & 0o777) == "0o600",
          (issued, files_with(issued, r.out) if issued else None))
    check("capture_as: the value joined the run denylist", issued and issued in open(os.path.join(r.out, "secrets", "run-denylist.txt")).read())
    check("secret entered by pty and by tmux paste is absent from every file of the run", files_with(pw, r.out) == [], files_with(pw, r.out))
    tm = r.res["steps"][3] if r.res else {}
    check("tmux step verdict and transcript are kept", tm.get("ok") and os.path.exists(os.path.join(r.out, "terminal", "tmux-turn.txt")), tm)
    check("no tmux server or socket is left behind", not [f for f in os.listdir(SOCKS)], os.listdir(SOCKS))

    # must-fail: a failing step stops the run; later steps are skipped; evidence is the kept transcript
    bad = copy.deepcopy(good)
    bad["steps"][0]["expect_exit"] = 3
    r = run_sh(bad)
    st = {s["id"]: s for s in (r.res or {"steps": []})["steps"]}
    check("control: a wrong exit code fails the run (exit 1), names the step, skips the rest",
          r.rc == 1 and r.res and r.res["failedStep"] == "exec-version" and st.get("exec-token", {}).get("skipped")
          and "exit 0, expected 3" in st["exec-version"].get("error", "") and not r.res["captures"], (r.rc, r.res))
    check("control: the failed step's evidence is its kept transcript",
          st.get("exec-version", {}).get("evidence") == "terminal/exec-version.txt", st.get("exec-version"))

    # must-fail: the text gate keeps a denylisted value out of transcripts
    listed = next(ln.strip() for ln in open(DENYLIST, encoding="utf-8") if ln.strip() and not ln.startswith("#"))  # an invented entry of the test denylist
    leak = base_scenario("term-leak", [T("exec-leak", "exec", cmd=f"echo owner is {listed}")])
    r = run_sh(leak)
    check("control: a transcript the text gate rejects fails the step and is not kept",
          r.rc == 1 and not os.path.exists(os.path.join(r.out, "terminal", "exec-leak.txt")) and "transcript not kept" in json.dumps(r.res), (r.rc, r.res))

    # must-fail: a shot whose transcript lacks its text is not drawn
    nop = copy.deepcopy(good)
    nop["shots"][0]["wait_for"] = ["transcript:text-that-is-not-there"]
    r = run_sh(nop)
    check("control: a terminal shot whose transcript lacks its text fails the run and writes no image",
          r.rc == 1 and r.res and r.res["failedStep"] == "shot:login-shot" and not os.path.exists(os.path.join(r.out, "shots", "01-login.png")), (r.rc, r.res))

    # must-fail: a secret with no value
    missing = base_scenario("term-nosecret", [T("pty-nosecret", "pty", cmd="cat", dialog=[{"wait_for": "x", "send": "{{secret.absent}}", "secret": True}])],
                            secrets={"absent": {"class": "test"}})
    r = run_sh(missing)
    check("control: a secret with no file, capture or hook value fails the step (exit 1) naming it",
          r.rc == 1 and "secret absent is not available" in json.dumps(r.res), (r.rc, r.res))

    # must-fail: validation happens before anything runs
    invalid = base_scenario("term-invalid", [T("tmux-nodone", "tmux", launch="echo hi", verify_note="x")])
    r = run_sh(invalid)
    check("control: an invalid terminal step is rejected before anything runs (exit 2)",
          r.rc == 2 and "done_when" in r.stderr and not os.path.exists(os.path.join(r.out, "results.json")), (r.rc, r.stderr))
    sleepy = base_scenario("term-sleepy", [T("wait-blind", "exec", cmd="sleep 3")])
    r = run_sh(sleepy)
    check("control: a fixed sleep as the only wait is rejected (exit 2)", r.rc == 2 and "sleep" in r.stderr, (r.rc, r.stderr))

    # preflight names the missing tool; the twin without that need passes on the same PATH
    no_tmux = stripped_path("tmux")
    r = run_sh(good, path=no_tmux)
    check("control: a tmux step without tmux on PATH exits 2 and names tmux",
          r.rc == 2 and "tmux" in (r.stdout + r.stderr) and "tmux-turn" in (r.stdout + r.stderr) and not os.path.exists(os.path.join(r.out, "terminal")), (r.rc, r.stdout[-300:], r.stderr))
    exec_only = base_scenario("term-exec-only", [steps[0]])
    r = run_sh(exec_only, path=no_tmux)
    check("twin: the same PATH runs a scenario with no tmux step", r.rc == 0 and r.res and r.res["passed"], (r.rc, r.stdout[-300:], r.stderr))
    no_rsvg = stripped_path("rsvg-convert")
    r = run_sh(base_scenario("term-shot-only", [steps[0] | {"shot": "s"}], [{"id": "s", "file": "01-s.png", "terminal_from": "exec-version", "wait_for": ["transcript:tool"]}]), path=no_rsvg)
    check("control: terminal shots without rsvg-convert exit 2 and name it", r.rc == 2 and "rsvg-convert" in (r.stdout + r.stderr), (r.rc, r.stdout[-300:], r.stderr))
    r = run_sh(exec_only, path=no_rsvg)
    check("twin: no shots, no rsvg-convert needed", r.rc == 0, (r.rc, r.stderr))

    # a human gate stops the run (exit 3) after the terminal shot of the steps before it is drawn
    gated = base_scenario("term-gate", [steps[0] | {"shot": "s1"}, T("exec-after-gate", "exec", cmd="echo after")],
                          [{"id": "s1", "file": "01-s1.png", "terminal_from": "exec-version", "wait_for": ["transcript:tool"]},
                           {"id": "s2", "file": "02-s2.png", "terminal_from": "exec-after-gate", "wait_for": ["transcript:after"]}],
                          human_gates=[{"id": "approve-it", "before_step": "exec-after-gate", "reason": "a person approves the image"}])
    gated["steps"][1]["shot"] = "s2"
    r = run_sh(gated)
    check("human gate: the run stops with exit 3, draws the shot of the step before the gate and not the one after",
          r.rc == 3 and os.path.exists(os.path.join(r.out, "shots", "01-s1.png")) and not os.path.exists(os.path.join(r.out, "shots", "02-s2.png"))
          and r.res and r.res["stoppedAtHumanGate"] == "approve-it", (r.rc, r.res))
    r = run_sh(gated, "--approved-gate", "approve-it")
    check("human gate twin: once approved, every step and both shots run", r.rc == 0 and os.path.exists(os.path.join(r.out, "shots", "02-s2.png")), (r.rc, r.stdout[-300:]))

    # explicit engine options are not needed and not consulted for a terminal-only scenario
    r = run_sh(exec_only, "--engine", "ego")
    check("terminal-only: an engine option without its prerequisites is ignored", r.rc == 0 and r.res and r.res["engine"] == "none", (r.rc, r.stderr))
    # a browser step still needs an engine
    mixed_needs = base_scenario("term-needs-engine", [steps[0], {"id": "open", "persona": "ivy", "surface": "browser", "action": "goto", "target": "/"}],
                                environment={"instances": [{"id": "a", "purpose": "demo", "base_url": "http://127.0.0.1:1"}]})
    r = run_sh(mixed_needs, env={"JQA_TTY": "0"})
    check("control: a scenario with a browser step and no usable engine exits 2 naming what to install",
          r.rc == 2 and "install" in r.stderr.lower() and not os.path.exists(os.path.join(r.out, "terminal")), (r.rc, r.stderr))

    reconnect_controls()


def reconnect_controls():
    """`reconnect: true`: a persistent wrapper connection keeps stale group membership until the hook restarts it."""
    state = os.path.join(WORK, "conn-state")
    os.makedirs(state, exist_ok=True)

    def reset():
        shutil.rmtree(state)
        os.makedirs(state)
        open(os.path.join(state, "groups"), "w").write("old\n")

    def cfg(name, fail=False):
        path = os.path.join(WORK, name)
        with open(path, "w") as fh:
            json.dump({"terminal": {"socket_dir": SOCKS, "wrapper": ["sh", os.path.join(WORK, "fake-session-wrap.sh"), state]},
                       "reconnect_test": {"state_dir": state, "fail": fail}}, fh)
        return path

    hooks = ["--hooks", os.path.join(WORK, "reconnect-hooks.mjs")]
    sees = lambda sid, word, **kw: T(sid, "exec", cmd="echo groups=$JQA_FAKE_GROUPS", expect_exit=0, stdout_contains=[f"groups={word}"], **kw)
    change = T("change-groups", "exec", cmd=f"echo new > {state}/groups", expect_exit=0)
    steps = [sees("before-change", "old"), change, sees("stale-without-reconnect", "old"), sees("fresh-with-reconnect", "new", reconnect=True)]
    scen = base_scenario("reconnect", steps)

    reset()
    r = run_sh(scen, *hooks, hook_cfg=cfg("hc-ok.json"))
    check("reconnect: without the flag the next step still sees the old membership; with it the step sees the new one",
          r.rc == 0 and r.res and r.res["passed"], (r.rc, r.stdout[-500:], r.stderr[-300:]))

    # must-fail twin: the same step without the flag must fail its expectation (the cache really is stale)
    reset()
    no_flag = copy.deepcopy(scen)
    del no_flag["steps"][3]["reconnect"]
    r = run_sh(no_flag, *hooks, hook_cfg=cfg("hc-ok.json"))
    check("reconnect control: the step that expects the new membership fails when reconnect is not requested",
          r.rc == 1 and r.res and r.res["failedStep"] == "fresh-with-reconnect", (r.rc, r.res and r.res["failedStep"]))

    # must-fail: a wrapper is configured, reconnect is requested and no hook exists
    reset()
    r = run_sh(scen, hook_cfg=cfg("hc-ok.json"))
    out = r.stdout + r.stderr
    check("reconnect control: reconnect with a wrapper and no hook exits 2 naming the step and the hook, before any step runs",
          r.rc == 2 and "fresh-with-reconnect" in out and "reconnect(ctx, step)" in out and not os.path.exists(os.path.join(r.out, "terminal")), (r.rc, out[-400:]))

    # must-fail: the hook fails
    reset()
    r = run_sh(scen, *hooks, hook_cfg=cfg("hc-fail.json", fail=True))
    st = {x["id"]: x for x in (r.res or {"steps": []})["steps"]}
    check("reconnect control: a failing reconnect hook fails that step and the run (exit 1) and the driver is not started",
          r.rc == 1 and r.res and r.res["failedStep"] == "fresh-with-reconnect" and "cannot restart the session" in st["fresh-with-reconnect"].get("error", "")
          and not os.path.exists(os.path.join(r.out, "terminal", "fresh-with-reconnect.result.json")), (r.rc, r.res and r.res["failedStep"]))

    # twin: without a wrapper nothing persists, so the flag needs no hook
    plain = base_scenario("reconnect-plain", [T("plain", "exec", cmd="echo tool 1.2.3", expect_exit=0, stdout_contains=["tool 1.2.3"], reconnect=True)])
    r = run_sh(plain)
    check("reconnect twin: without a wrapper the flag needs no hook and the run passes", r.rc == 0 and r.res and r.res["passed"], (r.rc, r.stdout[-300:], r.stderr))
    check("no tmux server or socket is left behind after the reconnect controls", not os.listdir(SOCKS), os.listdir(SOCKS))


# ---------------------------------------------------------------------------- Part B

def part_b(engine_args):
    print("-- Part B: terminal and browser steps in one scenario, against the invented demo app")
    log = os.path.join(WORK, "http.log")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory", os.path.join(HERE, "app")],
                           stdout=subprocess.DEVNULL, stderr=open(log, "a"))
    try:
        for _ in range(60):
            try:
                socket.create_connection(("127.0.0.1", port), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        with open(os.path.join(HERE, "smoke.json")) as fh:
            smoke = json.load(fh)
        browser = {x["id"]: x for x in smoke["steps"]}
        order = ["ivy-login-open", "ivy-login-email", "ivy-login-password", "ivy-login-submit", "show-code", "code-done"]
        prompt = ("bash -c 'printf \"Code: \"; read -r -s c; echo; if [ ${#c} -ge 8 ]; then echo accepted; else echo rejected; exit 4; fi'")
        steps = [
            T("before-browser", "exec", cmd='test "$(grep -c "GET /home/" "$JQA_MIXED_LOG")" = 0', expect_exit=0),
            *[browser[i] for i in order],
            T("enter-code", "pty", cmd=prompt, timeout_s=30, expect_exit=0, stdout_contains=["accepted"],
              dialog=[{"wait_for": "Code:", "send": "{{secret.one-time-code}}", "secret": True}], shot="code-turn"),
            T("after-browser", "exec", cmd='test "$(grep -c "GET /home/" "$JQA_MIXED_LOG")" -ge 1', expect_exit=0),
            {"id": "ivy-back", "persona": "ivy", "surface": "browser", "action": "goto", "target": "/home/",
             "expect": {"visible": ["@user-menu({{persona.ivy.name}})"]}},
        ]
        shots = [sh for sh in smoke["shots"] if sh["id"] in ("home", "code")]
        shots.append({"id": "code-turn", "file": "03-code-turn.png", "terminal_from": "enter-code", "wait_for": ["transcript:accepted"]})
        scenario = {k: smoke[k] for k in ("schema_version", "title", "product", "adapter", "roster", "selectors", "determinism", "environment")}
        shutil.copy(os.path.join(HERE, "selectors.json"), os.path.join(WORK, "selectors.json"))
        scenario.update({"id": "mixed", "roster": os.path.relpath(ROSTER, WORK), "selectors": "selectors.json", "steps": steps, "shots": shots,
                         "personas": [{"ref": "ivy", "as": "admin"}], "secrets": {"one-time-code": {"class": "test"}},
                         "assertions": [a for a in smoke["assertions"] if a["id"] == "greeting"]})
        env = {"JQA_MIXED_LOG": log}
        extra = ["--hooks", os.path.join(HERE, "hooks.mjs"), "--base", f"app=http://127.0.0.1:{port}", *engine_args]
        r = run_sh(scenario, *extra, env=env)
        check("mixed: terminal and browser steps pass in one run", r.rc == 0 and r.res and r.res["passed"], (r.rc, r.stdout[-800:], r.stderr[-400:]))
        got = [x["id"] for x in (r.res or {"steps": []})["steps"] if not x["id"].startswith("shot:")]
        check("mixed: steps ran in scenario order, terminal steps between browser steps", got == [x["id"] for x in steps], got)
        check("mixed: the first terminal step ran before the browser touched the app, the later one after",
              r.res and all(x.get("ok") for x in r.res["steps"]), r.res and [x for x in r.res["steps"] if not x.get("ok")])
        eng = json.load(open(os.path.join(r.out, "engine.json")))["engine"] if r.res else None
        check("mixed: a browser engine was used", eng in ("playwright", "ego") and r.res["engine"] == eng, eng)
        code_file = os.path.join(r.out, "secrets", "values", "one-time-code")
        code = open(code_file).read().strip() if os.path.exists(code_file) else ""
        check("mixed: a secret captured on the page reached the pty prompt and appears in no kept file",
              len(code) >= 8 and files_with(code, r.out) == [], (len(code), files_with(code, r.out) if code else None))
        caps = [c["file"] for c in (r.res or {"captures": []})["captures"]]
        check("mixed: browser captures and the terminal shot are all present", sorted(caps) == ["shots/01-home.png", "shots/02-code.png", "shots/03-code-turn.png"], caps)
        text = open(os.path.join(r.out, "terminal", "enter-code.txt")).read() if r.res else ""
        check("mixed: the pty transcript shows the placeholder, not the code", "accepted" in text and code not in text, text)
        # must-fail twin: the later terminal step must really judge browser-made state
        twin = copy.deepcopy(scenario)
        twin["steps"][-2]["cmd"] = 'test "$(grep -c "GET /home/" "$JQA_MIXED_LOG")" = 0'
        twin["id"] = "mixed-twin"
        open(log, "w").close()  # the app has seen no request since (the server appends)
        r = run_sh(twin, *extra, env=env)
        check("mixed control: a terminal step that expects the app untouched after browser steps fails the run",
              r.rc == 1 and r.res and r.res["failedStep"] == "after-browser", (r.rc, r.res and r.res["failedStep"]))
    finally:
        srv.terminate()
        srv.wait()


def main():
    if not shutil.which("tmux") or not shutil.which("node") or not shutil.which("python3"):
        print("cannot run: tmux (3.2+), node (18+) and python3 are required", file=sys.stderr)
        return 2
    args = sys.argv[1:]
    try:
        part_a()
        if args:
            part_b(args)
        else:
            print("SKIPPED Part B (terminal + browser in one scenario): no browser engine given. "
                  "Run orchestrate.sh --engine playwright --pw-module PATH [--pw-channel NAME] or --engine ego --ego-space ID to run it.")
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
        shutil.rmtree(SOCKS, ignore_errors=True)
    bad = [n for n, ok in results if not ok]
    print(f"controls: {len(results)}, failed: {len(bad)}" + ("" if args else ", browser part skipped"))
    return 1 if bad or not results else 0


if __name__ == "__main__":
    sys.exit(main())
