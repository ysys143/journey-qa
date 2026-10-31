// A browser backend that drives no browser, for controls on the interpreter itself
// (emulation handed to the backend, expectations, record files, evidence gating).
// It remembers every call. `script` maps a CSS selector to the network events its click causes.
import { FEATURES } from '../lib/emulation.mjs';
import { NetworkCollector, notCollected } from '../lib/network.mjs';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

export function stubBackend({ script = {}, emulates = new Set(FEATURES), collects = true, instanceByOrigin = new Map() } = {}) {
  const calls = [];
  const create = async () => {
    let net = new NetworkCollector({ origins: instanceByOrigin });
    let nextId = 0;
    return {
      engine: 'stub',
      version: '0',
      calls,
      emulates,
      evidenceDrop: [],
      async persona(name, state, emu) { calls.push({ persona: name, state, emu }); net = new NetworkCollector({ origins: instanceByOrigin }); },
      async goto(url) { calls.push({ goto: url }); },
      async click(selector) {
        calls.push({ click: selector });
        for (const ev of script[selector] || []) {
          const id = `r${nextId++}`;
          net.begin(id, { method: ev.method || 'GET', url: ev.url, type: ev.type || 'fetch', t: 1000 });
          if (ev.fail) net.fail(id, { error: ev.fail, t: 1000 + (ev.ms || 5) });
          else { net.response(id, { status: ev.status, t: 1000 + (ev.ms || 5) }); net.end(id); }
        }
      },
      async fill() {},
      async press() {},
      async waitVisible() {},
      async waitHidden() {},
      async waitURL() {},
      async url() { return 'http://stub.test/'; },
      async mouseMove() {},
      async colorScheme() {},
      async screenshot() {},
      async evaluate(fn) {
        if (fn.name === 'scrubDom') return [];
        if (fn.name === 'textPresent') return true;
        return undefined;
      },
      async sensitive(fn) { return fn(); },
      async exportState(path) { await mkdir(join(path, '..'), { recursive: true }); await writeFile(path, JSON.stringify({ cookies: [], origins: [] })); },
      async failureEvidence(dir) {
        await mkdir(dir, { recursive: true });
        await writeFile(join(dir, 'page.txt'), 'stub page text');
        return [join(dir, 'page.txt')];
      },
      network: collects ? async ({ drain = false } = {}) => net.snapshot(drain) : undefined,
      networkReason: collects ? null : notCollected('the stub backend has no request feed'),
      async close() {},
    };
  };
  return { create, calls };
}
