# example-app data contract

The dashboard's data rules that a synthetic data generator must follow. In a real adapter they come from reading the chart code and comparing with the real screens, and are re-checked when the product code changes; the rules below are invented examples of the kinds of rule to look for.

## Usage trend (per minute)

- Empty minutes are filled with 0. Usage rows must exist in nearly every minute inside an activity span to form a continuous band
- Request intervals of 5 to 40 seconds with jitter. A single session's intervals alone leave gaps over 60 seconds, so chain or overlap sessions within one activity span
- Split a session's total usage across requests with a long-tailed distribution so that a few requests stand out. An even split gives a flat line

## Plan usage

- The line breaks after 30 minutes without a sample. The client samples every 5 minutes, so samples arrive at 5 minutes plus or minus 20 seconds
- "Client on" time must be wider than the activity spans. There must be no gap of 30 minutes or more across the longest chart range (72 hours)
- All windows written by one sample (`short` and `weekly`) share the same `sampled_at`
- The primary key of `app_usage_samples` is `(provider, account_id, window_key, sampled_at)`, so several windows can be inserted at the same time
- Set `window_minutes` explicitly (`short` 300, `weekly` 10080). A missing value draws a zero-length line
- Utilization is a free parameter fitted to a target shape. Keep the short window in the mid range during busy hours and the weekly window rising smoothly through the week, with neither stuck at 0% or 100%

## Plans and prices

- Only these keys in the price table have prices: `provider-a:plan-large`, `provider-a:plan-medium`, `provider-b:plan-pro`, `provider-b:plan-plus`, `provider-b:plan-basic`
- A plan without a price draws no usage line
- Roster plan values are the stored shape the client writes, not display strings

## Cumulative counters

- A session's usage records must increase monotonically in all three cumulative values together: input, cached input, output
- A record whose three values all equal the previous record is discarded as a duplicate

## Derived tables and retention

- Leave rollup tables empty and let the backfill at server start recompute them
- A retention policy deletes data older than 90 days. Move the generated data's period to the most recent window relative to the capture day (`reanchor`)

## Screen behavior

- A global filter persists after page navigation. Reset it for every capture
- Put the generator marker in a column that no screen renders
