// Hooks for the runner smoke test (runners/README.md, "Hooks"). An adapter's
// hooks module has the same shape.
import { randomBytes } from 'node:crypto';

function headerName() {
  const b = document.querySelector('header button');
  return b ? b.textContent.trim() : null;
}

/** What the page itself reports about the emulation in effect. */
function viewportFacts() {
  return {
    width: innerWidth,
    screen: screen.width, // a mobile emulation lays a page without a viewport meta tag out at 980 px; the screen keeps the emulated width
    ratio: devicePixelRatio,
    touch: navigator.maxTouchPoints > 0,
    reduced: matchMedia('(prefers-reduced-motion: reduce)').matches,
  };
}

export default {
  // Values for {{secret.<name>}} that no secrets file provides. The demo app accepts any 8+ characters.
  async secret(name) {
    if (name === 'demo-password') return randomBytes(12).toString('base64url');
    return undefined;
  },
  after: {
    async 'ivy-sign-in'(ctx) {
      const f = await ctx.evaluate(viewportFacts);
      ctx.assert('desktop-unchanged', f.width === 800 && f.ratio === 1 && !f.touch && !f.reduced, 'pinned viewport, no touch, no reduced motion');
      return f;
    },
    async 'tomas-sign-in'(ctx) {
      const f = await ctx.evaluate(viewportFacts);
      ctx.assert('tablet-from-roster', f.screen === 820 && f.ratio === 2 && f.touch && f.reduced, 'tablet screen, touch, reduced motion');
      return f;
    },
    async 'priya-sign-in'(ctx) {
      const f = await ctx.evaluate(viewportFacts);
      // the roster says mobile at zoom 2; the scenario overrides the viewport and turns mobile off
      ctx.assert('zoom-from-roster', f.width === 300 && f.ratio === 2 && !f.touch && !f.reduced, 'explicit viewport 600x800 at the roster zoom of 2, no touch');
      return f;
    },
    async 'ivy-sign-in-again'(ctx) {
      const f = await ctx.evaluate(viewportFacts);
      ctx.assert('desktop-unchanged', f.width === 800 && f.ratio === 1 && !f.touch && !f.reduced, 'pinned viewport again after the emulated personas');
      return f;
    },
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
