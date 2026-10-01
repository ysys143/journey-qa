// Example only: one journey-qa phase as a workflow script for an agent runtime that offers
// agent(), phase() and log(). Plan -> execute (split stages) -> gate -> repair.
// Copy, adapt to your runtime, and pass the phase through `args`:
//   {
//     spec:          "phase spec text (what this phase must produce)",
//     doc:           "path to the plan / decision log",
//     rules:         "hard rules for this phase (no production writes, no credential reads, ...)",
//     evidenceDir:   "absolute path under the run directory",
//     gates:         ["G-leak-text", "G-png-meta", ...],
//     stages:        [{ title: "implement", goal: "..." }, { title: "load", goal: "..." }, ...],
//     models:        { planner: "<strong tier>", executor: "<standard tier>", gate: "<strong tier>" },
//     gateAgentType: "<read-only verifier agent type>"   // optional
//   }
// Design rules:
// - Big phases are split into sequential execute stages; one very large stage tends to stop early.
// - In runtimes where workflow agents cannot spawn sub-agents, every executor is told to do the work itself.
// - The gate agent is read-only and does not trust the executor's report.
// - Repair never edits gates, denylists or policy; two failed repairs stop the phase.
// - In runtimes that cannot message a running workflow agent, change args and resume instead.
// - Checks that need host infrastructure (containers, VMs) are run by the main session, not here.
export const meta = {
  name: 'journey-qa-phase',
  description: 'Run one journey-qa phase: plan, execute in split stages, gate read-only, repair at most twice',
  phases: [
    { title: 'Plan' },
    { title: 'Execute' },
    { title: 'Gate' },
    { title: 'Repair' },
  ],
}

const PLAN = {
  type: 'object', required: ['tasks'],
  properties: {
    tasks: {
      type: 'array',
      items: {
        type: 'object', required: ['id', 'stage', 'goal', 'done', 'evidence'],
        properties: {
          id: { type: 'string' },
          stage: { type: 'string' },
          goal: { type: 'string' },
          done: { type: 'string' },
          evidence: { type: 'string' },
          dependsOn: { type: 'array', items: { type: 'string' } },
        },
      },
    },
  },
}
const RUN = {
  type: 'object', required: ['status', 'summary', 'evidence'],
  properties: {
    status: { type: 'string', enum: ['done', 'blocked'] },
    summary: { type: 'string' },
    evidence: { type: 'array', items: { type: 'string' } },
    deviations: { type: 'array', items: { type: 'string' } },
    blockedBecause: { type: 'string' },
  },
}
const VERDICT = {
  type: 'object', required: ['pass', 'gates'],
  properties: {
    pass: { type: 'boolean' },
    gates: {
      type: 'array',
      items: {
        type: 'object', required: ['id', 'pass', 'proof'],
        properties: { id: { type: 'string' }, pass: { type: 'boolean' }, proof: { type: 'string' }, fix: { type: 'string' } },
      },
    },
    images: {
      type: 'array',
      items: {
        type: 'object', required: ['file', 'described', 'pass'],
        properties: { file: { type: 'string' }, described: { type: 'string' }, pass: { type: 'boolean' } },
      },
    },
    escalate: { type: 'array', items: { type: 'string' } },
  },
}

const SOLO = 'You have no subagent tool. Do the work yourself. Do not stop to hand off; ' +
  'if something truly blocks you, return status "blocked" with the exact reason and what you already did.'
const NO_GATE_EDITS = 'Never edit gate scripts, gates/MANIFEST.sha256, denylists, policy files or selftest fixtures. ' +
  'Never change what a document means to make a gate pass; report it instead.'

phase('Plan')
const plan = await agent(
  `${args.spec}\n\nBreak this phase into tasks grouped by these stages, in order: ` +
  `${args.stages.map(s => s.title).join(', ')}. Every task needs a done-criterion and an evidence path ` +
  `under ${args.evidenceDir}. Plan/decision log: ${args.doc}`,
  { model: args.models.planner, schema: PLAN, phase: 'Plan' })

phase('Execute')
let summary = ''
for (const stage of args.stages) {
  const tasks = plan.tasks.filter(t => t.stage === stage.title)
  const run = await agent(
    `Stage "${stage.title}": ${stage.goal}\nTasks: ${JSON.stringify(tasks)}\n` +
    `Previous stages: ${summary || '(none)'}\nRules: ${args.rules}\n${NO_GATE_EDITS}\n${SOLO}\n` +
    `Write evidence under ${args.evidenceDir}. Do not judge gates; that is a separate pass.`,
    { model: args.models.executor, schema: RUN, phase: 'Execute', label: `exec:${stage.title}` })
  if (!run || run.status === 'blocked') {
    log(`stage ${stage.title} blocked: ${run ? run.blockedBecause : 'agent died'}`)
    return { plan, blockedAt: stage.title, run }
  }
  summary += `\n[${stage.title}] ${run.summary}`
}

const GATE_PROMPT =
  `Judge gates ${args.gates.join(', ')} from evidence in ${args.evidenceDir}. ` +
  'First verify the gates, capture and adapter manifests (`./check.sh` does it); a mismatch fails every gate. ' +
  'Run the gate scripts yourself and record exit codes. Do not trust the executor summary. ' +
  'Missing evidence is a fail. Open every image this phase produced and describe each one in your own words (fixtures/realism-checklist.md). ' +
  'Put anything only the user can decide (waivers, exemptions, meaning-changing edits) in escalate. ' +
  `Plan/decision log: ${args.doc}`

const GATE_TYPE = args.gateAgentType ? { agentType: args.gateAgentType } : {}
let verdict = null
for (let attempt = 0; attempt <= 2; attempt++) {
  verdict = await agent(GATE_PROMPT,
    { model: args.models.gate, ...GATE_TYPE, schema: VERDICT, phase: 'Gate', label: `gate:${attempt}` })
  if (!verdict || verdict.pass || attempt === 2) break
  const failed = verdict.gates.filter(g => !g.pass)
  log(`gate failed: ${failed.map(g => g.id).join(', ')} (attempt ${attempt + 1})`)
  await agent(
    `Fix only these gate failures:\n${JSON.stringify(failed)}\nRules: ${args.rules}\n${NO_GATE_EDITS}\n${SOLO}\n` +
    `Write evidence under ${args.evidenceDir}.`,
    { model: args.models.executor, schema: RUN, phase: 'Repair', label: `repair:${attempt}` })
}
return { plan, summary, verdict }
