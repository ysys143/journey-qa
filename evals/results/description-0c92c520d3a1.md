# Trigger measurement: description sha256 0c92c520d3a1

- Description measured: `SKILL.md` line 3 (sha256 prefix `0c92c520d3a1`).
- Router: an agent CLI run headless with one strong-tier model, project-only settings, plan permission mode, at most 3 turns. The skill was listed next to seven neighbouring skills (verification, UI review, TDD, audit, prose editing, planning, secret response) plus the host's built-in skills.
- Recorded: the first skill invoked in each run.
- Runs per query: 3.

## Set in `trigger-queries.json`

| Group | Routed to journey-qa |
|---|---|
| Should trigger (10 queries) | 30/30 |
| Near miss (10 queries) | 0/30 |

Misrouted queries: none. Near misses went to no skill, or to the neighbouring verification skill (code-change check) and UI review skill (accessibility review), as intended.

## Two extra queries that under-triggered on the previous description

| Query | Routed to journey-qa |
|---|---|
| Acceptance-test the product as a new 3-person team that is adopting it | 3/3 |
| Execute the install guide verbatim from start to finish and record every missing step and wrong example as a defect | 2/3 |

Limits: one router model, three runs per query, a small skill listing. Hosts with many installed skills may shorten or drop descriptions, which this run did not exercise.
