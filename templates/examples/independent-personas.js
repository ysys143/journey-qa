// Example only: independent persona sessions (docs/workflow.md, "Independent persona sessions") as a workflow
// script for an agent runtime that offers agent(), phase() and log(). Each persona walks the journey from the
// beginning in its own agent session; a merge step then removes duplicates across personas and counts reach.
// Copy, adapt to your runtime, and pass the run through `args`:
//   {
//     runDir:       "absolute path of the run directory (.journey-qa/runs/<run-id>)",
//     docsEntry:    "entry point of the public documentation (a URL or a run-relative path)",
//     journeys:     [{ id: "first-run", goal: "reach a working product by following the public docs" }],
//     personas:     [{ id: "ivy",
//                      profile:        "this person's roster entry as text, perspective fields included (and nobody else's)",
//                      browserProfile: "a browser profile or task space name of its own",
//                      instance:       "id of a clean environment instance of its own" }],
//     rules:        "hard rules for every session (no production writes, no credential reads, ...)",
//     rosterPath:   "absolute path of the roster",
//     parallel:     3,                                       // sessions running at once
//     models:       { persona: "<standard tier>", executor: "<standard tier>", gate: "<strong tier>" },   // roles, not model names
//     gateAgentType: "<read-only verifier agent type>"       // optional
//   }
// Design rules:
// - One agent() call is one session. A persona's prompt holds that persona's profile and nothing about the others;
//   nothing a session writes is read by another before the merge.
// - Every persona session runs on the same model tier, so a difference between personas comes from the profile.
// - The prepare agent creates the directories and the session records; it does not walk any journey.
// - The merge is a script, not a judgment: it refuses sessions that share an id, a browser profile or an instance.
// - The gate agent is read-only and does not trust the sessions' summaries or the merge output.
// - In runtimes where workflow agents cannot spawn sub-agents, every session is told to do the work itself.
// - In runtimes that cannot message a running workflow agent, change args and resume instead.
export const meta = {
  name: 'journey-qa-independent-personas',
  description: 'Prepare per-persona directories, run one isolated session per persona, merge findings by reach, judge read-only',
  phases: [
    { title: 'Prepare' },
    { title: 'Explore' },
    { title: 'Merge' },
    { title: 'Judge' },
  ],
}

const PREPARED = {
  type: 'object', required: ['directories'],
  properties: { directories: { type: 'array', items: { type: 'string' } }, notes: { type: 'string' } },
}
const SESSION = {
  type: 'object', required: ['status', 'summary'],
  properties: {
    status: { type: 'string', enum: ['done', 'blocked'] },
    summary: { type: 'string' },
    findings: { type: 'number' },
    blockedBecause: { type: 'string' },
  },
}
const MERGED = {
  type: 'object', required: ['exitCode', 'output'],
  properties: { exitCode: { type: 'number' }, output: { type: 'string' }, defects: { type: 'number' } },
}
const VERDICT = {
  type: 'object', required: ['pass', 'checks'],
  properties: {
    pass: { type: 'boolean' },
    checks: {
      type: 'array',
      items: {
        type: 'object', required: ['id', 'pass', 'proof'],
        properties: { id: { type: 'string' }, pass: { type: 'boolean' }, proof: { type: 'string' }, fix: { type: 'string' } },
      },
    },
    sameDefect: { type: 'array', items: { type: 'string' } },
    escalate: { type: 'array', items: { type: 'string' } },
  },
}

const SOLO = 'You have no subagent tool. Do the work yourself. Do not stop to hand off; ' +
  'if something truly blocks you, return status "blocked" with the exact reason and what you already did.'
const NO_GATE_EDITS = 'Never edit gate scripts, manifests, denylists, policy files or selftest fixtures. ' +
  'Never change what a document means to make a gate pass; report it instead.'
const ids = args.personas.map(p => p.id).join(',')

phase('Prepare')
const prepared = await agent(
  `Under ${args.runDir}/personas/ create one directory per persona (${ids}), each with evidence/, shots/ and a mode-700 secrets/. ` +
  `Write personas/<id>/session.json (templates/persona-session.schema.json) for each from this table and nothing else: ` +
  `${JSON.stringify(args.personas.map(p => ({ persona: p.id, session_id: `${p.id}-session`, browser_profile: p.browserProfile, instance: p.instance, journeys: args.journeys.map(j => j.id), inputs: { profile: p.id, docs: args.docsEntry } })))}. ` +
  `Do not walk any journey and do not read any persona profile. ${SOLO}`,
  { model: args.models.executor, schema: PREPARED, phase: 'Prepare' })
if (!prepared) return { blockedAt: 'Prepare' }

function briefFor(p) {
  return [
    `You are ${p.id}, one person walking a product's journeys for the first time. Your profile (nobody else's is available to you):`,
    p.profile,
    `Journeys: ${JSON.stringify(args.journeys)}.`,
    `The only documentation you may use is the public documentation starting at ${args.docsEntry}. Follow it exactly as written, in the written order, ` +
      'as this person would, with their experience, device, assistive technology, reading habit, goals and constraints. A command the docs do not contain is a defect.',
    `Your browser state is the profile "${p.browserProfile}" and your environment is the instance "${p.instance}". Use nothing else.`,
    `Write only under ${args.runDir}/personas/${p.id}/. Do not read any other directory of ${args.runDir}/personas/. Pass absolute paths to tools; write run-relative paths in evidence.`,
    `Record every gap between the docs, the product and what happens as a finding in findings.json (templates/persona-findings.schema.json): class, a detail code from templates/issue-draft.md, ` +
      'the journey id, a locus every reader can name (the docs location as file#anchor, or the product route), a title, and at least one evidence file inside your directory. ' +
      'Barriers your perspective meets are findings too. You record defects and evidence; you do not fix the product or the docs.',
    `Rules: ${args.rules}`,
    NO_GATE_EDITS,
    SOLO,
  ].join('\n')
}

phase('Explore')
const sessions = []
for (let i = 0; i < args.personas.length; i += args.parallel || 1) {
  const wave = args.personas.slice(i, i + (args.parallel || 1))
  const done = await Promise.all(wave.map(p =>
    agent(briefFor(p), { model: args.models.persona, schema: SESSION, phase: 'Explore', label: `persona:${p.id}` })))
  wave.forEach((p, k) => sessions.push({ id: p.id, result: done[k] }))
}
const blocked = sessions.filter(s => !s.result || s.result.status === 'blocked')
if (blocked.length) {
  log(`sessions blocked: ${blocked.map(s => s.id).join(', ')}`)
  return { sessions, blockedAt: 'Explore' }
}

phase('Merge')
const merged = await agent(
  `Run the gates on each persona directory under ${args.runDir}/personas/ (gates/leakscan.py, gates/png_meta.py, gates/image_scan.py with the run's roster, denylists and policy) and record every exit code. ` +
  `Then run: python3 runners/explore/merge_findings.py --run ${args.runDir} --roster ${args.rosterPath} --expect ${ids} and return its exit code and output. ` +
  `Do not edit any persona file to make either step pass. ${NO_GATE_EDITS} ${SOLO}`,
  { model: args.models.executor, schema: MERGED, phase: 'Merge' })
if (!merged || merged.exitCode !== 0) {
  log(`merge failed: ${merged ? merged.output : 'agent died'}`)
  return { sessions, merged, blockedAt: 'Merge' }
}

phase('Judge')
const GATE_TYPE = args.gateAgentType ? { agentType: args.gateAgentType } : {}
const verdict = await agent(
  `Judge ${args.runDir}/merged-findings.json from the files under ${args.runDir}/personas/. Do not trust the sessions' summaries or the merge output. ` +
  'For each defect open the evidence of every persona listed and confirm it shows that defect (reach). ' +
  'Decide each entry of "near" (same journey and locus under different keys): list the ids that are one defect in sameDefect. ' +
  'Fail the run when a session shows signs of another persona\'s knowledge (a finding that cites a place only another persona visited, copied wording, a shared login). ' +
  'Put anything only the user can decide in escalate. Read only.',
  { model: args.models.gate, ...GATE_TYPE, schema: VERDICT, phase: 'Judge' })
return { sessions, merged, verdict }
