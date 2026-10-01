# Realism checklist

The judge (read-only, strong model tier) opens every demo image and checks the items below one by one. Data that passes the numeric gates can still fail here. Answer each item with pass / fail / not applicable and a one-line reason.

Items marked "adapt per product" assume a product with the stated domain features; rewrite or drop them for a product without those features.

## Shape against the reference

- [ ] Placed beside the reference screen, is it the same kind of shape (a continuous band, hills, a weekly rhythm)? Is it more than isolated points and spikes?
- [ ] Is single-person scale inside the reference's single-person range? Is the team total inside the headcount-proportional range?
- [ ] Are weekends and holidays distinguishable from weekday scale? Is there no large activity at night?
- [ ] Does the shortest time range (for example, the last 3 hours) show more than one user, model or provider? (adapt per product)
- [ ] Is no meter or ratio (for example, period quota usage) stuck at 0% or 100%? (adapt per product)

## Screen state

- [ ] Is there no large empty span at the end or start of a chart (a sign the time anchor is stale)?
- [ ] After re-anchoring, does a freshness check compare the newest event against the generator's real tail? A generator can end its data some minutes before the anchor, so "newest event within N minutes of now" fails on every run while the screens are fine. Measure the tail gap the generator leaves and assert against it, or make the generator fill the gap up to the anchor
- [ ] Are there no empty KPIs, empty detail screens or empty lists? If there are, is the empty state intended?
- [ ] Are emails, names and paths untruncated?
- [ ] Do date and number formats match the scenario's locale?
- [ ] Is no generator marker visible anywhere on screen?
- [ ] Do figures agree across screens (per-user totals and team total, list and detail, cost and volume)?

## Internal consistency of content

- [ ] Are records and logs internally consistent? Examples to check (adapt per product): the language of a file read matches the language of the code shown; a step that claims "fixed" has a fixing step; a summary does not say "passed" after a failing test
- [ ] Do project, repository and branch names suit that person's team?
- [ ] Are the usage patterns of the personas distinguishable from each other (volume, main feature, tools)?

## Leaks

- [ ] Is every name, email, host and path in the images on the roster (confirm by eye even when the script gates passed)?
- [ ] If any image still shows a one-time secret (a temporary password, a token), was it raised to the human gate?

## Image quality

- [ ] Is it the scale from the capture specification (scale 1 renders soft)?
- [ ] Is the browser window frame excluded?
- [ ] Do the light and dark sets (when both are required) use the same data and composition?

Record the verdict per image, with a one-line description, in the format of `templates/gate-verdict.schema.json`.
