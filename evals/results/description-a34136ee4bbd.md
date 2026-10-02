# Trigger measurement: description sha256 a34136ee4bbd

- Description measured: `SKILL.md` line 3 (sha256 prefix `a34136ee4bbd`, 768 characters).
- Router: an agent CLI run headless with one strong-tier model, project-only settings, plan permission mode, at most 3 turns. The skill was listed next to seven neighbouring skills (verification, UI review, TDD, audit, prose editing, planning, secret response) plus the host's built-in skills.
- Recorded: the first skill invoked in each run.

## Set in `trigger-queries.json` (3 runs per query)

| Group | Routed to journey-qa |
|---|---|
| Should trigger (10 queries) | 30/30 |
| Near miss (10 queries) | 0/30 |

## Extra queries

| Query | Runs | Routed to journey-qa |
|---|---|---|
| Acceptance-test the product as a new 3-person team that is adopting it | 3 | 3/3 |
| Execute the install guide verbatim from start to finish and record every missing step and wrong example as a defect | 5 | 5/5 |

## How the shorter description got here

The description was cut from about 1000 to under 800 characters. Two intermediate versions were measured with the same method:

- Without any mention of turning a journey into a scenario or replaying it, the codify query routed 0/3 and the replay query 2/3. The final text names both.
- With "run setup or install docs verbatim and log the gaps", the verbatim-guide query routed 1/3 in one measurement and 3/3 in another. The final text uses the wording people use for that request (execute the guide step by step, log missing steps and wrong examples).

Limits: one router model, a small skill listing, few runs per query. Hosts with many installed skills may shorten or drop descriptions, which this run did not exercise.
