// Failed-request evidence. A collector turns request lifecycle events (from Playwright
// page events or from CDP Network events) into a per-step record of the requests that
// failed: an HTTP status of 400 or more, an aborted request, any other network failure.
// A record holds method, where (scenario instance id or host), path, query keys with their
// values replaced, status, failure text, kind and timing. It never holds a header, a body
// or a query value. Pure functions and one class; nothing here touches a browser or a file.

export const REDACTED = '[redacted]';
const MAX_PATH = 300;
const MAX_QUERY = 200;
const MAX_FAILURE = 80;
const MAX_FAILED = 200;
const MAX_LOG = 5000;
const MAX_OPEN = 2000;
const TRACKED = /^(https?|wss?):$/;

/**
 * Describe a URL without anything that can carry a secret in a query string.
 * Returns null for schemes that are not network requests (data:, blob:, about:, extensions).
 * origins: Map of origin -> scenario instance id; a request to another origin is described by host.
 */
export function describeUrl(raw, origins = new Map()) {
  let u;
  try { u = new URL(raw); } catch { return null; }
  if (!TRACKED.test(u.protocol)) return null;
  const out = {};
  const instance = origins.get(u.origin);
  if (instance) out.instance = instance;
  else out.host = u.hostname;
  out.path = u.pathname.length > MAX_PATH ? `${u.pathname.slice(0, MAX_PATH)}[truncated]` : u.pathname;
  const keys = u.search.slice(1).split('&').filter(Boolean).map((kv) => {
    const i = kv.indexOf('=');
    return i < 0 ? kv.slice(0, 40) : `${kv.slice(0, Math.min(i, 40))}=${REDACTED}`;
  });
  if (keys.length) {
    const q = keys.join('&');
    out.query = q.length > MAX_QUERY ? `${q.slice(0, MAX_QUERY)}[truncated]` : q;
  }
  return out;
}

function cleanFailure(text) {
  if (!text) return null;
  return String(text).replace(/\s+/g, ' ').trim().slice(0, MAX_FAILURE) || null;
}

/** Collects the requests of one browser tab. begin / response / fail / end follow a request's life. */
export class NetworkCollector {
  constructor({ origins = new Map() } = {}) {
    this.origins = origins;
    this.open = new Map(); // request id -> { method, where, type, t0, status, responded, rec } (null for ignored schemes)
    this.total = 0;
    this.failed = [];
    this.omitted = 0;
    this.eventsSeen = 0; // CDP events ingested; lets a backend notice a feed that never delivers
  }

  begin(id, { method, url, type, t }) {
    const where = describeUrl(url, this.origins);
    if (!where) { this.open.set(id, null); return; }
    this.total += 1;
    this.open.set(id, { method: (method || 'GET').toUpperCase(), where, type: type || null, t0: t, status: null, responded: false, rec: null });
    if (this.open.size > MAX_OPEN) { // a body nobody reads keeps its request open; forget the oldest answered ones
      for (const [k, v] of this.open) {
        if (this.open.size <= MAX_OPEN) break;
        if (!v || v.responded) this.open.delete(k);
      }
    }
  }

  response(id, { status, t }) {
    const r = this.open.get(id);
    if (!r) return;
    r.status = status;
    r.responded = true;
    if (status >= 400 && !r.rec) r.rec = this.add(r, { kind: 'http-error', status, failure: null, t });
  }

  fail(id, { error, canceled, blocked, t }) {
    const r = this.open.get(id);
    this.open.delete(id);
    if (!r) return;
    const failure = cleanFailure(error) || (blocked ? `blocked:${cleanFailure(blocked)}` : 'failed');
    if (r.rec) { r.rec.failure = failure; return; } // a response with an error status that then broke off: one record
    const aborted = canceled || /ERR_ABORTED/.test(failure);
    this.add(r, { kind: aborted ? 'aborted' : 'failed', status: r.status, failure, t });
  }

  end(id) {
    this.open.delete(id);
  }

  add(r, { kind, status, failure, t }) {
    const rec = { method: r.method, ...r.where, status: status ?? null, failure, kind, resource: r.type, ms: Math.max(0, Math.round(t - r.t0)) };
    if (this.failed.length >= MAX_FAILED) { this.omitted += 1; return rec; }
    this.failed.push(rec);
    return rec;
  }

  /** Feed CDP Network events (the ego runtime's page.events()). Other events are ignored. */
  ingestCdp(events) {
    for (const e of events || []) {
      const p = e.params || {};
      switch (e.method) {
        case 'Network.requestWillBeSent':
          this.eventsSeen += 1;
          if (p.redirectResponse) this.response(p.requestId, { status: p.redirectResponse.status, t: p.timestamp * 1000 });
          this.begin(p.requestId, { method: p.request && p.request.method, url: p.request && p.request.url, type: p.type, t: p.timestamp * 1000 });
          break;
        case 'Network.responseReceived':
          this.eventsSeen += 1;
          this.response(p.requestId, { status: p.response && p.response.status, t: p.timestamp * 1000 });
          break;
        case 'Network.loadingFailed':
          this.eventsSeen += 1;
          this.fail(p.requestId, { error: p.errorText, canceled: p.canceled, blocked: p.blockedReason, t: p.timestamp * 1000 });
          break;
        case 'Network.loadingFinished':
          this.eventsSeen += 1;
          this.end(p.requestId);
          break;
        default:
      }
    }
  }

  /** What was seen since the last drain. drain: start the next step's window afterwards. */
  snapshot(drain = false) {
    const live = [...this.open.values()].filter((r) => r && !r.responded).length; // sent, and neither answered nor failed
    const snap = { collected: true, total: this.total, in_flight: live, failed: this.failed.map((f) => ({ ...f })), omitted: this.omitted };
    if (drain) { this.total = 0; this.failed = []; this.omitted = 0; }
    return snap;
  }
}

/** The record a backend that cannot collect hands back. The reason always says "not collected". */
export function notCollected(reason) {
  return { collected: false, reason: `not collected: ${reason}` };
}

/** One line per failed request for an error message: METHOD path (status or failure). */
export function describeFailed(f) {
  return `${f.method} ${f.path}${f.query ? `?${f.query}` : ''} (${f.status !== null && f.status >= 400 ? f.status : f.failure})`;
}

/**
 * expect.no_failed_requests: a problem text, or null when the step is proven clean.
 * A step whose requests were not collected is not proven clean.
 */
export function failedRequestsProblem(snap) {
  if (!snap || !snap.collected) return `no_failed_requests cannot be proven: ${(snap && snap.reason) || 'not collected: the backend returned no record'}`;
  if (!snap.failed.length && !snap.omitted) return null;
  const n = snap.failed.length + (snap.omitted || 0);
  const shown = snap.failed.slice(0, 3).map(describeFailed).join('; ');
  return `${n} failed request${n === 1 ? '' : 's'}: ${shown}${n > 3 ? '; ...' : ''}`;
}

/** The file a step leaves in <run>/network/. */
export function stepRecord({ step, persona, backend, snap }) {
  return { step, persona: persona || null, backend, ...snap };
}

/** A bounded log of reduced events for failure evidence (ego): method, path, status; no URLs with queries, no bodies. */
export function pushLog(log, entry) {
  log.push(entry);
  if (log.length > MAX_LOG) log.splice(0, log.length - MAX_LOG);
}
