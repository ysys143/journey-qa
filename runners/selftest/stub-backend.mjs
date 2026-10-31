// A browser backend that drives no browser, for controls on the interpreter itself
// (emulation handed to the backend, expectations, evidence gating). It remembers every call.
import { FEATURES } from '../lib/emulation.mjs';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

export function stubBackend({ emulates = new Set(FEATURES) } = {}) {
  const calls = [];
  const create = async () => ({
    engine: 'stub',
    version: '0',
    calls,
    emulates,
    evidenceDrop: [],
    async persona(name, state, emu) { calls.push({ persona: name, state, emu }); },
    async goto(url) { calls.push({ goto: url }); },
    async click(selector) { calls.push({ click: selector }); },
    async fill() {},
    async press() {},
    async waitVisible() {},
    async waitHidden() {},
    async waitURL() {},
    async url() { return 'http://stub.test/'; },
    async mouseMove(x, y) { calls.push({ mouse: [x, y] }); },
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
    async close() {},
  });
  return { create, calls };
}
