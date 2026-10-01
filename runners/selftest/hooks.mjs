// Hooks for the runner smoke test (runners/README.md, "Hooks"). An adapter's
// hooks module has the same shape.
import { randomBytes } from 'node:crypto';

function headerName() {
  const b = document.querySelector('header button');
  return b ? b.textContent.trim() : null;
}

export default {
  // Values for {{secret.<name>}} that no secrets file provides. The demo app accepts any 8+ characters.
  async secret(name) {
    if (name === 'demo-password') return randomBytes(12).toString('base64url');
    return undefined;
  },
  after: {
    async 'ivy-login-submit'(ctx) {
      const name = await ctx.evaluate(headerName);
      ctx.assert('greeting', name === ctx.person('ivy').name, 'header shows the persona name');
      return { name };
    },
    async 'ivy-again'(ctx) {
      const nav = await ctx.navLog();
      ctx.assert('state-restored', !nav.some((p) => p.startsWith('/login')), 'sign-in page not shown after switching back');
      return { nav };
    },
    async 'marcus-home'(ctx) {
      const nav = await ctx.navLog();
      ctx.assert('no-login-with-state', !nav.some((p) => p.startsWith('/login')), 'sign-in page not shown with injected state');
      return { nav };
    },
  },
};
