# Production data access

Reading production data can be necessary to make synthetic data realistic. A read-only query can still take production down. Review must therefore ask two questions: which columns does the query read, and how heavy is it. Checking only the first is not enough.

## What to take

Production data is material for making screens plausible. Do not copy it with personal data removed.

| Take | Do not take |
|---|---|
| Distributions of values and time structure (percentiles, hourly weights, ratios) | Every identifier column |
| Product vocabulary such as model, plan and tool names | Free-text and JSON columns |
| | Tokens, password hashes, credential tables |
| | Message queue and archive schemas |

- Fix the readable tables and columns as an allowlist and check each query file against it.
- Results are aggregates only. Convert them to a numbers-only distribution file, check by structure that only allowed keys are present, and delete the raw output.
- One exception: identifier columns may be read to build the leak-scan denylist. Write the result straight to `secrets/`, never print it to the screen, and report only the line count.
- A human sets the scope (human gate).

## For every query

1. Save it to a file before running it.
2. A reviewer other than the author (a separately started agent) reviews it. If no reviewer can be started, do not run it.
3. Run `EXPLAIN` (without ANALYZE) first and keep the output as a file. If the estimated row count exceeds the cap, do not run the query.
4. Wrap it in a read-only transaction with resource limits (`templates/prod-query.sql`).
5. Aggregate by session or day before any window function or join.
6. Use the shortest period that works.
7. Check production host state (CPU, disk, container status) before and after.
8. On failure, retry once with a narrower query.
9. Only the top-level executing agent runs it. Do not delegate to a sub-agent.

Wrapping for PostgreSQL:

```sql
SET default_transaction_read_only = on;
BEGIN READ ONLY;
SET LOCAL max_parallel_workers_per_gather = 0;
SET LOCAL statement_timeout = '60s';
SET LOCAL temp_file_limit = '256MB';
-- query
ROLLBACK;
```

For other databases apply the same principle (read-only session, statement time limit, limits on temp space and parallelism) in that database's own way.

## When a rule is broken

- Report any incident (production load, exhausted space) to the user immediately.
- A violation that already ran cannot be fixed afterward. Record it factually in the decision log and ask the user whether to accept it. Do not approve it yourself. If refused, it stays a permanent violation.
- Apply the rules again from the next query.
