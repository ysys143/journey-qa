# Synthetic data

Capturing the screens of a product in use (aggregates over several users, charts, permission scopes) needs data. Real data with only personal information removed is not used. Data is generated fresh from two inputs: the roster and a reference shape.

Passing every numeric gate does not mean the data looks real. Generated data can satisfy all counts and ranges and still render as an obvious fake: totals at a fraction of real scale, records that contradict each other, charts that draw only isolated points. The order below is fixed for that reason: compare against a reference first, then read the chart code to learn what the screens actually require of the data.

## Order

1. **Reference capture**: look at the same screens of the real product (step 0 of `docs/workflow.md`). Keep in the repository only the reference shape, filled in from `reference-shape.template.md`
2. **Read the data contract**: check in the code how chart and table components draw the data, and write it in the adapter's `data-contracts.md`
3. **Fix the roster**: `roster_check.py` passes
4. **Generate**: a generator that follows the interface below (adapter-owned)
5. **Offline verification**: before loading, compare the generated output with the reference shape's ranges and with the contract
6. **Load**: into the instance used for the operating screens only (if operating screens need demo data, this is a separate instance from the clean-install instance). Leave derived tables empty and let the product's backfill recompute them
7. **Post-load verification**: `gates/sql_assert.py` (identity assertions), `gates/leakscan.py` on a plain-text dump, regenerate the operational-UUID allowlist, empty-state check of every screen
8. **Judgment**: the judge places the images beside the reference and reviews them with `realism-checklist.md`

## Generator interface

The generator is written per product by the adapter (`adapters/<name>/generator/`). The contract:

```
generate --roster ROSTER.json --shape SHAPE.json --seed N --anchor ISO8601 --days D --out DIR
```

| Item | Rule |
|---|---|
| Input | The roster, a numbers-only shape file (percentiles, hour-of-day weights, ratios), a seed, an anchor time, a duration. Production source rows are not an input |
| Determinism | The same input gives the same bytes. Random IDs also come from the seeded RNG (no OS-random `uuid4()`) |
| Output | A bulk-load format (for example PostgreSQL `COPY`). Row-by-row INSERT is slow at hundreds of thousands of rows |
| Statistics | `DIR/stats.json`: row counts per table, per-person and per-day totals, active-minute ratios, and other values the offline verification uses |
| Generator marker | Leave a marker on every generated free-text row, in a **column that no screen renders**. A marker that renders on screen is a defect |
| New values | Identifiers, projects, repositories, branches and conversation content are generated fresh. No mapping back to the source is kept |
| Derived tables | Left empty. The product's own backfill recomputes them. The backfill marker distinguishes "needs recomputation" from "one-time repair" |
| Time re-anchor | A `reanchor` tool moves the times to the present right before capture. This prevents a retention policy from deleting old data, or a "last 3 hours" view from being empty |

## Contract checklist

Items to check in the product's chart and table code. Each product may or may not have them; adapt per product.

- Whether the chart zero-fills gaps (for example, empty minutes become 0). If so, values must exist in nearly every minute inside an activity span to form a continuous band: use short request intervals and overlap or chain sessions
- Whether lines break after N minutes without samples. If so, samples must arrive at the cadence the client really polls. If one poll fills several windows, stamp them with the same time
- Whether a price table has keys that roster values must match. If a price calculation looks up specific keys, the roster's plan values must be the stored form of those keys
- Current vocabulary: use current model and product names, and do not use a model on a date before the date its price applies (the price computes as 0)
- Cumulative counters: whether they must increase monotonically within a session, and whether a record identical to the previous one is discarded as a duplicate
- Record kinds the rendering needs: whether a detail screen is empty without a particular record kind. Read the product code to confirm, and write such findings down separately as documentation-defect or product-bug candidates

## Files

| File | Content |
|---|---|
| `roster.schema.json` | Roster format |
| `roster.example.json` | Example roster (five fictional people). Reuse only the shape; invent new values for each product |
| `roster_check.py` | Roster validation |
| `reference-shape.template.md` | Template for describing the production reference shape |
| `realism-checklist.md` | Judge's checklist |
