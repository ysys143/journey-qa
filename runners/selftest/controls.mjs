// Controls for the runner library that need no browser: the emulation rules, the failed-request
// records, and the interpreter driven through a stub backend. Each behavior has a must-pass and a must-fail
// control. Prints "OK <label>" or "MISMATCH <label>"; exit 0 only when every control holds.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { ConfigError, Run } from '../lib/run.mjs';
import { resolveEmulation } from '../lib/emulation.mjs';
import { NetworkCollector, describeUrl, failedRequestsProblem, notCollected } from '../lib/network.mjs';
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
async function interpret(doc, { roster = ROSTER, backend = stubBackend(), beforeRun, deny = [] } = {}) {
  const dir = await mkdtemp(join(tmpdir(), 'jqa-controls-'));
  scratch.push(dir);
  const out = join(dir, 'out');
  await mkdir(join(out, 'secrets'), { recursive: true });
  await writeFile(join(dir, 'scenario.json'), JSON.stringify(doc));
  const denylists = [DENYLIST];
  if (deny.length) { // an invented stand-in for a real identifier, for this run only
    await writeFile(join(dir, 'deny.txt'), `${deny.join('\n')}\n`);
    denylists.push(join(dir, 'deny.txt'));
  }
  const cfg = {
    repo: REPO, out, scenario: join(dir, 'scenario.json'), scenarioDir: dir, roster, rosterPath: roster, hooks: null, hookConfig: {},
    secretsDir: null, stateFrom: null, injectFailure: null, bases: {}, approvedGates: [], denylists, policies: [], evidenceOcr: 'off',
    python: 'python3', actionTimeout: 500, navTimeout: 500, engine: 'stub',
  };
  const log = console.log;
  console.log = () => {}; // the interpreter narrates every step
  try {
    const run = await Run.load(cfg);
    if (beforeRun) beforeRun(run);
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


// ---------------------------------------------------------------- failed-request records

const origins = new Map([['http://app.test', 'app']]);
const cdp = (method, params) => ({ method, params });
const sent = (id, url, t, extra = {}) => cdp('Network.requestWillBeSent', { requestId: id, request: { url, method: 'GET' }, type: 'Fetch', timestamp: t, ...extra });
const got = (id, status, t) => cdp('Network.responseReceived', { requestId: id, response: { status }, timestamp: t });
const done = (id, t) => cdp('Network.loadingFinished', { requestId: id, timestamp: t });
const broke = (id, errorText, t, extra = {}) => cdp('Network.loadingFailed', { requestId: id, errorText, timestamp: t, ...extra });
const collect = (events) => { const c = new NetworkCollector({ origins }); c.ingestCdp(events); return c; };

await control('network: query values are replaced, keys are kept, the origin becomes the instance id', () => {
  const d = describeUrl('http://app.test/api/boom?session=abc123&page=2&debug', origins);
  assert.deepEqual(d, { instance: 'app', path: '/api/boom', query: 'session=[redacted]&page=[redacted]&debug' });
  assert.ok(!JSON.stringify(d).includes('abc123'));
});
await control('network: a request to another origin is described by its host; userinfo and fragment are dropped', () => {
  const d = describeUrl('https://user:pw@cdn.example.net/x.js?v=9#frag', origins);
  assert.deepEqual(d, { host: 'cdn.example.net', path: '/x.js', query: 'v=[redacted]' });
  assert.ok(!JSON.stringify(d).includes('pw') && !JSON.stringify(d).includes('frag'));
});
await control('network: a scheme that is not a network request is not tracked', () => {
  assert.equal(describeUrl('data:text/plain,hello', origins), null);
  assert.equal(describeUrl('chrome-extension://abc/x.js', origins), null);
});
await control('network: 200 responses leave a clean record with the request count', () => {
  const s = collect([sent('1', 'http://app.test/a', 10), got('1', 200, 10.1), done('1', 10.2), sent('2', 'http://app.test/b', 11), got('2', 304, 11.1), done('2', 11.2)]).snapshot();
  assert.equal(s.total, 2);
  assert.deepEqual(s.failed, []);
  assert.equal(failedRequestsProblem(s), null);
});
await control('network: a status of 400 or more is a failed request with method, path, status and timing', () => {
  const s = collect([sent('1', 'http://app.test/api/boom?session=x', 10), got('1', 500, 10.25), done('1', 10.3)]).snapshot();
  assert.deepEqual(s.failed, [{ method: 'GET', instance: 'app', path: '/api/boom', query: 'session=[redacted]', status: 500, failure: null, kind: 'http-error', resource: 'Fetch', ms: 250 }]);
});
await control('network: a request that fails at the network level is a failed request with its error text', () => {
  const s = collect([sent('1', 'http://app.test/api/abort', 10), broke('1', 'net::ERR_EMPTY_RESPONSE', 10.03)]).snapshot();
  assert.equal(s.failed.length, 1);
  assert.equal(s.failed[0].kind, 'failed');
  assert.equal(s.failed[0].failure, 'net::ERR_EMPTY_RESPONSE');
  assert.equal(s.failed[0].status, null);
});
await control('network: a cancelled request is reported as aborted', () => {
  const s = collect([sent('1', 'http://app.test/poll', 10), broke('1', 'net::ERR_ABORTED', 10.5, { canceled: true })]).snapshot();
  assert.equal(s.failed[0].kind, 'aborted');
});
await control('network: an error status that then breaks off is one record, not two', () => {
  const s = collect([sent('1', 'http://app.test/x', 10), got('1', 502, 10.1), broke('1', 'net::ERR_CONTENT_LENGTH_MISMATCH', 10.2)]).snapshot();
  assert.equal(s.failed.length, 1);
  assert.equal(s.failed[0].status, 502);
  assert.equal(s.failed[0].failure, 'net::ERR_CONTENT_LENGTH_MISMATCH');
});
await control('network: a redirect is not a failure; the final response decides', () => {
  const ok = collect([sent('1', 'http://app.test/old', 10), sent('1', 'http://app.test/new', 10.1, { redirectResponse: { status: 302 } }), got('1', 200, 10.2), done('1', 10.3)]).snapshot();
  assert.deepEqual(ok.failed, []);
  assert.equal(ok.total, 2);
  const missing = collect([sent('1', 'http://app.test/old', 10), sent('1', 'http://app.test/new', 10.1, { redirectResponse: { status: 302 } }), got('1', 404, 10.2)]).snapshot();
  assert.equal(missing.failed.length, 1);
  assert.equal(missing.failed[0].path, '/new');
});
await control('network: draining starts the next step\'s window and keeps requests that are still waiting for an answer', () => {
  const c = collect([sent('1', 'http://app.test/a', 10), got('1', 404, 10.1), sent('2', 'http://app.test/slow', 10.2)]);
  const first = c.snapshot(true);
  assert.equal(first.failed.length, 1);
  assert.equal(first.in_flight, 1); // /slow has no answer yet; /a was answered although its body is still arriving
  c.ingestCdp([done('1', 10.3), broke('2', 'net::ERR_CONNECTION_RESET', 11)]);
  const second = c.snapshot(true);
  assert.equal(second.total, 0);
  assert.equal(second.failed.length, 1);
  assert.equal(second.failed[0].path, '/slow');
  assert.equal(c.snapshot().failed.length, 0);
});
await control('network: the failure message names each request without a query value', () => {
  const s = collect([sent('1', 'http://app.test/api/boom?session=abc', 10), got('1', 500, 10.1)]).snapshot();
  const msg = failedRequestsProblem(s);
  assert.match(msg, /^1 failed request: GET \/api\/boom\?session=\[redacted\] \(500\)$/);
});
await control('network: MUST FAIL a step whose requests were not collected is not proven clean', () => {
  const msg = failedRequestsProblem(notCollected('the engine has no request feed'));
  assert.match(msg, /cannot be proven: not collected: the engine has no request feed/);
  assert.match(failedRequestsProblem(null), /cannot be proven/);
});

// ---------------------------------------------------------------- failed requests through the interpreter

const clickStep = (id, extra = {}) => ({ id, persona: 'ivy', surface: 'browser', action: 'click', target: 'css=#go', expect: { visible: ['css=#done'], ...extra } });
const web = (path) => `http://app.test${path}`;
const ivy = [{ ref: 'ivy', as: 'admin' }];
const app = (doc) => ({ ...doc, environment: { instances: [{ id: 'app', purpose: 'stub', base_url: 'http://app.test' }] } });

await control('interpreter: every browser step leaves a gated network/<step>.json, clean steps included', async () => {
  const backend = stubBackend({ script: { '#go': [{ url: web('/api/ok?scope=team'), status: 200 }] }, instanceByOrigin: new Map([['http://app.test', 'app']]) });
  const { result, read } = await interpret(app(scenario(ivy, [clickStep('ivy-load', { no_failed_requests: true })])), { backend });
  assert.equal(result.passed, true);
  const rec = await read('network/ivy-load.json');
  assert.deepEqual({ step: rec.step, persona: rec.persona, backend: rec.backend, collected: rec.collected, total: rec.total, failed: rec.failed }, { step: 'ivy-load', persona: 'ivy', backend: 'stub', collected: true, total: 1, failed: [] });
  assert.equal(result.steps[0].network.file, 'network/ivy-load.json');
  assert.deepEqual(result.network, { steps: 1, stepsWithFailedRequests: [], notCollected: [], discarded: [] });
});
await control('interpreter: a failed request is recorded without the expectation, and the step still passes', async () => {
  const backend = stubBackend({ script: { '#go': [{ url: web('/api/boom?session=abc123&page=2'), status: 500, ms: 14 }] }, instanceByOrigin: new Map([['http://app.test', 'app']]) });
  const { result, read } = await interpret(app(scenario(ivy, [clickStep('ivy-report')])), { backend });
  assert.equal(result.passed, true);
  const rec = await read('network/ivy-report.json');
  assert.equal(rec.failed.length, 1);
  assert.deepEqual([rec.failed[0].method, rec.failed[0].path, rec.failed[0].query, rec.failed[0].status, rec.failed[0].ms], ['GET', '/api/boom', 'session=[redacted]&page=[redacted]', 500, 14]);
  assert.ok(!JSON.stringify(rec).includes('abc123'));
  assert.deepEqual(result.network.stepsWithFailedRequests, ['ivy-report']);
});
await control('interpreter: MUST FAIL no_failed_requests on a step with an HTTP 500 fails the step and keeps the record as failure evidence', async () => {
  const backend = stubBackend({ script: { '#ok': [{ url: web('/api/ok'), status: 200 }], '#go': [{ url: web('/api/boom?session=abc123'), status: 500 }] }, instanceByOrigin: new Map([['http://app.test', 'app']]) });
  const { result, read, out } = await interpret(app(scenario(ivy, [{ ...clickStep('ivy-ok'), target: 'css=#ok' }, clickStep('ivy-report', { no_failed_requests: true })])), { backend });
  assert.equal(result.passed, false);
  assert.equal(result.failedStep, 'ivy-report');
  const step = result.steps.find((x) => x.id === 'ivy-report');
  assert.match(step.error, /^1 failed request: GET \/api\/boom\?session=\[redacted\] \(500\)$/);
  assert.equal(step.evidenceKept, true);
  const attached = await read('failure/ivy-report/network.json');
  assert.equal(attached.failed[0].status, 500);
  assert.ok(!JSON.stringify(attached).includes('abc123'));
  assert.equal((await read('network/ivy-report.json')).failed.length, 1);
  assert.equal(result.network.stepsWithFailedRequests[0], 'ivy-report');
  assert.ok(out);
});
await control('interpreter: MUST FAIL no_failed_requests on a request that broke off fails the step', async () => {
  const backend = stubBackend({ script: { '#go': [{ url: web('/api/abort'), fail: 'net::ERR_EMPTY_RESPONSE' }] }, instanceByOrigin: new Map([['http://app.test', 'app']]) });
  const { result } = await interpret(app(scenario(ivy, [clickStep('ivy-sync', { no_failed_requests: true })])), { backend });
  assert.equal(result.passed, false);
  assert.match(result.steps[0].error, /GET \/api\/abort \(net::ERR_EMPTY_RESPONSE\)/);
});
await control('interpreter: a step is judged by its own window, not the one before', async () => {
  const backend = stubBackend({ script: { '#bad': [{ url: web('/api/boom'), status: 500 }], '#go': [{ url: web('/api/ok'), status: 200 }] }, instanceByOrigin: new Map([['http://app.test', 'app']]) });
  const doc = app(scenario(ivy, [{ ...clickStep('ivy-bad'), target: 'css=#bad' }, clickStep('ivy-clean', { no_failed_requests: true })]));
  const { result, read } = await interpret(doc, { backend });
  assert.equal(result.passed, true);
  assert.equal((await read('network/ivy-bad.json')).failed.length, 1);
  assert.equal((await read('network/ivy-clean.json')).failed.length, 0);
});
await control('interpreter: a backend that cannot collect says "not collected" in the record, and the expectation fails', async () => {
  const noFeed = () => stubBackend({ collects: false });
  const plain = await interpret(app(scenario(ivy, [clickStep('ivy-load')])), { backend: noFeed() });
  assert.equal(plain.result.passed, true);
  const rec = await plain.read('network/ivy-load.json');
  assert.equal(rec.collected, false);
  assert.match(rec.reason, /^not collected: /);
  assert.deepEqual(plain.result.network.notCollected, ['ivy-load']);
  const strict = await interpret(app(scenario(ivy, [clickStep('ivy-load', { no_failed_requests: true })])), { backend: noFeed() });
  assert.equal(strict.result.passed, false);
  assert.match(strict.result.steps[0].error, /cannot be proven: not collected/);
});
await control('interpreter: MUST FAIL a record the gate rejects is discarded, not kept', async () => {
  const hit = stubBackend({ script: { '#go': [{ url: web('/users/quillfeather/profile'), status: 404 }] }, instanceByOrigin: new Map([['http://app.test', 'app']]) });
  const rejected = await interpret(app(scenario(ivy, [clickStep('ivy-profile')])), { backend: hit, deny: ['Quillfeather'] });
  assert.equal(rejected.result.passed, true);
  assert.match(rejected.result.steps[0].network.file, /^discarded: /);
  await assert.rejects(readFile(join(rejected.out, 'network', 'ivy-profile.json')));
  assert.deepEqual(rejected.result.network.discarded, ['ivy-profile']);
  const clean = stubBackend({ script: { '#go': [{ url: web('/users/someone/profile'), status: 404 }] }, instanceByOrigin: new Map([['http://app.test', 'app']]) });
  const kept = await interpret(app(scenario(ivy, [clickStep('ivy-profile')])), { backend: clean, deny: ['Quillfeather'] });
  assert.equal(kept.result.steps[0].network.file, 'network/ivy-profile.json');
});
await control('interpreter: a one-time secret in a path is replaced before the record is written', async () => {
  const backend = stubBackend({ script: { '#go': [{ url: web('/reset/tok-7f3a91c2d4e5'), status: 410 }] }, instanceByOrigin: new Map([['http://app.test', 'app']]) });
  const { read, run } = await interpret(app(scenario(ivy, [clickStep('ivy-reset')])), { backend, beforeRun: (r) => r.registerSecret('tok-7f3a91c2d4e5', 'reset') });
  const rec = await read('network/ivy-reset.json');
  assert.equal(rec.failed[0].path, '/reset/[REDACTED]');
  assert.ok(run);
});


// ---------------------------------------------------------------- the demo app the browser smoke test uses

await control('demo app: /api/ok answers 200, /api/boom answers 500, /api/abort drops the connection, pages are served', async () => {
  const port = await new Promise((resolve) => { const s = createServer(); s.listen(0, '127.0.0.1', () => { const { port: p } = s.address(); s.close(() => resolve(p)); }); });
  const server = spawn('python3', [join(HERE, 'app_server.py'), String(port)], { stdio: 'ignore' });
  try {
    const base = `http://127.0.0.1:${port}`;
    for (let i = 0; ; i++) {
      try { await fetch(`${base}/login/`); break; } catch (e) { if (i > 50) throw e; await new Promise((r) => setTimeout(r, 100)); }
    }
    assert.equal((await fetch(`${base}/api/ok?scope=team`)).status, 200);
    assert.equal((await fetch(`${base}/api/boom?session=x`)).status, 500);
    assert.equal((await fetch(`${base}/network/`)).status, 200);
    await assert.rejects(fetch(`${base}/api/abort`));
  } finally {
    server.kill();
  }
});


// ---------------------------------------------------------------- the workflow examples in templates/examples/

const AsyncFunction = Object.getPrototypeOf(async () => {}).constructor;
const EXAMPLES = join(REPO, 'templates', 'examples');
/** Compile an example workflow script (it uses top-level await and return) and run it against stub agent(), phase() and log(). */
async function runExample(source, args, respond) {
  const fn = new AsyncFunction('args', 'agent', 'phase', 'log', source.replace(/^export /gm, ''));
  const calls = [];
  const agent = async (prompt, opts) => { calls.push({ prompt, opts }); return respond({ prompt, opts, calls }); };
  const result = await fn(args, agent, () => {}, () => {});
  return { result, calls };
}

await control('examples: every workflow script compiles', async () => {
  for (const name of ['workflow-phase.js', 'independent-personas.js']) {
    new AsyncFunction('args', 'agent', 'phase', 'log', (await readFile(join(EXAMPLES, name), 'utf8')).replace(/^export /gm, ''));
  }
});
await control('examples: MUST FAIL a script with a syntax error does not compile', () => {
  assert.throws(() => new AsyncFunction('args', 'agent', 'phase', 'log', 'const x = ;'), SyntaxError);
});

const personaArgs = {
  runDir: '/runs/r1', docsEntry: 'docs/index.md', rosterPath: '/runs/roster.json', rules: 'no production writes', parallel: 2,
  journeys: [{ id: 'first-run', goal: 'reach a working product' }],
  personas: ['ivy', 'marcus', 'tomas'].map((id) => ({ id, profile: `PROFILE-OF-${id.toUpperCase()}`, browserProfile: `bp-${id}`, instance: `inst-${id}` })),
  models: { persona: 'tier-persona', executor: 'tier-executor', gate: 'tier-gate' }, gateAgentType: 'read-only-verifier',
};
const answers = (over = {}) => ({ opts }) => {
  const label = opts.label || opts.phase;
  if (over[label]) return over[label];
  if (opts.phase === 'Prepare') return { directories: [] };
  if (opts.phase === 'Explore') return { status: 'done', summary: 'walked it', findings: 1 };
  if (opts.phase === 'Merge') return { exitCode: 0, output: 'merged', defects: 1 };
  return { pass: true, checks: [] };
};
const source = await readFile(join(EXAMPLES, 'independent-personas.js'), 'utf8');

await control('examples: each persona session gets its own profile and no other persona\'s', async () => {
  const { result, calls } = await runExample(source, personaArgs, answers());
  const sessions = calls.filter((c) => c.opts.phase === 'Explore');
  assert.equal(sessions.length, 3);
  for (const c of sessions) {
    const id = c.opts.label.split(':')[1];
    assert.ok(c.prompt.includes(`PROFILE-OF-${id.toUpperCase()}`));
    for (const other of ['ivy', 'marcus', 'tomas'].filter((x) => x !== id)) {
      assert.ok(!c.prompt.includes(`PROFILE-OF-${other.toUpperCase()}`), `${id} saw ${other}`);
      assert.ok(!c.prompt.includes(`bp-${other}`) && !c.prompt.includes(`inst-${other}`), `${id} was given ${other}'s state`);
    }
    assert.equal(c.opts.model, 'tier-persona');
  }
  const prepare = calls.find((c) => c.opts.phase === 'Prepare');
  assert.ok(!/PROFILE-OF-/.test(prepare.prompt));
  assert.equal(result.verdict.pass, true);
  assert.equal(calls.find((c) => c.opts.phase === 'Judge').opts.model, 'tier-gate');
  assert.equal(calls.find((c) => c.opts.phase === 'Judge').opts.agentType, 'read-only-verifier');
});
await control('examples: MUST FAIL a blocked persona session stops the run before the merge', async () => {
  const { result, calls } = await runExample(source, personaArgs, answers({ 'persona:marcus': { status: 'blocked', summary: 'x', blockedBecause: 'the instance was not clean' } }));
  assert.equal(result.blockedAt, 'Explore');
  assert.ok(!calls.some((c) => c.opts.phase === 'Merge' || c.opts.phase === 'Judge'));
});
await control('examples: MUST FAIL a merge that exits 1 stops the run before the judge', async () => {
  const { result, calls } = await runExample(source, personaArgs, answers({ Merge: { exitCode: 1, output: 'ISOLATION ivy, marcus share a session_id' } }));
  assert.equal(result.blockedAt, 'Merge');
  assert.ok(!calls.some((c) => c.opts.phase === 'Judge'));
});

await Promise.all(scratch.map((d) => rm(d, { recursive: true, force: true })));
console.log(`controls: ${n}, mismatches: ${bad}`);
process.exit(n > 0 && bad === 0 ? 0 : 1);
