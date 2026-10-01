// Hooks for the reconnect controls in orchestrate.py: restart the fake wrapper's connection.
import { rm } from 'node:fs/promises';
import { join } from 'node:path';

export default {
  async reconnect(ctx, step) {
    const t = ctx.config.reconnect_test || {};
    if (t.fail) throw new Error(`cannot restart the session before ${step.id}`);
    await rm(join(t.state_dir, 'session'), { force: true });
  },
};
