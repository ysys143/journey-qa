# Trigger evaluation set

`trigger-queries.json` holds 20 queries used to check whether the `description` in `SKILL.md` routes requests correctly.

- 10 entries have `expected_route: "journey-qa"` (should trigger).
- 10 entries have `expected_route: "not journey-qa"` (near-misses: similar wording, different intent).
- Both English and Korean queries appear in each group.
- Each entry has a one-line `reason` explaining the expected route.

## Measuring

Re-run the set every time the `description` changes. A stale measurement says nothing about the current text.

1. For each query, present only the skill description (name plus description, as a host would see it) together with the query to the routing component you want to evaluate. Do not give it the skill body.
2. Run each query several times (at least 3) because routing can vary between runs.
3. Record, per query, how many runs routed to this skill.
4. Compute:
   - trigger rate = routed runs / total runs over the 10 should-trigger queries (higher is better);
   - false-trigger rate = routed runs / total runs over the 10 near-miss queries (lower is better).
5. Inspect every query that routed the wrong way and decide whether the description or the query needs to change.

## Recording results

Write results next to the set, in `evals/results/<date>-<router>.md` or similar: the description text (or its hash) that was measured, the router used, runs per query, both rates, and the list of misrouted queries. Only record numbers that were actually measured. No results are recorded yet.

## Checks

`./check.sh` runs `evals/check_set.py` (20 entries, 10/10 split, both languages in each group, required fields, no duplicates, description at most 1024 characters with a "Not for:" clause) and `evals/selftest.sh` (broken variants must fail). It does not measure routing.
