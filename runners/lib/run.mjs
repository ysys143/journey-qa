// Engine-neutral scenario interpreter. It executes the steps of a scenario
// (scenarios/schema.json) on one backend: personas and login state, actions,
// wait-based expectations, one-time secrets, captures with masks, failure
// evidence that is redacted and gated before it is kept, and results.json.
// Product knowledge enters only through the scenario, the adapter's selector
// map and the adapter's hooks module. No model is called during a run.
import { execFileSync, spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdir, readdir, readFile, rm, writeFile } from 'node:fs/promises';
import { dirname, join, relative } from 'node:path';
import * as P from './page.mjs';
import { writePrivate } from './files.mjs';

const WRONG_SELECTOR = '[data-jqa-injected-wrong-selector]';
const MASK = '\u2022'.repeat(12);
const REF_RE = /^@([a-z0-9]+(?:[.-][a-z0-9]+)*)(?:\((.+)\))?$/;
const TEMPLATE_RE = /\{\{\s*([a-z]+)\.([A-Za-z0-9_-]+)(?:\.([A-Za-z0-9_]+))?\s*\}\}/g;
const PERSONA_FIELDS = new Set(['name', 'email', 'handle', 'id', 'role', 'team']);
const BROWSER_ACTIONS = new Set(['goto', 'click', 'fill', 'select', 'press', 'wait', 'request']);

export class ConfigError extends Error {}

/** Parse a resolved locator: css=..., text=..., url:... */
function parseLocator(raw) {
  if (raw.startsWith('css=')) return { kind: 'css', value: raw.slice(4) };
  if (raw.startsWith('text=')) return { kind: 'text', value: raw.slice(5) };
  if (raw.startsWith('url:')) return { kind: 'url', value: raw.slice(4) };
  throw new ConfigError(`unsupported locator form (use @name, css=, text= or url:): ${raw.slice(0, 40)}`);
}

export class Run {
  static async load(cfg) {
    const scenario = JSON.parse(await readFile(cfg.scenario, 'utf8'));
    const dir = cfg.scenarioDir;
    const roster = JSON.parse(await readFile(cfg.roster || join(dir, scenario.roster), 'utf8'));
    let selectors = { selectors: {}, mask: {} };
    if (scenario.selectors) selectors = JSON.parse(await readFile(join(dir, scenario.selectors), 'utf8'));
    const hooks = cfg.hooks ? (await import(cfg.hooks)).default || {} : {};
    const run = new Run(cfg, scenario, roster, selectors, hooks);
    run.preflight();
    return run;
  }

  constructor(cfg, scenario, roster, selectors, hooks) {
    this.cfg = cfg;
    this.s = scenario;
    this.people = new Map(roster.people.map((p) => [p.id, p]));
    this.sel = selectors.selectors || {};
    this.mask = selectors.mask || {};
    this.hooks = hooks;
    this.steps = [];
    this.captures = [];
    this.secrets = new Map(); // name -> value, memory only
    this.oneTime = []; // every registered secret value
    this.asserted = new Map();
    this.notes = {};
    this.failed = null;
    this.current = null;
    this.persona = null;
    this.saved = new Set();
    this.instance = null;
    this.t0 = Date.now();
    this.statesDir = join(cfg.out, 'secrets');
    this.secretSelectors = [];
    this.armed = false;
  }

  /** Reject what this runner cannot execute before any browser starts. */
  preflight() {
    const s = this.s;
    for (const step of s.steps) {
      if (step.surface === 'terminal') throw new ConfigError(`step ${step.id}: terminal steps need a pty driver; this runner drives browsers`);
      if (!BROWSER_ACTIONS.has(step.action)) throw new ConfigError(`step ${step.id}: action ${step.action} is not supported`);
      const locs = [];
      if (step.target && step.action !== 'goto' && step.action !== 'request') locs.push(step.target);
      locs.push(...((step.expect || {}).visible || []), ...((step.expect || {}).hidden || []));
      for (const l of locs) this.locator(l, { check: true });
      for (const l of ((step.secrets || {}).to_run_denylist || [])) {
        const loc = this.locator(l, { check: true });
        if (loc.kind !== 'css' || /:has-text|:text-is|>>/.test(loc.value)) {
          throw new ConfigError(`step ${step.id}: secrets.to_run_denylist locators must be plain CSS`);
        }
        this.secretSelectors.push(loc.value);
      }
    }
    const used = new Set(s.steps.map((x) => x.shot).filter(Boolean));
    for (const shot of s.shots || []) {
      if (!used.has(shot.id)) continue;
      if (shot.terminal_from) throw new ConfigError(`shot ${shot.id}: terminal shots are rendered with capture/render_terminal.py`);
      for (const l of shot.wait_for) this.locator(l, { check: true });
      for (const l of shot.no_truncate || []) {
        if (this.locator(l, { check: true }).kind !== 'css') throw new ConfigError(`shot ${shot.id}: no_truncate locators must be CSS`);
      }
    }
    this.instances = new Map(((s.environment || {}).instances || []).map((i) => [i.id, (this.cfg.bases || {})[i.id] || i.base_url]));
    if (!this.instances.size) throw new ConfigError('scenario declares no environment.instances');
    for (const url of this.instances.values()) new URL(url);
  }

  origins() {
    return [...new Set([...this.instances.values()].map((u) => new URL(u).origin))];
  }

  base(id) {
    const key = id || this.instance || [...this.instances.keys()][0];
    const url = this.instances.get(key);
    if (!url) throw new Error(`unknown instance ${key}`);
    return url.replace(/\/$/, '');
  }

  // ------------------------------------------------------------ resolution

  /** Replace {{persona.*}} templates; {{secret.*}} only where allowSecrets. */
  async template(text, { allowSecrets = false } = {}) {
    let out = '';
    let last = 0;
    for (const m of text.matchAll(TEMPLATE_RE)) {
      out += text.slice(last, m.index);
      last = m.index + m[0].length;
      const [, ns, id, field] = m;
      if (ns === 'persona') {
        const p = this.people.get(id);
        if (!p || !PERSONA_FIELDS.has(field) || p[field] === undefined) throw new ConfigError(`template ${m[0]} does not resolve`);
        out += String(p[field]);
      } else if (ns === 'secret' && allowSecrets) {
        out += await this.secretValue(id);
      } else {
        throw new ConfigError(`template ${m[0]} is not allowed here`);
      }
    }
    return out + text.slice(last);
  }

  /** Synchronous persona-only template for locators. */
  templateSync(text) {
    return text.replace(TEMPLATE_RE, (all, ns, id, field) => {
      const p = ns === 'persona' && this.people.get(id);
      if (!p || !PERSONA_FIELDS.has(field) || p[field] === undefined) throw new ConfigError(`template ${all} does not resolve in a locator`);
      return String(p[field]);
    });
  }

  locator(raw, { check = false } = {}) {
    let text = this.templateSync(raw);
    const m = text.match(REF_RE);
    if (text.startsWith('@')) {
      if (!m || !(m[1] in this.sel)) throw new ConfigError(`selector reference ${raw.slice(0, 40)} is not in the selector map`);
      const value = this.sel[m[1]];
      if (value.includes('{arg}') !== (m[2] !== undefined)) throw new ConfigError(`selector ${m[1]}: argument mismatch`);
      text = 'css=' + value.split('{arg}').join(m[2] ?? '');
    }
    const loc = parseLocator(text);
    if (!check && this.cfg.injectFailure && this.cfg.injectFailure === this.current && loc.kind === 'css') {
      return { kind: 'css', value: WRONG_SELECTOR };
    }
    return loc;
  }

  css(raw) {
    const loc = this.locator(raw);
    if (loc.kind !== 'css') throw new Error(`a CSS locator is required here: ${raw.slice(0, 40)}`);
    return loc.value;
  }

  async secretValue(name) {
    if (this.secrets.has(name)) return this.secrets.get(name);
    let value;
    const file = this.cfg.secretsDir && join(this.cfg.secretsDir, name);
    if (file && existsSync(file)) value = (await readFile(file, 'utf8')).trim();
    else if (this.hooks.secret) value = await this.hooks.secret(name, this.ctx());
    if (!value) throw new Error(`secret ${name} is not available (no captured value, file or hook)`);
    this.registerSecret(value, name);
    return value;
  }

  registerSecret(value, name) {
    if (!value) return;
    if (value.length < 4) throw new Error('a one-time secret shorter than 4 characters cannot be gated');
    if (name) this.secrets.set(name, value);
    if (!this.oneTime.includes(value)) this.oneTime.push(value);
  }

  redact(text) {
    let t = String(text);
    for (const v of this.oneTime) {
      for (const form of [v, JSON.stringify(v).slice(1, -1), encodeURIComponent(v)]) t = t.split(form).join('[REDACTED]');
    }
    return t;
  }

  // ------------------------------------------------------------ waiting

  async waitFor(raw, { hidden = false, timeout } = {}) {
    const loc = this.locator(raw);
    const t = timeout ?? this.cfg.actionTimeout;
    if (loc.kind === 'css') return hidden ? this.d.waitHidden(loc.value, { timeout: t }) : this.d.waitVisible(loc.value, { timeout: t });
    if (loc.kind === 'url') {
      const want = await this.template(loc.value);
      return this.d.waitURL((u) => u.pathname.startsWith(want), { timeout: t });
    }
    return this.waitText(await this.template(loc.value), { absent: hidden, timeout: t });
  }

  async waitText(text, { absent = false, timeout }) {
    const end = Date.now() + (timeout ?? this.cfg.actionTimeout);
    for (;;) {
      const present = await this.d.evaluate(P.textPresent, text);
      if (present !== absent) return;
      if (Date.now() > end) throw new Error(`text ${absent ? 'still present' : 'not present'} after ${timeout ?? this.cfg.actionTimeout}ms`);
      await new Promise((r) => setTimeout(r, 200));
    }
  }

  /**
   * Wait until the main region's text and height stop changing. Polled from
   * Node with short evaluates: some engines limit how long one evaluate may run.
   */
  async settle() {
    let prev = null;
    let same = 0;
    for (let i = 0; i < 40; i++) {
      const cur = await this.d.evaluate(P.settleProbe);
      same = cur === prev && cur.startsWith('loaded') ? same + 1 : 0;
      if (same >= 2) return i;
      prev = cur;
      await new Promise((r) => setTimeout(r, 250));
    }
    return -1;
  }

  // ------------------------------------------------------------ personas

  statePath(persona) {
    return join(this.statesDir, `state-${persona}.json`);
  }

  /** Switch persona: save the current one's login state, restore or inject the next one's. */
  async usePersona(persona) {
    if (persona === this.persona) return;
    if (this.persona) {
      await this.d.exportState(this.statePath(this.persona));
      this.saved.add(this.persona);
    }
    let state = null;
    if (this.saved.has(persona)) state = this.statePath(persona);
    else if (this.cfg.stateFrom && existsSync(join(this.cfg.stateFrom, `state-${persona}.json`))) {
      state = join(this.cfg.stateFrom, `state-${persona}.json`);
      this.notes.injectedState = [...(this.notes.injectedState || []), persona];
    }
    await this.d.persona(persona, state);
    this.persona = persona;
  }

  // ------------------------------------------------------------ steps

  /** Run one unit of work with timing and failure handling. `always` units run after a failure too. */
  async unit(id, persona, fn, { always = false } = {}) {
    if (this.failed && !always) {
      this.steps.push({ id, persona, skipped: true });
      return;
    }
    const rec = { id, persona, ok: false };
    const t = Date.now();
    this.current = id;
    try {
      rec.checks = (await fn()) ?? undefined;
      rec.ok = true;
    } catch (e) {
      rec.error = this.redact(String(e && e.message ? e.message : e).split('\n')[0].slice(0, 300));
      if (!this.failed) {
        this.failed = rec;
        await this.collectEvidence(rec, join(this.cfg.out, 'failure', id));
        this.persona = null; // evidence collection may close the context; the next unit starts fresh
      }
    }
    rec.ms = Date.now() - t;
    this.current = null;
    this.steps.push(rec);
    console.log(`[step] ${id} ${rec.ok ? 'ok' : 'FAIL'} ${rec.ms}ms${rec.error ? ' :: ' + rec.error : ''}`);
  }

  async step(step) {
    const hooks = this.hooks;
    const ctx = this.ctx();
    await this.usePersona(step.persona);
    if (step.instance) this.instance = step.instance;
    if (hooks.before && hooks.before[step.id]) await hooks.before[step.id](ctx);
    const secrets = step.secrets || {};
    const secretCss = (secrets.to_run_denylist || []).map((l) => this.locator(l, { check: true }).value);
    if (secretCss.length) await this.d.evaluate(P.hideSecretPixels, secretCss);
    const checks = {};
    // A secret fill and the click or key press that follows it (usually the submit)
    // run outside traces and event buffers; redaction is the second line.
    const usesSecret = /\{\{\s*secret\./.test(JSON.stringify([step.value ?? null, step.request ?? null]));
    const sensitive = usesSecret || (this.armed && (step.action === 'click' || step.action === 'press'));
    this.armed = usesSecret && step.action === 'fill';
    const act = async () => {
      const value = step.value !== undefined ? await this.template(step.value, { allowSecrets: true }) : undefined;
      switch (step.action) {
        case 'goto':
          await this.d.goto(this.base(step.instance) + (await this.template(step.target)));
          break;
        case 'click':
          await this.d.click(this.css(step.target));
          break;
        case 'fill':
          await this.d.fill(this.css(step.target), value);
          break;
        case 'press':
          await this.d.press(this.css(step.target), value);
          break;
        case 'select':
          if (!(await this.d.evaluate(P.selectValue, { selector: this.css(step.target), value }))) throw new Error('select: value not applied');
          break;
        case 'wait':
          await this.waitFor(step.target);
          break;
        case 'request': {
          const r = step.request;
          const body = r.body === undefined ? undefined : JSON.parse(await this.template(JSON.stringify(r.body), { allowSecrets: true }));
          checks.status = await this.d.evaluate(P.fetchStatus, { method: r.method || 'GET', path: await this.template(r.path), body });
          break;
        }
        default:
          throw new Error(`unsupported action ${step.action}`);
      }
      await this.expect(step, checks);
      for (const [i, sel] of secretCss.entries()) {
        const v = await this.d.evaluate(P.takeSecret, { selector: sel, mask: MASK });
        if (!v) throw new Error(`one-time secret not found at secrets.to_run_denylist[${i}]`);
        this.registerSecret(v, i === 0 ? secrets.capture_as : undefined);
      }
    };
    if (sensitive || secretCss.length) await this.d.sensitive(act);
    else await act();
    if (hooks.after && hooks.after[step.id]) Object.assign(checks, (await hooks.after[step.id](ctx)) || {});
    if (step.shot) await this.shot(step.shot);
    return Object.keys(checks).length ? checks : undefined;
  }

  async expect(step, checks) {
    const e = step.expect || {};
    if (e.url !== undefined) {
      const want = await this.template(e.url);
      await this.d.waitURL((u) => u.pathname === want);
    }
    if (e.url_pattern !== undefined) {
      const re = new RegExp(await this.template(e.url_pattern));
      await this.d.waitURL((u) => re.test(u.pathname));
    }
    for (const l of e.visible || []) await this.waitFor(l);
    for (const l of e.hidden || []) await this.waitFor(l, { hidden: true });
    for (const t of e.text || []) await this.waitText(await this.template(t), {});
    if (e.status !== undefined && checks.status !== e.status) throw new Error(`status ${checks.status}, expected ${e.status}`);
    if (e.exit_code !== undefined || e.stdout_contains) throw new Error('terminal expectations on a browser step');
  }

  // ------------------------------------------------------------ captures

  async shot(id) {
    const shot = (this.s.shots || []).find((x) => x.id === id);
    for (const l of shot.wait_for) await this.waitFor(l);
    const vs = shot.view_state || {};
    if (vs.scroll === 'top') await this.d.evaluate(P.scrollTo, { mode: 'top' });
    else if (vs.scroll && vs.scroll.selector) await this.d.evaluate(P.scrollTo, { selector: this.css(vs.scroll.selector), top: vs.scroll.top || 0 });
    else if (vs.scroll && typeof vs.scroll.y === 'number') await this.d.evaluate(P.scrollTo, { y: vs.scroll.y });
    const cut = await this.d.evaluate(P.truncated, (shot.no_truncate || []).map((l) => this.css(l)));
    if (cut.length) throw new Error(`shot ${id}: ${cut.length} element group(s) truncated`);
    const base = this.s.determinism.color_scheme || 'light';
    const variants = shot.variants || [vs.theme || base];
    for (const v of variants) {
      const file = shot.variants ? shot.file.replace(/\.png$/, `.${v}.png`) : shot.file;
      if (v !== base) await this.d.colorScheme(v);
      try {
        await this.capture(file);
      } finally {
        if (v !== base) await this.d.colorScheme(base);
      }
    }
  }

  /**
   * Screenshot and visible text at the same moment, then the mask rectangles,
   * then metadata stripping (capture/SPEC.md). Nothing is written when the
   * page text contains a one-time secret.
   */
  async capture(file) {
    const dir = join(this.cfg.out, 'shots');
    await mkdir(dir, { recursive: true });
    const png = join(dir, file);
    const vp = this.s.determinism.viewport;
    const pointer = (this.hooks.capture && this.hooks.capture.pointer) || { x: 2, y: vp.height - 4 };
    await this.d.mouseMove(pointer.x, pointer.y); // a pointer left over a chart opens a tooltip
    await this.d.evaluate(P.freezeMotion);
    await this.settle();
    const text = await this.d.evaluate(P.pageText);
    if (this.oneTime.some((v) => text.includes(v))) throw new Error(`capture ${file}: page text contains a one-time secret; nothing written`);
    await this.d.screenshot(png);
    const masks = await this.d.evaluate(P.collectMasks, { selectors: this.mask.selectors || [], text: this.mask.text || [] });
    await writeFile(`${png}.txt`, text);
    await writeFile(png.replace(/\.png$/, '.masks.json'), JSON.stringify(masks));
    this.python([join(this.cfg.repo, 'capture', 'strip_png.py'), png]);
    this.captures.push({ file: relative(this.cfg.out, png), masks: masks.rects.length });
  }

  python(argv, opts = {}) {
    return execFileSync(this.cfg.python || 'python3', argv, { stdio: ['ignore', 'pipe', 'pipe'], ...opts }).toString();
  }

  // ------------------------------------------------------------ evidence

  async writeRunDenylist() {
    const path = join(this.statesDir, 'run-denylist.txt');
    await writePrivate(path, ['# one-time values seen during this run; delete at wrap-up', ...this.oneTime].join('\n') + '\n');
    return path;
  }

  /**
   * Failure evidence is kept only when capture/evidence.py redacts it and every
   * gate passes. Values still on the page (password inputs, an unmasked
   * one-time secret) are registered and scrubbed first.
   */
  async collectEvidence(rec, dir) {
    try {
      const found = await this.d.evaluate(P.scrubDom, { mask: MASK, selectors: this.secretSelectors }).catch(() => null);
      // Masked in the DOM either way; values under 4 characters cannot be told apart from page text.
      for (const v of found || []) if (v.length >= 4) this.registerSecret(v);
      await this.registerSessionCookies();
      await this.d.failureEvidence(dir, { scrubbed: found !== null });
      const argv = [join(this.cfg.repo, 'capture', 'evidence.py'), relative(this.cfg.out, dir), '--roster', this.cfg.rosterPath];
      for (const d of this.cfg.denylists) argv.push('--denylist', d);
      for (const p of this.cfg.policies) argv.push('--policy', p);
      if (this.oneTime.length) argv.push('--secrets', await this.writeRunDenylist());
      for (const g of this.d.evidenceDrop || []) argv.push('--drop', g);
      argv.push('--ocr', this.cfg.evidenceOcr || 'require');
      const res = spawnSync(this.cfg.python || 'python3', argv, { cwd: this.cfg.out, encoding: 'utf8' });
      rec.evidenceGate = (res.stdout || '').split('\n').filter(Boolean);
      rec.evidenceKept = res.status === 0;
    } catch (err) {
      rec.evidenceKept = false;
      rec.evidenceError = this.redact(String(err && err.message ? err.message : err).slice(0, 200));
    }
    if (rec.evidenceKept) {
      rec.evidence = relative(this.cfg.out, dir);
    } else {
      await rm(dir, { recursive: true, force: true });
      rec.evidence = 'discarded: not proven free of secrets';
    }
  }

  /**
   * Session cookies travel in request headers that traces and logs record.
   * Every cookie value of 16+ characters in the current and saved login
   * states becomes a run secret, so evidence is redacted and gated against it.
   */
  async registerSessionCookies() {
    if (this.persona) await this.d.exportState(this.statePath(this.persona)).catch(() => {});
    const files = (await readdir(this.statesDir).catch(() => [])).filter((f) => /^state-.*\.json$/.test(f));
    for (const f of files) {
      try {
        const state = JSON.parse(await readFile(join(this.statesDir, f), 'utf8'));
        for (const c of state.cookies || []) if (typeof c.value === 'string' && c.value.length >= 16) this.registerSecret(c.value);
      } catch {
        // an unreadable state file adds nothing; the gates still run
      }
    }
  }

  // ------------------------------------------------------------ hooks

  ctx() {
    const run = this;
    return {
      config: this.cfg.hookConfig || {},
      out: this.cfg.out,
      people: [...this.people.values()],
      person: (id) => run.people.get(id),
      base: (id) => run.base(id),
      selector: (ref) => run.css(ref),
      persona: (id) => run.usePersona(id),
      goto: (path, instance) => run.d.goto(run.base(instance) + path),
      click: (ref, o) => run.d.click(run.css(ref), o),
      fill: (ref, value, o) => run.d.fill(run.css(ref), value, o),
      waitFor: (ref, o) => run.waitFor(ref, o),
      evaluate: (fn, arg) => run.d.evaluate(fn, arg),
      url: async () => new URL(await run.d.url()),
      settle: () => run.settle(),
      navLog: () => run.d.evaluate(P.readNavLog),
      sensitive: (fn) => run.d.sensitive(fn),
      secret: (value, name) => run.registerSecret(value, name),
      note: (key, value) => { run.notes[key] = value; },
      exec: (file, args = [], { input } = {}) => execFileSync(file, args, { input, stdio: ['pipe', 'pipe', 'pipe'] }).toString(),
      assert(id, cond, message) {
        if (!(run.s.assertions || []).some((a) => a.id === id)) throw new Error(`assert: ${id} is not declared in the scenario's assertions`);
        const prev = run.asserted.get(id);
        run.asserted.set(id, { ok: !!cond && (!prev || prev.ok), message: run.redact(message || '') });
        if (!cond) throw new Error(`assert ${id}: ${message || 'failed'}`);
      },
    };
  }

  // ------------------------------------------------------------ main

  async execute(createBackend) {
    this.d = await createBackend(this.cfg, { determinism: this.s.determinism, origins: this.origins(), engine: this.hooks.engine || {} });
    const gates = new Map((this.s.human_gates || []).map((g) => [g.before_step, g]));
    let stoppedAt = null;
    try {
      for (const item of this.s.setup || []) {
        await this.unit(`setup:${item.id}`, null, async () => {
          const fn = this.hooks.setup && this.hooks.setup[item.id];
          if (!fn) throw new Error(`setup ${item.id} has no hook (setup runs through the adapter's hooks module)`);
          return fn(this.ctx());
        });
      }
      for (const step of this.s.steps) {
        const gate = gates.get(step.id);
        if (gate && !this.failed && !(this.cfg.approvedGates || []).includes(gate.id)) {
          stoppedAt = gate.id;
          console.log(`[run] stopped at human gate ${gate.id} before ${step.id}`);
          break;
        }
        await this.unit(step.id, step.persona, () => this.step(step));
      }
    } finally {
      if (this.hooks.teardown) await this.unit('teardown', null, () => this.hooks.teardown(this.ctx()), { always: true });
      if (this.persona) {
        await this.d.exportState(this.statePath(this.persona)).catch(() => {});
      }
      await this.d.close().catch(() => {});
    }
    return this.finish(stoppedAt);
  }

  async finish(stoppedAt) {
    const declared = (this.s.assertions || []).map((a) => a.id);
    const unchecked = declared.filter((id) => !this.asserted.has(id));
    const assertions = Object.fromEntries([...this.asserted.entries()]);
    const passed = !this.failed && !stoppedAt && unchecked.length === 0
      && this.steps.every((s) => s.ok) && Object.values(assertions).every((a) => a.ok);
    const result = {
      engine: this.d.engine,
      engineVersion: this.d.version,
      scenario: this.s.id,
      startedAt: new Date(this.t0).toISOString(),
      totalMs: Date.now() - this.t0,
      passed,
      stoppedAtHumanGate: stoppedAt,
      failedStep: this.failed ? this.failed.id : null,
      injectFailure: this.cfg.injectFailure || null,
      steps: this.steps,
      captures: this.captures,
      assertions,
      uncheckedAssertions: unchecked,
      notes: this.notes,
    };
    await mkdir(dirname(join(this.cfg.out, 'results.json')), { recursive: true });
    await writeFile(join(this.cfg.out, 'results.json'), JSON.stringify(result, null, 2));
    console.log(`[run] ${result.engine} ${result.scenario} passed=${passed} total=${result.totalMs}ms`);
    return result;
  }
}
