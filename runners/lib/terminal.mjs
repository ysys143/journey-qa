// Terminal steps inside a scenario run: preflight of the needed tools, one driver
// invocation per step (runners/terminal/*_driver.py), the hand-off of secrets between
// the browser side and the drivers, and the terminal shots rendered after the run from
// the normalized and redacted transcripts (capture/SPEC.md "Terminal images").
//
// The drivers are separate CLIs. This module builds each driver's step file from the
// scenario step, never puts a secret value on an argv, and reads the driver's
// result.json for the verdict.
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { join, relative } from 'node:path';
import { writePrivate } from './files.mjs';

const DRIVER_FILES = { exec: 'exec_driver.py', pty: 'pty_driver.py', tmux: 'tmux_driver.py' };
const DRIVER_FIELDS = {
  exec: ['cmd', 'expect_exit', 'stdout_contains', 'timeout_s'],
  pty: ['cmd', 'dialog', 'expect_exit', 'stdout_contains', 'timeout_s'],
  tmux: ['launch', 'size', 'keys', 'done_when', 'verify', 'verify_note', 'expect_exit', 'timeout_s', 'cleanup'],
};
const SECRET_RE = /\{\{\s*secret\.([A-Za-z0-9_-]+)\s*\}\}/g;

export const driverOf = (step) => step.driver || 'exec';

/** Adapter-local terminal settings: the hook config's `terminal` object (never in the scenario). */
export function terminalConfig(cfg) {
  const t = (cfg.hookConfig || {}).terminal || {};
  const wrapper = Array.isArray(t.wrapper) ? t.wrapper.map(shellQuote).join(' ') : t.wrapper || null;
  return { wrapper, blockedPattern: t.blocked_pattern || null, sqlCommand: t.sql_command || null, socketDir: t.socket_dir || null, tmux: t.tmux || 'tmux' };
}

function shellQuote(s) {
  return /^[A-Za-z0-9_@%+=:,./-]+$/.test(s) ? s : `'${String(s).replace(/'/g, `'\\''`)}'`;
}

function version(cmd, args) {
  const r = spawnSync(cmd, args, { encoding: 'utf8' });
  return r.error || r.status !== 0 ? null : `${r.stdout || ''}${r.stderr || ''}`.trim();
}

/** Names the first missing tool a terminal part of the scenario needs; null when all are there. */
export function missingTool(run) {
  const steps = run.s.steps.filter((x) => x.surface === 'terminal');
  if (!steps.length) return null;
  const py = run.cfg.python || 'python3';
  const pyv = version(py, ['--version']);
  const m = pyv && pyv.match(/Python (\d+)\.(\d+)/);
  if (!m || +m[1] < 3 || (+m[1] === 3 && +m[2] < 10)) return `python3 (3.10 or newer) is needed by the terminal drivers (${steps[0].id}); install it or pass a different interpreter`;
  const tmuxSteps = steps.filter((x) => driverOf(x) === 'tmux');
  if (tmuxSteps.length) {
    const tmux = terminalConfig(run.cfg).tmux;
    const v = version(tmux, ['-V']);
    if (!v) return `tmux is needed by step ${tmuxSteps[0].id} (driver tmux) and was not found; install tmux 3.2 or newer`;
    const tv = v.match(/(\d+)\.(\d+)/);
    if (tv && (+tv[1] < 3 || (+tv[1] === 3 && +tv[2] < 2))) return `tmux 3.2 or newer is needed by step ${tmuxSteps[0].id}; found ${v}`;
  }
  const used = new Set(run.s.steps.map((x) => x.shot).filter(Boolean));
  if ((run.s.shots || []).some((sh) => sh.terminal_from && used.has(sh.id)) && !version('rsvg-convert', ['--version'])) {
    return 'rsvg-convert is needed to render terminal shots and was not found; install librsvg';
  }
  return null;
}

/**
 * Names the first terminal step that asks for `reconnect: true` where a hook is required and missing; null otherwise.
 * A hook is required when the adapter configures a wrapper: the wrapper's session may persist between driver calls
 * and only the adapter knows how to restart it. Without a wrapper every driver call starts a fresh process (exec,
 * pty) or a fresh dedicated tmux server (tmux), so there is nothing stale and `reconnect` needs no hook.
 */
export function reconnectProblem(run) {
  const step = run.s.steps.find((x) => x.surface === 'terminal' && x.reconnect);
  if (!step || !terminalConfig(run.cfg).wrapper || typeof run.hooks.reconnect === 'function') return null;
  return `step ${step.id} sets reconnect: true and the adapter configures a terminal wrapper, but the hooks module has no reconnect(ctx, step) hook to restart its session`;
}

function secretNames(step) {
  const names = new Set();
  const walk = (node) => {
    if (typeof node === 'string') for (const m of node.matchAll(SECRET_RE)) names.add(m[1]);
    else if (Array.isArray(node)) node.forEach(walk);
    else if (node && typeof node === 'object') {
      for (const [k, v] of Object.entries(node)) {
        if ((k === 'paste_buffer' || k === 'secret') && typeof v === 'string') names.add(v);
        walk(v);
      }
    }
  };
  walk(step);
  return [...names];
}

/** The step file a driver reads: its fields, the declared secret classes and capture_as. */
export function driverStep(run, step) {
  const driver = driverOf(step);
  const out = { id: step.id };
  for (const k of DRIVER_FIELDS[driver]) if (step[k] !== undefined) out[k] = step[k];
  const classes = {};
  for (const [name, decl] of Object.entries(run.s.secrets || {})) classes[name] = decl.class;
  out.secret_classes = classes;
  if (step.secrets && step.secrets.capture_as) out.capture_as = step.secrets.capture_as;
  return out;
}

/** Copy the secrets the step names into <out>/secrets/values (mode 600): the one place the drivers read. */
async function materializeSecrets(run, names) {
  const dir = join(run.cfg.out, 'secrets', 'values');
  for (const name of names) {
    const value = await run.secretValue(name);
    await writePrivate(join(dir, name), value + '\n');
  }
  return dir;
}

/** Run one terminal step. Throws when the driver did not pass; returns the step's checks. */
export async function runTerminalStep(run, step) {
  const driver = driverOf(step);
  const cfg = run.cfg;
  const tc = terminalConfig(cfg);
  if (step.reconnect) {
    const problem = reconnectProblem(run);
    if (problem) throw new Error(problem);
    // A hook that throws fails this step (and the run); the driver is not started.
    if (typeof run.hooks.reconnect === 'function') await run.hooks.reconnect(run.ctx(), step);
  }
  const valuesDir = await materializeSecrets(run, secretNames(step));
  const denylist = await run.writeRunDenylist();
  const dir = join(cfg.out, 'secrets', 'terminal-steps');
  const stepFile = join(dir, `${step.id}.json`);
  await writePrivate(stepFile, JSON.stringify(driverStep(run, step), null, 1) + '\n');
  const argv = [join(cfg.repo, 'runners', 'terminal', DRIVER_FILES[driver]), '--step', stepFile, '--out', cfg.out,
    '--secrets-dir', valuesDir, '--run-denylist', denylist, '--roster', cfg.rosterPath];
  for (const d of cfg.denylists) argv.push('--denylist', d);
  for (const p of cfg.policies) argv.push('--policy', p);
  if (tc.wrapper) argv.push('--wrapper', tc.wrapper);
  if (driver === 'tmux') {
    if (tc.tmux !== 'tmux') argv.push('--tmux', tc.tmux);
    if (tc.blockedPattern) argv.push('--blocked-pattern', tc.blockedPattern);
    if (tc.sqlCommand) argv.push('--sql-command', tc.sqlCommand);
    if (tc.socketDir) argv.push('--socket-dir', tc.socketDir);
  }
  const res = spawnSync(cfg.python || 'python3', argv, { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
  if (res.error) throw new Error(`terminal ${driver}: cannot start the driver (${res.error.code || res.error.message})`);
  // Values the driver registered (capture_as) join this run's secrets.
  await absorbRunDenylist(run, denylist);
  const name = step.secrets && step.secrets.capture_as && step.secrets.capture_as.name;
  if (name && existsSync(join(valuesDir, name))) run.registerSecret((await readFile(join(valuesDir, name), 'utf8')).trim(), name);
  const resultPath = join(cfg.out, 'terminal', `${step.id}.result.json`);
  let result = null;
  if (existsSync(resultPath)) result = JSON.parse(await readFile(resultPath, 'utf8'));
  if (res.status === 2 || (!result && res.status !== 0)) {
    const why = (res.stderr || '').split('\n').find((l) => l.trim()) || 'no reason given';
    throw new Error(`terminal ${driver}: cannot run: ${why.slice(0, 200)}`);
  }
  const kept = !!(result && result.transcript);
  run.terminal.set(step.id, { kept, passed: !!(result && result.passed) });
  if (res.status !== 0 || !result || !result.passed) {
    const problems = result ? (result.problems || []).join('; ') || `status ${result.status}` : 'no result file';
    throw new Error(`terminal ${driver}: ${problems}${kept ? '' : ' (transcript not kept)'}`);
  }
  return { driver, status: result.status, exit: result.exit, transcript: result.transcript };
}

async function absorbRunDenylist(run, path) {
  let text;
  try {
    text = await readFile(path, 'utf8');
  } catch {
    return;
  }
  for (const line of text.split('\n')) {
    if (!line || line.startsWith('#') || run.oneTime.includes(line)) continue;
    try {
      run.registerSecret(line);
    } catch {
      // too short to gate; the drivers already refuse such values
    }
  }
}

/** Evidence of a failed terminal unit: the kept transcript, never a browser capture. */
export function terminalEvidence(run, rec, id) {
  const t = run.terminal.get(id);
  if (!t) {
    rec.evidence = 'none: a terminal shot is drawn from transcripts that are kept separately';
    rec.evidenceKept = false;
    return;
  }
  rec.evidenceKept = t.kept;
  rec.evidence = t.kept ? `terminal/${id}.txt` : `none: transcript not kept (see terminal/${id}.result.json)`;
}

/**
 * Render the terminal shots a run carried, in scenario order, from the redacted transcripts of
 * the steps they list. A shot whose steps did not all pass with a kept transcript is not drawn.
 */
export async function renderTerminalShots(run) {
  const used = new Set(run.s.steps.map((x) => x.shot).filter(Boolean));
  for (const shot of run.s.shots || []) {
    if (!shot.terminal_from || !used.has(shot.id)) continue;
    const ids = Array.isArray(shot.terminal_from) ? shot.terminal_from : [shot.terminal_from];
    if (!run.terminal.has(ids[ids.length - 1])) continue; // its carrying step did not run (the run stopped at a human gate)
    await run.unit(`shot:${shot.id}`, null, async () => {
      for (const id of ids) {
        const t = run.terminal.get(id);
        if (!t || !t.kept || !t.passed) throw new Error(`shot ${shot.id}: step ${id} has no kept, passing transcript`);
      }
      const parts = [];
      for (const id of ids) parts.push(await readFile(join(run.cfg.out, 'terminal', `${id}.txt`), 'utf8'));
      const text = parts.join('');
      for (const w of shot.wait_for) {
        const want = await run.template(w.slice('transcript:'.length));
        if (!text.includes(want)) throw new Error(`shot ${shot.id}: the transcript does not contain '${want}'`);
      }
      const work = join(run.cfg.out, 'terminal', 'shots');
      await mkdir(work, { recursive: true });
      const src = join(work, `${shot.id}.txt`);
      await writeFile(src, text);
      const gate = spawnSync(run.cfg.python || 'python3', [join(run.cfg.repo, 'gates', 'leakscan.py'), '--roster', run.cfg.rosterPath,
        ...run.cfg.denylists.flatMap((d) => ['--denylist', d]), '--denylist', await run.writeRunDenylist(),
        ...run.cfg.policies.flatMap((p) => ['--policy', p]), '--skip-images', src], { encoding: 'utf8' });
      if (gate.status !== 0) {
        await rm(src, { force: true });
        throw new Error(`shot ${shot.id}: the joined transcript failed the text gate (exit ${gate.status}); no image written`);
      }
      const dir = join(run.cfg.out, 'shots');
      await mkdir(dir, { recursive: true });
      const png = join(dir, shot.file);
      const r = spawnSync(run.cfg.python || 'python3', [join(run.cfg.repo, 'capture', 'render_terminal.py'), src, png], { encoding: 'utf8' });
      if (r.status !== 0) throw new Error(`shot ${shot.id}: render_terminal.py exit ${r.status}: ${(r.stderr || r.stdout || '').split('\n')[0].slice(0, 200)}`);
      run.captures.push({ file: relative(run.cfg.out, png), masks: 0, terminal: true, from: ids });
      return { shot: shot.id, render: (r.stdout || '').trim().split('\n').pop() };
    }, { terminal: true });
  }
}
