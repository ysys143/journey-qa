// Playwright backend. One isolated browser context per persona; storageState
// saves and injects a login. A trace is recorded for every context and kept
// only when a step fails; parts marked sensitive() are left out of it. No
// video is recorded: no gate can scan video.
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { NAV_LOG_SCRIPT, pageText } from '../lib/page.mjs';
import { writePrivate } from '../lib/files.mjs';

export async function createBackend(cfg, { determinism }) {
  if (!cfg.playwrightModule) throw new Error('config: playwrightModule is required for the playwright engine');
  const pw = await import(cfg.playwrightModule);
  const chromium = pw.chromium || (pw.default && pw.default.chromium);
  const browser = await chromium.launch({ headless: !cfg.headed, channel: cfg.pwChannel || undefined });
  const vp = determinism.viewport;
  let ctx = null;
  let page = null;
  let consoleLines = [];
  let chunk = false; // a trace chunk is recording

  async function closeContext(traceDir) {
    if (!ctx) return [];
    const files = [];
    if (traceDir && chunk) {
      const trace = join(traceDir, 'trace.zip');
      await ctx.tracing.stopChunk({ path: trace });
      files.push(trace);
    } else if (chunk) {
      await ctx.tracing.stopChunk();
    }
    chunk = false;
    await ctx.tracing.stop();
    await ctx.close();
    ctx = null;
    page = null;
    return files;
  }

  const d = {
    engine: 'playwright',
    version: `playwright ${cfg.playwrightVersion || '?'} / ${browser.version()}`,
    // trace.stacks records the runner's own source paths (the operator's home directory), not the page.
    evidenceDrop: ['*.stacks'],

    /** A fresh, isolated context for a persona, optionally with saved state. */
    async persona(name, statePath) {
      await closeContext(null);
      ctx = await browser.newContext({
        viewport: { width: vp.width, height: vp.height },
        deviceScaleFactor: vp.device_scale_factor,
        locale: determinism.locale,
        timezoneId: determinism.timezone,
        colorScheme: determinism.color_scheme || 'light',
        storageState: statePath || undefined,
      });
      await ctx.addInitScript(NAV_LOG_SCRIPT);
      await ctx.tracing.start({ screenshots: false, snapshots: true, title: name });
      await ctx.tracing.startChunk();
      chunk = true;
      page = await ctx.newPage();
      consoleLines = [];
      page.on('console', (m) => consoleLines.push(`${m.type()} ${m.text()}`));
      page.on('pageerror', (e) => consoleLines.push(`pageerror ${e.message}`));
    },

    goto: (url) => page.goto(url, { waitUntil: 'load', timeout: cfg.navTimeout }),
    click: (s, o = {}) => page.click(s, { timeout: o.timeout ?? cfg.actionTimeout }),
    fill: (s, v, o = {}) => page.fill(s, v, { timeout: o.timeout ?? cfg.actionTimeout }),
    press: (s, key, o = {}) => page.press(s, key, { timeout: o.timeout ?? cfg.actionTimeout }),
    waitVisible: (s, o = {}) => page.waitForSelector(s, { state: 'visible', timeout: o.timeout ?? cfg.actionTimeout }),
    waitHidden: (s, o = {}) => page.waitForSelector(s, { state: 'hidden', timeout: o.timeout ?? cfg.actionTimeout }),
    waitURL: (pred, o = {}) => page.waitForURL((u) => pred(u), { timeout: o.timeout ?? cfg.navTimeout }),
    evaluate: (fn, arg) => page.evaluate(fn, arg),
    url: async () => page.url(),
    screenshot: (path) => page.screenshot({ path }),
    mouseMove: (x, y) => page.mouse.move(x, y),
    colorScheme: (scheme) => page.emulateMedia({ colorScheme: scheme }),

    /** Run fn outside the trace: login bodies and one-time values never reach trace.zip. */
    async sensitive(fn) {
      if (chunk) await ctx.tracing.stopChunk();
      chunk = false;
      try {
        return await fn();
      } finally {
        await ctx.tracing.startChunk();
        chunk = true;
      }
    },

    async exportState(path) {
      await writePrivate(path, JSON.stringify(await ctx.storageState()));
    },

    /** Raw evidence; the runner redacts and gates it before anything is kept. */
    async failureEvidence(dir, { scrubbed }) {
      await mkdir(dir, { recursive: true });
      const files = [];
      const shot = join(dir, '99-failure.png');
      await page.screenshot({ path: shot }).then(() => files.push(shot), () => {});
      await page.evaluate(pageText).then((t) => writeFile(`${shot}.txt`, t)).then(() => files.push(`${shot}.txt`), () => {});
      if (scrubbed) await page.content().then((h) => writeFile(join(dir, 'page.html'), h)).then(() => files.push(join(dir, 'page.html')), () => {});
      await writeFile(join(dir, 'console.log'), consoleLines.join('\n'));
      files.push(join(dir, 'console.log'));
      await writeFile(join(dir, 'url.txt'), page.url());
      files.push(join(dir, 'url.txt'));
      files.push(...(await closeContext(dir)));
      return files;
    },

    async close() {
      await closeContext(null);
      await browser.close();
    },
  };
  return d;
}
