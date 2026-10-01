// Entry point for one scenario run on one engine (runners/README.md).
//   playwright: node run-engine.mjs <config.json>
//   ego:        run.sh pipes `globalThis.JQA_CONFIG = {...}; await import(...)`
//               into `ego-browser nodejs` (its runtime has no argv or env).
// Exit (also written to results.json): 0 passed, 1 failed, 2 configuration
// error before the browser started, 3 stopped at a human gate.
import { readFile } from 'node:fs/promises';
import { Run, ConfigError } from './lib/run.mjs';

const cfg = globalThis.JQA_CONFIG ?? JSON.parse(await readFile(process.argv[2], 'utf8'));
let code = 1;
try {
  const run = await Run.load(cfg);
  const backend = cfg.engine === 'playwright' ? './playwright/backend.mjs' : cfg.engine === 'ego' ? './ego/backend.mjs' : null;
  if (!backend) throw new ConfigError(`unknown engine ${cfg.engine}`);
  const { createBackend } = await import(backend);
  const result = await run.execute(createBackend);
  code = result.passed ? 0 : result.stoppedAtHumanGate ? 3 : 1;
} catch (e) {
  code = e instanceof ConfigError ? 2 : 1;
  console.log(`[run] ${e instanceof ConfigError ? 'configuration error' : 'runner error'}: ${String(e && e.message ? e.message : e).split('\n')[0].slice(0, 300)}`);
}
console.log(`[run] exit ${code}`);
if (typeof process !== 'undefined' && process) process.exitCode = code;
