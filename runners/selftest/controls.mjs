// Controls for the runner library that need no browser: the emulation rules, and the
// interpreter driven through a stub backend. Each behavior has a must-pass and a must-fail
// control. Prints "OK <label>" or "MISMATCH <label>"; exit 0 only when every control holds.
import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { ConfigError, Run } from '../lib/run.mjs';
import { resolveEmulation } from '../lib/emulation.mjs';
import { stubBackend } from './stub-backend.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = join(HERE, '..', '..');
const ROSTER = join(REPO, 'fixtures', 'roster.example.json');
const PLAIN_ROSTER = join(REPO, 'fixtures', 'selftest', 'pass-dual-provider.json'); // no perspective fields
const DENYLIST = join(REPO, 'gates', 'selftest', 'denylist.test.txt');

let n = 0;
let bad = 0;
async function control(label, fn) {
  n += 1;
  try {
    await fn();
    console.log(`OK ${label}`);
  } catch (e) {
    bad += 1;
    console.log(`MISMATCH ${label}: ${String(e && e.message ? e.message : e).split('\n')[0]}`);
  }
}
async function rejects(fn, pattern) {
  try {
    await fn();
  } catch (e) {
    assert.match(String(e && e.message), pattern);
    return e;
  }
  throw new Error('expected a failure and there was none');
}

const determinism = { seed: 1, time_anchor: 'now', viewport: { width: 1440, height: 900, device_scale_factor: 2 }, locale: 'en-US', timezone: 'UTC' };
const person = (persona) => ({ id: 'p', persona });
const resolve = (persona, spec, supports) => resolveEmulation({ determinism, person: person(persona), spec, supports });

// ---------------------------------------------------------------- emulation rules

await control('emulation: no spec keeps the pinned viewport and applies nothing', () => {
  const e = resolve({ device: 'mobile' }, undefined);
  assert.deepEqual(e.viewport, determinism.viewport);
  assert.equal(e.mobile, false);
  assert.deepEqual(e.applied, []);
});
await control('emulation: from_roster mobile gives the phone viewport with touch', () => {
  const e = resolve({ device: 'mobile' }, { from_roster: true });
  assert.deepEqual(e.viewport, { width: 390, height: 844, device_scale_factor: 3 });
  assert.equal(e.mobile, true);
});
await control('emulation: from_roster tablet gives the tablet viewport with touch', () => {
  const e = resolve({ device: 'tablet' }, { from_roster: true });
  assert.deepEqual(e.viewport, { width: 820, height: 1180, device_scale_factor: 2 });
  assert.equal(e.mobile, true);
});
await control('emulation: from_roster desktop keeps the pinned viewport', () => {
  const e = resolve({ device: 'desktop' }, { from_roster: true });
  assert.deepEqual(e.viewport, determinism.viewport);
  assert.equal(e.mobile, false);
});
await control('emulation: assistive_tech reduced-motion and zoom are applied', () => {
  const e = resolve({ assistive_tech: ['reduced-motion', 'zoom'] }, { from_roster: true });
  assert.equal(e.reducedMotion, true);
  assert.equal(e.zoom, 2);
  assert.deepEqual(e.viewport, { width: 720, height: 450, device_scale_factor: 4 }); // the CSS viewport halves, the pixel ratio doubles
});
await control('emulation: explicit fields override the roster', () => {
  const e = resolve({ device: 'mobile', assistive_tech: ['zoom'] }, { from_roster: true, mobile: false, zoom: 1.5, viewport: { width: 600, height: 800, device_scale_factor: 2 } });
  assert.equal(e.mobile, false);
  assert.equal(e.zoom, 1.5);
  assert.deepEqual(e.viewport, { width: 400, height: 533, device_scale_factor: 3 });
});
await control('emulation: what a browser cannot emulate is reported, not dropped', () => {
  const e = resolve({ device: 'desktop', assistive_tech: ['screen-reader', 'keyboard-only', 'high-contrast', 'voice-control'] }, { from_roster: true });
  assert.deepEqual(e.notApplied, ['assistive_tech:screen-reader', 'assistive_tech:keyboard-only', 'assistive_tech:high-contrast', 'assistive_tech:voice-control']);
});
await control('emulation: a feature the backend does not support is reported and left off', () => {
  const e = resolve({ assistive_tech: ['reduced-motion'] }, { from_roster: true }, new Set(['viewport', 'mobile']));
  assert.equal(e.reducedMotion, false);
  assert.deepEqual(e.notApplied, ['reduced_motion']);
});
await control('emulation: zoom without viewport support is reported and left off', () => {
  const e = resolve({}, { zoom: 2 }, new Set(['mobile']));
  assert.equal(e.zoom, 1);
  assert.deepEqual(e.viewport, determinism.viewport);
  assert.deepEqual(e.notApplied, ['zoom']);
});
await control('emulation: MUST FAIL from_roster for a person with no device or assistive_tech', async () => {
  await rejects(() => resolve({ experience: 'novice' }, { from_roster: true }), /no persona\.device or persona\.assistive_tech/);
});

// ---------------------------------------------------------------- the interpreter, through a stub backend

const steps = (...ids) => ids.map((id) => ({ id, persona: id.split('-')[0], surface: 'browser', action: 'click', target: 'css=#go', expect: { visible: ['css=#done'] } }));
function scenario(personas, stepList, extra = {}) {
  return {
    schema_version: 1, id: 'stub', title: 'stub', roster: 'roster.json', determinism: { ...determinism, viewport: { width: 800, height: 600, device_scale_factor: 1 } },
    environment: { instances: [{ id: 'app', purpose: 'stub', base_url: 'http://stub.test' }] },
    personas, steps: stepList, ...extra,
  };
}

const scratch = [];

/** Run a scenario on a stub backend in a scratch directory (removed at exit); returns { result, run, calls, out, dir, read }. */
async function interpret(doc, { roster = ROSTER, backend = stubBackend() } = {}) {
  const dir = await mkdtemp(join(tmpdir(), 'jqa-controls-'));
  scratch.push(dir);
  const out = join(dir, 'out');
  await mkdir(join(out, 'secrets'), { recursive: true });
  await writeFile(join(dir, 'scenario.json'), JSON.stringify(doc));
  const cfg = {
    repo: REPO, out, scenario: join(dir, 'scenario.json'), scenarioDir: dir, roster, rosterPath: roster, hooks: null, hookConfig: {},
    secretsDir: null, stateFrom: null, injectFailure: null, bases: {}, approvedGates: [], denylists: [DENYLIST], policies: [], evidenceOcr: 'off',
    python: 'python3', actionTimeout: 500, navTimeout: 500, engine: 'stub',
  };
  const log = console.log;
  console.log = () => {}; // the interpreter narrates every step
  try {
    const run = await Run.load(cfg);
    const result = await run.execute(backend.create);
    return { result, run, calls: backend.calls, out, dir, read: async (p) => JSON.parse(await readFile(join(out, p), 'utf8')) };
  } finally {
    console.log = log;
  }
}

await control('interpreter: each persona switch hands the backend that persona\'s emulation', async () => {
  const doc = scenario(
    [{ ref: 'ivy', as: 'admin' }, { ref: 'priya', as: 'viewer', emulation: { from_roster: true } }],
    steps('ivy-open', 'priya-open'),
  );
  const { result, calls } = await interpret(doc);
  assert.equal(result.passed, true);
  const by = Object.fromEntries(calls.filter((c) => c.persona).map((c) => [c.persona, c.emu]));
  assert.deepEqual(by.ivy.viewport, { width: 800, height: 600, device_scale_factor: 1 });
  assert.equal(by.ivy.mobile, false);
  assert.deepEqual(by.priya.viewport, { width: 195, height: 422, device_scale_factor: 6 }); // mobile preset at the roster's double zoom
  assert.equal(by.priya.mobile, true);
  assert.deepEqual(result.notes.emulation.priya.applied, ['viewport 195x422@6', 'mobile', 'zoom x2']);
  assert.equal(result.notes.emulation.ivy, undefined);
});
await control('interpreter: a feature the backend cannot apply is recorded in the notes', async () => {
  const doc = scenario([{ ref: 'tomas', as: 'contributor', emulation: { from_roster: true } }], steps('tomas-open'));
  const { result } = await interpret(doc, { backend: stubBackend({ emulates: new Set(['viewport', 'mobile']) }) });
  assert.equal(result.passed, true);
  assert.ok(result.notes.emulation.tomas.notApplied.includes('reduced_motion'));
  assert.ok(result.notes.emulation.tomas.applied.includes('mobile'));
});
await control('interpreter: MUST FAIL from_roster for a roster person without perspective is a configuration error before any step', async () => {
  const doc = scenario([{ ref: 'ivy', as: 'admin', emulation: { from_roster: true } }], steps('ivy-open'));
  const backend = stubBackend();
  const e = await rejects(() => interpret(doc, { roster: PLAIN_ROSTER, backend }), /persona ivy: emulation\.from_roster/);
  assert.ok(e instanceof ConfigError);
  assert.equal(backend.calls.length, 0);
});

await Promise.all(scratch.map((d) => rm(d, { recursive: true, force: true })));
console.log(`controls: ${n}, mismatches: ${bad}`);
process.exit(n > 0 && bad === 0 ? 0 : 1);
