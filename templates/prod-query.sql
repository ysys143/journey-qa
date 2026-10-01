-- prod-query.sql — template for one read against a production database.
--
-- Before running (docs/prod-access.md):
--   [ ] saved to a file before execution
--   [ ] reviewed by someone other than the author (reviewer: ____)
--   [ ] EXPLAIN (no ANALYZE) run first and saved next to this file; estimated rows: ____ (ceiling: ____)
--   [ ] only allowlisted tables/columns are read
--   [ ] aggregates per session/day BEFORE any window function or join fan-out
--   [ ] shortest date window that answers the question: ____ to ____
--   [ ] host health checked before (and will be after)
--   [ ] run by the top-level executor only
--
-- Output: aggregates only. Convert to a numeric-only distribution file and delete raw output.

SET default_transaction_read_only = on;
BEGIN READ ONLY;
SET LOCAL max_parallel_workers_per_gather = 0;
SET LOCAL statement_timeout = '60s';
SET LOCAL temp_file_limit = '256MB';

-- Replace with the reviewed query. Example shape: reduce per day first, then summarise.
WITH per_day AS (
  SELECT date_trunc('day', ts) AS day, count(*) AS n
  FROM some_event_table
  WHERE ts >= now() - interval '14 days'
  GROUP BY 1
)
SELECT percentile_disc(ARRAY[0.1, 0.25, 0.5, 0.75, 0.9, 0.99]) WITHIN GROUP (ORDER BY n) AS n_pct
FROM per_day;

ROLLBACK;
