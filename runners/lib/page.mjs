// In-page functions shared by every backend. Each one is serialized and run
// with evaluate(fn, arg), so it must not refer to anything outside its own
// body. Return values are counts, flags and short strings where possible.

export function pageText() {
  return document.body ? document.body.innerText : '';
}

export function textPresent(text) {
  return !!document.body && document.body.innerText.includes(text);
}

/** Probe for settle(): fonts loaded, text length and height of the main region. */
export function settleProbe() {
  const root = document.querySelector('main') || document.body;
  return `${document.fonts.status}:${root ? root.innerText.length : 0}:${root ? root.scrollHeight : 0}`;
}

/** Disable transitions, animations and the caret so two captures of the same state match. */
export function freezeMotion() {
  if (document.getElementById('jqa-no-motion')) return;
  const s = document.createElement('style');
  s.id = 'jqa-no-motion';
  s.textContent = '*,*::before,*::after{transition:none!important;animation:none!important;caret-color:transparent!important}';
  document.head.appendChild(s);
}

/** Make one-time secrets invisible in pixels from the moment they render. Selectors are plain CSS. */
export function hideSecretPixels(selectors) {
  const s = document.createElement('style');
  s.textContent = selectors.map((sel) => `${sel}:not([data-jqa-masked]){color:transparent!important}`).join('\n');
  document.head.appendChild(s);
}

/** Read a displayed one-time secret and replace it in the DOM. The caller keeps the value in memory. */
export function takeSecret({ selector, mask }) {
  const el = document.querySelector(selector);
  if (!el) return null;
  const value = el.textContent.trim();
  el.textContent = mask;
  el.dataset.jqaMasked = '1';
  return value;
}

/** Clear password inputs and mask still-visible one-time secrets; return the values found. */
export function scrubDom({ mask, selectors }) {
  const found = [];
  for (const i of document.querySelectorAll('input[type=password]')) {
    if (i.value) found.push(i.value);
    i.value = '';
    i.removeAttribute('value');
  }
  for (const sel of selectors) {
    for (const el of document.querySelectorAll(`${sel}:not([data-jqa-masked])`)) {
      const v = el.textContent.trim();
      if (v) found.push(v);
      el.textContent = mask;
      el.dataset.jqaMasked = '1';
    }
  }
  return found;
}

/** Set a <select>'s value and fire the events a user change fires. */
export function selectValue({ selector, value }) {
  const el = document.querySelector(selector);
  if (!el) return false;
  el.value = value;
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  return el.value === value;
}

/** Selectors (plain CSS) with at least one element whose text is cut off. */
export function truncated(selectors) {
  return selectors.filter((sel) => [...document.querySelectorAll(sel)].some((el) => el.scrollWidth > el.clientWidth + 1));
}

/**
 * Scroll for a capture. {mode: 'top'}: every scrolled element back to 0.
 * {selector, top}: the element's nearest scrollable ancestor so its top edge
 * sits at `top` CSS px. {y}: the document scroller to y.
 */
export function scrollTo(arg) {
  if (arg.mode === 'top') {
    for (const el of [document.scrollingElement, ...document.querySelectorAll('*')]) {
      if (el && el.scrollTop > 0) el.scrollTop = 0;
    }
    return true;
  }
  if (arg.selector) {
    const el = document.querySelector(arg.selector);
    if (!el) return false;
    let box = el.parentElement;
    while (box && box !== document.body && !(box.scrollHeight > box.clientHeight && /auto|scroll/.test(getComputedStyle(box).overflowY))) {
      box = box.parentElement;
    }
    const scroller = box && box !== document.body ? box : document.scrollingElement;
    scroller.scrollTop += el.getBoundingClientRect().top - (arg.top || 0);
    return true;
  }
  document.scrollingElement.scrollTop = arg.y || 0;
  return true;
}

/** Same-origin request with the page's cookies; returns the status only. */
export async function fetchStatus({ method, path, body }) {
  const init = { method, credentials: 'same-origin', headers: {} };
  if (body !== undefined) {
    init.headers['content-type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  const res = await fetch(path, init);
  return res.status;
}

/**
 * Rectangles (CSS px, viewport-relative) of regions that change without a
 * product change: chart areas and media, times and dates, durations, live
 * counters. `extra.selectors` and `extra.text` (regular expression sources)
 * come from the adapter's selector map. gates/img_diff.py ignores pixels inside.
 */
export function collectMasks(extra) {
  const rects = [];
  const add = (r, kind) => {
    const x = Math.max(0, r.left), y = Math.max(0, r.top);
    const w = Math.min(innerWidth, r.right) - x, h = Math.min(innerHeight, r.bottom) - y;
    if (w > 0 && h > 0) rects.push({ kind, x: Math.floor(x), y: Math.floor(y), w: Math.ceil(w), h: Math.ceil(h) });
  };
  const boxes = ['canvas', 'video', 'iframe', ...(extra.selectors || [])].join(',');
  for (const el of document.querySelectorAll(boxes)) add(el.getBoundingClientRect(), 'region');
  const volatile = [
    ['relative-time', /\b\d+\s?(s|m|h|d|min|mins|hours?|days?)\s+ago\b|\bjust now\b/i],
    ['time', /\b\d{1,2}:\d{2}(:\d{2})?\b/],
    ['date', /\b\d{2}\/\d{2}(\/\d{4})?\b|\b\d{4}-\d{2}-\d{2}\b|\b\d{4}\.\s?\d{1,2}\.\s?\d{1,2}\b/],
    ['duration', /\b\d+[hms]\s\d+[ms]\b/],
    ['counter', /^[\s$~≈]*[\d.,]+\s*(%|[KMB]|ms|s|m|h)?(\s*(\/|of)\s*[\d.,$]+)?\s*$|\$[\d.,]+|\b\d+(\.\d+)?%|\b[\d.]+[KMB]\b/],
    ...(extra.text || []).map((src) => ['adapter', new RegExp(src)]),
  ];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const t = n.textContent.trim();
    if (!t || !n.parentElement || n.parentElement.closest(boxes)) continue;
    const hit = volatile.find(([, re]) => re.test(t));
    if (!hit) continue;
    const range = document.createRange();
    range.selectNodeContents(n);
    for (const r of range.getClientRects()) add(r, hit[0]);
  }
  return { viewport: { w: innerWidth, h: innerHeight, dpr: devicePixelRatio }, rects };
}

/**
 * Records every pathname the tab shows, including client-side redirects, in
 * sessionStorage. Installed as an init script so it runs before the app.
 */
export const NAV_LOG_SCRIPT = `(() => {
  try { performance.setResourceTimingBufferSize(5000); } catch {}
  const key = 'jqa:nav';
  const log = () => { try { const a = JSON.parse(sessionStorage.getItem(key) || '[]'); if (a[a.length - 1] !== location.pathname) { a.push(location.pathname); sessionStorage.setItem(key, JSON.stringify(a)); } } catch {} };
  for (const m of ['pushState', 'replaceState']) { const f = history[m]; history[m] = function (...args) { const r = f.apply(this, args); log(); return r; }; }
  addEventListener('popstate', log);
  log();
})();`;

export function readNavLog() {
  try { return JSON.parse(sessionStorage.getItem('jqa:nav') || '[]'); } catch { return []; }
}

export function clearNavLog() {
  try { sessionStorage.removeItem('jqa:nav'); } catch {}
}
