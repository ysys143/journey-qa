// Browser emulation for one persona. A scenario's personas[].emulation says what to
// emulate; with from_roster the roster person's perspective (persona.device,
// persona.assistive_tech) supplies the values, and explicit fields win over them.
// Pure functions: the backends apply the result, nothing here touches a browser.

/** What a roster device means for the browser. A desktop person keeps the scenario's pinned viewport. */
export const DEVICE_PRESETS = {
  mobile: { viewport: { width: 390, height: 844, device_scale_factor: 3 }, mobile: true },
  tablet: { viewport: { width: 820, height: 1180, device_scale_factor: 2 }, mobile: true },
};

/** persona.assistive_tech "zoom" is browser zoom at this factor. */
export const ZOOM_FROM_ROSTER = 2;

/** Perspective values a browser cannot emulate; they are reported as not applied, never dropped silently. */
export const NOT_EMULATED = ['screen-reader', 'keyboard-only', 'high-contrast', 'voice-control'];

/** Backend capabilities a request can need. A backend lists the ones it applies in its `emulates` set. */
export const FEATURES = ['viewport', 'mobile', 'reduced_motion'];

/**
 * Resolve the emulation of one persona.
 *   determinism  the scenario's pinned values (viewport is the base)
 *   person       the roster person
 *   spec         personas[].emulation, or undefined
 *   supports     Set of FEATURES the backend applies; omitted = all (preflight)
 * Returns { viewport, mobile, reducedMotion, zoom, applied, notApplied }. viewport is the effective
 * one: browser zoom is modelled the way a browser applies it, the CSS viewport shrinks by the factor
 * and the device pixel ratio grows by it.
 * Throws when from_roster has nothing to read, so a request never silently does nothing.
 */
export function resolveEmulation({ determinism, person, spec, supports }) {
  const base = determinism.viewport;
  const out = { viewport: { ...base }, mobile: false, reducedMotion: false, zoom: 1, applied: [], notApplied: [] };
  if (!spec) return out;
  const want = { viewport: null, mobile: null, reduced_motion: null, zoom: null };
  const notes = [];
  if (spec.from_roster) {
    const profile = (person && person.persona) || {};
    if (profile.device === undefined && profile.assistive_tech === undefined) {
      throw new Error(`emulation.from_roster: roster person ${person ? person.id : '?'} has no persona.device or persona.assistive_tech`);
    }
    const preset = DEVICE_PRESETS[profile.device];
    if (preset) {
      want.viewport = preset.viewport;
      want.mobile = preset.mobile;
    }
    const tech = profile.assistive_tech || [];
    if (tech.includes('reduced-motion')) want.reduced_motion = true;
    if (tech.includes('zoom')) want.zoom = ZOOM_FROM_ROSTER;
    for (const t of tech) if (NOT_EMULATED.includes(t)) notes.push(`assistive_tech:${t}`);
  }
  for (const k of Object.keys(want)) if (spec[k] !== undefined) want[k] = spec[k];

  const can = (feature) => !supports || supports.has(feature);
  const take = (feature, value, apply) => {
    if (value === null || value === undefined) return;
    if (!can(feature)) { out.notApplied.push(feature); return; }
    apply(value);
  };
  take('viewport', want.viewport, (v) => { out.viewport = { ...v }; });
  take('mobile', want.mobile, (v) => { out.mobile = !!v; });
  take('reduced_motion', want.reduced_motion, (v) => { out.reducedMotion = !!v; });
  if (want.zoom !== null && want.zoom !== 1) {
    if (!can('viewport')) out.notApplied.push('zoom');
    else out.zoom = want.zoom;
  }
  if (out.zoom !== 1) {
    out.viewport = {
      width: Math.round(out.viewport.width / out.zoom),
      height: Math.round(out.viewport.height / out.zoom),
      device_scale_factor: out.viewport.device_scale_factor * out.zoom,
    };
  }
  const vp = out.viewport;
  if (vp.width !== base.width || vp.height !== base.height || vp.device_scale_factor !== base.device_scale_factor) {
    out.applied.push(`viewport ${vp.width}x${vp.height}@${vp.device_scale_factor}`);
  }
  if (out.mobile) out.applied.push('mobile');
  if (out.reducedMotion) out.applied.push('reduced_motion');
  if (out.zoom !== 1) out.applied.push(`zoom x${out.zoom}`);
  out.notApplied.push(...notes);
  return out;
}
