// example-app run-mode hooks (runners/README.md, "Hooks") for team-tour,
// team-tour-injected and onboarding. Machine-specific values come from the
// hook config file (README.md, "Run mode"), never from this repository:
//   psql_command          argv that runs psql on instance B's database, SQL on stdin
//                         (unaligned, tuples only, "|" separator, ON_ERROR_STOP)
//   reanchor_command      argv of the generator's re-anchor script
//   health_url            instance B's /api/health as reachable from Node
//   freshness_max_seconds the generator's tail gap after re-anchoring plus a margin
import { randomBytes } from 'node:crypto';
import { mkdirSync, readFileSync, rmdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROSTER_SIZE = 5;
const QUINN_MATCH = "app_user_id = 'quinnsampleton' OR email = 'quinn.sampleton@example.com'";
const LOCK = join(tmpdir(), 'jqa-example-app-onboarding.lock');
let lockTaken = false; // this run holds the onboarding lock and created nothing it should not remove

function need(ctx, key) {
  const v = ctx.config[key];
  if (v === undefined || v === null || v === '') throw new Error(`hook config is missing ${key}`);
  return v;
}

function psql(ctx, sql) {
  const [file, ...args] = need(ctx, 'psql_command');
  return ctx.exec(file, args, { input: sql }).trim();
}

// ------------------------------------------------------------ in-page checks

/** Overview trend card: heading, "No data", legend names, coverage note. */
function overviewState(names) {
  const main = document.querySelector('main');
  const heading = [...main.querySelectorAll('h1,h2,h3,h4')].find((h) => h.textContent.startsWith('Usage Trend'));
  let card = heading;
  while (card && card !== main && !card.querySelector('ul li')) card = card.parentElement;
  const legend = card ? [...card.querySelectorAll('ul li')].map((li) => li.textContent.trim()) : [];
  const note = card ? [...card.querySelectorAll('p')].map((p) => p.textContent).find((t) => /plan usage/i.test(t)) : null;
  return {
    heading: heading ? heading.textContent.trim() : null,
    rosterNamesInLegend: names.filter((n) => legend.includes(n)).length,
    coverageNoteHasPercent: note != null && note.includes('%'),
  };
}

/** Scroll <main> so the Plan usage card's top edge sits at `top` CSS px. */
function scrollUsageCard(top) {
  const main = document.querySelector('main');
  const legend = document.querySelector('section[aria-label="Plan usage legend"]');
  if (!legend) return false;
  let card = legend;
  while (card.parentElement && card.parentElement !== main && !card.className.includes('rounded')) card = card.parentElement;
  main.scrollTop += card.getBoundingClientRect().top - top;
  return true;
}

function usageAccountLines() {
  const section = document.querySelector('section[aria-label="Plan usage legend"]');
  const groups = section ? [...section.querySelectorAll('[role=group]')].filter((g) => g.getAttribute('aria-label') !== 'Weighted') : [];
  return groups.reduce((n, g) => n + g.querySelectorAll('li').length, 0);
}

/** Session rows, rows carrying an email, event blocks, date formats. */
function sessionsState(email) {
  const main = document.querySelector('main');
  const idOf = (b) => (b.querySelector('span.font-mono') || {}).textContent;
  const rows = [...main.querySelectorAll('button[aria-pressed]')].filter((b) => /^[0-9a-f]{8}$/.test(idOf(b) || ''));
  const text = main.innerText;
  return {
    rows: rows.length,
    rowsWithEmail: email ? rows.filter((r) => r.textContent.includes(email)).length : null,
    eventBlocks: main.querySelectorAll('div.border.rounded > button span.font-mono.flex-shrink-0').length,
    englishDates: /\b\d{2}\/\d{2} \d{2}:\d{2} (AM|PM)\b/.test(text),
  };
}

function addUserRole() {
  const c = document.querySelector('[role=dialog] [role=combobox]');
  return c ? c.textContent.trim() : null;
}

function tempPasswordMasked() {
  const el = document.querySelector('[role=dialog] .select-all');
  return !!el && el.dataset.jqaMasked === '1';
}

/** Sidebar while the password is temporary: every item but Settings is a disabled span. */
function sidebarLock() {
  const items = [...document.querySelectorAll('aside nav a[href], aside nav [aria-disabled]')];
  const locked = items.filter((a) => a.getAttribute('aria-disabled') === 'true').length;
  const open = items.filter((a) => a.getAttribute('aria-disabled') !== 'true' && !/^\/settings\/?$/.test(a.getAttribute('href') || '')).length;
  return { locked, open };
}

function usersTable(userId) {
  const main = document.querySelector('main');
  const m = main.innerText.match(/\b(\d+) users\b/);
  return { count: m ? Number(m[1]) : null, listed: main.innerText.includes(userId) };
}

// ------------------------------------------------------------ shared checks

async function overview(ctx) {
  await ctx.settle();
  const names = ctx.people.filter((p) => p.id !== 'quinn').map((p) => p.name);
  const s = await ctx.evaluate(overviewState, names);
  ctx.assert('overview-legend', s.heading === 'Usage Trend — Per Minute (3h)' && s.rosterNamesInLegend === ROSTER_SIZE && s.coverageNoteHasPercent,
    `heading ${s.heading}, ${s.rosterNamesInLegend} roster names, note with %: ${s.coverageNoteHasPercent}`);
  return s;
}

async function usage(ctx) {
  await ctx.evaluate(scrollUsageCard, 212);
  await ctx.settle();
  const lines = await ctx.evaluate(usageAccountLines);
  ctx.assert('usage-accounts', lines >= ROSTER_SIZE, `${lines} account lines`);
  return { lines };
}

async function dates(ctx) {
  await ctx.settle();
  const s = await ctx.evaluate(sessionsState, null);
  ctx.assert('english-dates', s.rows > 0 && s.englishDates, `rows ${s.rows}, English dates ${s.englishDates}`);
  return s;
}

const detail = dates; // the expectation already waits for an event block

async function memberRows(ctx) {
  await ctx.settle();
  const s = await ctx.evaluate(sessionsState, ctx.person('marcus').email);
  ctx.assert('member-rows-own', s.rows > 0 && s.rowsWithEmail === s.rows, `${s.rowsWithEmail}/${s.rows} rows carry the member's email`);
  return s;
}

async function noLogin(ctx) {
  const nav = await ctx.navLog();
  ctx.assert('no-login-with-state', !nav.some((p) => p.startsWith('/login')), 'the login page was shown');
  return { nav };
}

async function managementCount(ctx) {
  await ctx.goto('/users/', 'b');
  await ctx.click('@users.tab-management');
  await ctx.waitFor('@users.add');
  await ctx.settle();
  return ctx.evaluate(usersTable, 'quinnsampleton');
}

export default {
  // The refresh cookie is scoped to the refresh path; /api/health is a same-origin page that is not an app route.
  engine: { cookiePaths: ['/', '/api/auth/refresh'], stateLoadPath: '/api/health' },
  capture: { pointer: { x: 4, y: 896 } },

  setup: {
    async reanchor(ctx) {
      const [file, ...args] = need(ctx, 'reanchor_command');
      ctx.exec(file, args);
      const health = await (await fetch(need(ctx, 'health_url'))).json();
      const age = Math.round((Date.now() - Date.parse(health.latest_event_at)) / 1000);
      const max = Number(need(ctx, 'freshness_max_seconds'));
      ctx.assert('newest-event-fresh', age <= max, `newest event ${age}s old, limit ${max}s`);
      return { ageSeconds: age };
    },
    async 'quinn-absent'(ctx) {
      try {
        mkdirSync(LOCK);
      } catch {
        throw new Error('another onboarding run holds the lock; refusing to start');
      }
      const n = psql(ctx, `SELECT count(*) FROM app_users WHERE ${QUINN_MATCH};`);
      if (n !== '0') {
        rmdirSync(LOCK);
        throw new Error(`the temporary persona already exists (${n} rows); run runner/cleanup-onboarding.sql first`);
      }
      lockTaken = true;
    },
  },

  // A random new password for the onboarding persona; it lives in memory only.
  async secret(name) {
    return name === 'new_password' ? randomBytes(18).toString('base64url') : undefined;
  },

  after: {
    'ivy-overview-minute': overview,
    'ivy-plan-usage': usage,
    'ivy-sessions-open': dates,
    'ivy-sessions-pick-provider-a': detail,
    'marcus-sessions': memberRows,

    'injected-ivy-overview-minute': overview,
    'injected-ivy-plan-usage': usage,
    'injected-ivy-sessions-open': dates,
    async 'injected-ivy-sessions-pick-provider-a'(ctx) {
      return { ...(await detail(ctx)), ...(await noLogin(ctx)) };
    },
    async 'injected-marcus-sessions'(ctx) {
      return { ...(await memberRows(ctx)), ...(await noLogin(ctx)) };
    },

    async 'admin-adds-user-id'(ctx) {
      const role = await ctx.evaluate(addUserRole);
      ctx.assert('new-user-role', role === 'User', `role ${role}`);
      return { role };
    },
    async 'temp-password'(ctx) {
      ctx.assert('temp-password-masked', await ctx.evaluate(tempPasswordMasked), 'the one-time password is still in the DOM');
    },
    async 'forced-change-admin'(ctx) {
      await ctx.settle();
      const lock = await ctx.evaluate(sidebarLock);
      ctx.assert('sidebar-locked', lock.locked > 0 && lock.open === 0, `${lock.locked} locked, ${lock.open} open`);
      return lock;
    },
  },

  /** Onboarding only, and only when this run's setup passed: remove the persona with SQL, then confirm in the admin UI. */
  async teardown(ctx) {
    if (!lockTaken) return undefined;
    try {
      if (ctx.config.psql_command === undefined) throw new Error('hook config is missing psql_command; run runner/cleanup-onboarding.sql by hand');
      const out = psql(ctx, readFileSync(join(HERE, 'cleanup-onboarding.sql'), 'utf8'));
      const kv = Object.fromEntries(out.split('\n').map((l) => l.split('|')).map(([k, v]) => [k, Number(v)]));
      ctx.assert('cleanup-sql', kv.remaining_quinn === 0 && kv.users === ROSTER_SIZE, `${kv.remaining_quinn} persona rows left, ${kv.users} users`);
      await ctx.persona('ivy');
      const t = await managementCount(ctx);
      ctx.assert('cleanup-ui', t.count === ROSTER_SIZE && !t.listed, `${t.count} users listed`);
      return { sql: kv, ui: t };
    } finally {
      rmdirSync(LOCK);
      lockTaken = false;
    }
  },
};
