// ego-browser backend. Runs inside `ego-browser nodejs`, whose runtime has the
// global taskSpace() and no process.argv or process.env (runners/run.sh
// prepends globalThis.JQA_CONFIG). A task space shares the user's browser
// profile, so a persona switch clears only the scenario origins' cookies and
// localStorage. Cookies ignore ports: give the instance under test its own
// host name (for example a *.localhost alias of the loopback address) so its
// cookies never overwrite those of another local server on the same address.
// Failed requests come from the CDP Network events the runtime buffers (page.events()); they are reduced
// to method, path, query keys, status and timing as they are read, and the raw events are dropped.
import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { NAV_LOG_SCRIPT, pageText, clearNavLog } from '../lib/page.mjs';
import { writePrivate } from '../lib/files.mjs';
import { FEATURES } from '../lib/emulation.mjs';
import { NetworkCollector, notCollected, pushLog } from '../lib/network.mjs';

export async function createBackend(cfg, { determinism, origins, engine = {}, instanceByOrigin }) {
  if (typeof taskSpace !== 'function') throw new Error('the ego engine runs only inside `ego-browser nodejs`');
  const task = await taskSpace(cfg.egoSpace);
  const page = task.page('p1');
  const vp = determinism.viewport;
  const cookiePaths = engine.cookiePaths || ['/'];
  const stateLoadPath = engine.stateLoadPath || '/';
  const hosts = new Set(origins.map((o) => new URL(o).hostname));
  const cookieUrls = origins.flatMap((o) => cookiePaths.map((p) => o + p));

  // CDP emulation lives as long as this process's session. User agent, locale and timezone are set
  // once per run; viewport, touch and media features are set again whenever a persona's differ.
  const ua = await page.evaluate(() => navigator.userAgent);
  await page.cdp('Emulation.setUserAgentOverride', { userAgent: ua, acceptLanguage: determinism.locale });
  await page.cdp('Emulation.setLocaleOverride', { locale: determinism.locale });
  await page.cdp('Emulation.setTimezoneOverride', { timezoneId: determinism.timezone });
  const media = { scheme: determinism.color_scheme || 'light', reducedMotion: null }; // null: never emulated, leave the browser's own setting
  const applyMedia = () => {
    const features = [{ name: 'prefers-color-scheme', value: media.scheme }];
    if (media.reducedMotion !== null) features.push({ name: 'prefers-reduced-motion', value: media.reducedMotion ? 'reduce' : 'no-preference' });
    return page.cdp('Emulation.setEmulatedMedia', { media: 'screen', features });
  };
  const scheme = (s) => { media.scheme = s; return applyMedia(); };
  let shape = null; // viewport and touch emulation currently applied
  async function applyShape(emu) {
    const key = JSON.stringify([emu.viewport, emu.mobile]);
    if (key === shape) return;
    const mobileChanged = shape === null ? emu.mobile : JSON.parse(shape)[1] !== emu.mobile;
    await page.cdp('Emulation.setDeviceMetricsOverride', { width: emu.viewport.width, height: emu.viewport.height, deviceScaleFactor: emu.viewport.device_scale_factor, mobile: emu.mobile });
    if (mobileChanged) await page.cdp('Emulation.setTouchEmulationEnabled', emu.mobile ? { enabled: true, maxTouchPoints: 5 } : { enabled: false });
    shape = key;
  }
  await applyShape({ viewport: vp, mobile: false });
  await applyMedia();
  await page.cdp('Page.addScriptToEvaluateOnNewDocument', { source: NAV_LOG_SCRIPT });

  // Request feed. Reduced as it is read: nothing with a body, a header or a query value is kept.
  const net = new NetworkCollector({ origins: instanceByOrigin });
  const eventLog = []; // method, path and status of every event, for failure evidence
  let netBroken = null;
  let navigated = false;
  const summary = (e) => {
    const p = e.params || {};
    const url = (p.request && p.request.url) || (p.response && p.response.url) || p.url || (p.frame && p.frame.url);
    let path = null;
    try { path = url ? new URL(url).pathname : null; } catch { path = null; }
    return { method: e.method, path, status: p.response ? p.response.status : undefined, error: p.errorText };
  };
  async function pump() {
    if (netBroken) return;
    try {
      const events = await page.events();
      for (const e of events) pushLog(eventLog, summary(e));
      net.ingestCdp(events);
    } catch (err) {
      netBroken = notCollected(`page.events() failed (${(err && err.name) || 'Error'})`);
    }
  }

  async function onOrigin() {
    const url = await page.url();
    return origins.some((o) => url.startsWith(o + '/'));
  }

  async function clearOrigins() {
    if (await onOrigin()) await page.evaluate(clearNavLog);
    const { cookies } = await page.cdp('Network.getCookies', { urls: cookieUrls });
    // Host-only cookies of the scenario origins; a parent-domain cookie would reach other servers.
    for (const c of cookies.filter((x) => hosts.has(x.domain))) {
      await page.cdp('Network.deleteCookies', { name: c.name, domain: c.domain, path: c.path });
    }
    for (const origin of origins) await page.cdp('Storage.clearDataForOrigin', { origin, storageTypes: 'local_storage' });
  }

  async function importState(path) {
    const state = JSON.parse(await readFile(path, 'utf8'));
    for (const c of state.cookies.filter((x) => hosts.has(x.domain))) {
      const p = { name: c.name, value: c.value, domain: c.domain, path: c.path, httpOnly: c.httpOnly, secure: c.secure, sameSite: c.sameSite };
      if (c.expires > 0) p.expires = c.expires;
      await page.cdp('Network.setCookie', p);
    }
    for (const o of state.origins.filter((x) => origins.includes(x.origin))) {
      // localStorage needs a document on the origin; the adapter names a path that is not an app route.
      navigated = true;
      await page.goto(o.origin + stateLoadPath, { waitUntil: 'load' });
      await page.evaluate((items) => {
        for (const { name, value } of items) localStorage.setItem(name, value);
        sessionStorage.removeItem('jqa:nav');
      }, o.localStorage);
    }
  }

  const d = {
    engine: 'ego',
    version: cfg.egoVersion || 'ego-browser',

    emulates: new Set(FEATURES),

    async persona(_name, statePath, emu = { viewport: vp, mobile: false, reducedMotion: false }) {
      await applyShape(emu);
      if (emu.reducedMotion !== (media.reducedMotion === true)) {
        media.reducedMotion = emu.reducedMotion;
        await applyMedia();
      }
      await clearOrigins();
      if (statePath) await importState(statePath);
    },

    goto: (url) => { navigated = true; return page.goto(url, { waitUntil: 'load', timeout: cfg.navTimeout }); },
    click: (s, o = {}) => page.click(s, { timeout: o.timeout ?? cfg.actionTimeout }),
    fill: (s, v, o = {}) => page.fill(s, v, { timeout: o.timeout ?? cfg.actionTimeout }),
    async press(s, key, o = {}) {
      if (typeof page.press !== 'function') throw new Error('press is not available on this engine');
      return page.press(s, key, { timeout: o.timeout ?? cfg.actionTimeout });
    },
    waitVisible: (s, o = {}) => page.waitForSelector(s, { state: 'visible', timeout: o.timeout ?? cfg.actionTimeout }),
    waitHidden: (s, o = {}) => page.waitForSelector(s, { state: 'hidden', timeout: o.timeout ?? cfg.actionTimeout }),
    waitURL: (pred, o = {}) => page.waitForURL((u) => pred(u), { timeout: o.timeout ?? cfg.navTimeout }),
    evaluate: (fn, arg) => (arg === undefined ? page.evaluate(fn) : page.evaluate(fn, arg)),
    url: () => page.url(),
    mouseMove: (x, y) => page.mouse.move(x, y),
    colorScheme: scheme,

    // page.screenshot() returns CSS-pixel size; the raw CDP call honours the device scale factor.
    async screenshot(path) {
      const { data } = await page.cdp('Page.captureScreenshot', { format: 'png' });
      await writeFile(path, Buffer.from(data, 'base64'));
    },

    /** Run fn, then reduce and drop the CDP events it buffered (they carry request bodies and cookies). */
    async sensitive(fn) {
      try {
        return await fn();
      } finally {
        await pump();
      }
    },

    /** Failed requests since the last drain, or an explicit "not collected" when the feed is unusable. */
    async network({ drain = false } = {}) {
      await pump();
      if (netBroken) return netBroken;
      if (navigated && net.eventsSeen === 0) return notCollected('no network events arrived after a navigation');
      return net.snapshot(drain);
    },

    /** Export the login in Playwright storageState format: cookies via CDP, localStorage via the page. */
    async exportState(path) {
      const { cookies } = await page.cdp('Network.getCookies', { urls: cookieUrls });
      const out = [];
      for (const origin of origins) {
        if ((await page.url()).startsWith(origin + '/')) {
          const localStorage = await page.evaluate(() => Object.entries(window.localStorage).map(([name, value]) => ({ name, value })));
          out.push({ origin, localStorage });
        }
      }
      const state = {
        cookies: cookies.filter((c) => hosts.has(c.domain)).map((c) => ({
          name: c.name, value: c.value, domain: c.domain, path: c.path,
          expires: c.session ? -1 : c.expires, httpOnly: c.httpOnly, secure: c.secure, sameSite: c.sameSite || 'Lax',
        })),
        origins: out,
      };
      await writePrivate(path, JSON.stringify(state));
    },

    /** Raw evidence; the runner redacts and gates it before anything is kept. */
    async failureEvidence(dir) {
      await mkdir(dir, { recursive: true });
      const files = [];
      const shot = join(dir, '99-failure.png');
      await d.screenshot(shot).then(() => files.push(shot), () => {});
      await page.evaluate(pageText).then((t) => writeFile(`${shot}.txt`, t)).then(() => files.push(`${shot}.txt`), () => {});
      await page.snapshot().then((s) => writeFile(join(dir, 'snapshot.txt'), String(s))).then(() => files.push(join(dir, 'snapshot.txt')), () => {});
      await page.info().then((i) => writeFile(join(dir, 'info.json'), JSON.stringify(i, null, 2))).then(() => files.push(join(dir, 'info.json')), () => {});
      // Raw CDP events hold request bodies and Set-Cookie headers: keep method, path, status and error text only.
      await pump();
      await writeFile(join(dir, 'events.json'), JSON.stringify(eventLog)).then(() => files.push(join(dir, 'events.json')), () => {});
      return files;
    },

    async close() {},
  };
  return d;
}
