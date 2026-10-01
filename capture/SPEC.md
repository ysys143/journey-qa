# Capture specification

Applies to every screenshot and terminal image. The gates (`gates/png_meta.py`, `gates/image_scan.py`) enforce names and metadata mechanically; the rest is a checklist for whoever captures. Values are set per product in the adapter and pinned in the scenario file's `determinism`.

## Browser screenshots

| Item | Default | Reason |
|---|---|---|
| Viewport | 1440x900 CSS px | Wide enough that typical documentation body widths are not clipped |
| Scale | 2 (physical 2880x1800) | Scale 1 renders soft in documents, and OCR accuracy on small text is lower |
| Region | Page area only | Exclude the address bar, tabs and OS window frame. If cropping is needed, crop before metadata removal |
| Theme | If the product has a docs site, match its theme. Where a README is shown in both light and dark modes, provide two sets | Capture the dark version separately with `prefers-color-scheme: dark` rendering. Never invert a light image |
| Locale and timezone | Pinned in the scenario | Unpinned values mix another locale's date format into the screen |
| Paths | Pass absolute paths to the capture tool; use paths relative to the run directory in reports, sibling text and logs | Tools resolve relative paths against their own working directory and can save to an unexpected place. An absolute path in evidence exposes the operator's home name |

Explore mode may use any browser automation the agent has; run mode uses the runner's engines (`docs/engines.md`). When a tool fails, follow the tool-failure section of `docs/workflow.md` (prove it with an isolated test).

## Screen state before capture

- Apply the shot list's screen state (filters, sort, scroll position, expanded panels, theme) and wait until the specified selector is visible. Do not use fixed-time waits
- Some products keep a global filter across page navigation. Set the state explicitly again for every capture
- **Truncation check**: for elements that must not be cut off (emails, names, paths), measure `scrollWidth > clientWidth`. If truncated, choose a screen that is not truncated or record a defect. Truncated text is also read wrongly by OCR and produces false positives
- Empty-state check: a large empty span at the end of a chart, an empty KPI or an empty detail screen indicates the data's reference time is stale. Move the time anchor to the present right after generating data and record the capture window (how long after generation)
- For a screen that shows a secret (a temporary password, for example), add the value to the run denylist before capture. Do not dump page text to the screen at that step. Invalidate the secret within the same step and confirm it is invalid (for example, a 401)

## Naming

Default (`gates/png_meta.py`):

```
^[0-9]{2}-[a-z0-9]+(-[a-z0-9]+)*(\.light|\.dark)?\.png$
```

- A two-digit step number (journey order), a lowercase hyphenated slug, and `.light`/`.dark` only for README images
- The whole name is printable ASCII
- Real names and handles must not appear in file names. The leak scan checks paths too

## Sibling text

Next to every `NN-slug.png`, place `NN-slug.png.txt` (appended, extension not replaced).

- Save `document.body.innerText` (or the tool's "visible text") at the **same moment** as the screenshot. Do not re-extract it later or build it with OCR
- It is literal text, so it also catches small text that OCR misses. It is the effective first line of defense (`gates/LIMITS.md`)
- Sibling text is never exported. It stays in the run directory

## Metadata removal and hash

```
python3 capture/strip_png.py <file>      # keeps only IHDR PLTE IDAT IEND tRNS, prints sha256
python3 gates/png_meta.py <file>         # name + chunk check
```

Tag-based removal (`exiftool -all=`) can leave chunks that a rasterizer (bKGD) or a resizing tool (cHRM) adds. Rewriting from a per-chunk allowlist does not depend on such tool differences. Compute the hash **after** removal. Screenshots taken from a real display carry an ICC profile (`iCCP`) that names the display's maker and model; the allowlist drops it with every other metadata chunk. Strip every PNG a run writes, failure screenshots included.

## Masks and baseline comparison

A run in run mode compares each capture with a human-approved baseline image of the same engine using `gates/img_diff.py` (metric, threshold and violations in `gates/SPEC.md`, "img_diff.py").

- At capture time, next to `NN-slug.png`, write `NN-slug.masks.json`: the rectangles (CSS px, viewport-relative, with the device pixel ratio) of regions that change without a product change. Collect them from the DOM in the same moment as the screenshot: chart plot areas, relative times ("5m ago"), clock times and dates, durations, live counters, and any region the adapter adds
- Keep masks narrow. A mask hides defects too, and a fully masked image fails the comparison
- Baselines live per engine: `baselines/<engine>/<scenario-id>/NN-slug.png`. Fonts rasterize differently across engines and headed or headless modes, so a baseline approved on one engine does not judge captures from another
- Above the threshold, the run calls an agent (or a person) to judge the difference; it does not adjust the threshold

## Failure evidence

When a step fails, a runner collects evidence: a screenshot with its sibling text, and whatever the engine offers (page HTML, console log, accessibility snapshot, a browser trace archive, an event log). Raw engine evidence can contain request bodies, cookies and typed values, including passwords, so it is never kept as recorded.

1. Before collecting, clear password inputs on the page and mask any one-time secret still displayed; register every value found as a run secret, and so are the session cookie values of the run's login states (request headers in traces and logs carry them)
2. Record no video: no gate can scan it
3. Reduce raw event logs to method, path and status
4. Run `capture/evidence.py <dir> --roster R --denylist D [--policy P] --secrets <run denylist>`. It unpacks archives, drops files no gate can scan, strips PNGs, redacts run secrets (raw, JSON-escaped, URL-encoded, nested) and applies `redact.py`, then gates the result with `leakscan`, `png_meta` and `image_scan`
5. The evidence is kept only when that command exits 0. Any other exit deletes the directory, and the run record says the evidence was discarded

## Terminal images

Terminal steps always appear in documents as code blocks of the real output, and only key steps get an image added. The image is made by **drawing the recorded output text**, not by photographing a real terminal window, so the text in the image is the text that was scanned.

1. **Record**: keep the output of the real run as plain text (`script`, `tee`, a pty driver). The raw file goes in `secrets/`
2. **Redact**: `python3 capture/redact.py raw.txt [--policy P] > redacted.txt`
3. **Scan**: run `gates/leakscan.py` on the redacted transcript. On failure, fix the redaction rules or the source data. Do not widen the gate
4. **Run denylist**: add one-time values with no fixed pattern (temporary passwords, tokens typed at a prompt) to the run denylist as soon as they are observed, and pass it with `--denylist` to every later gate invocation
5. **Render**: `python3 capture/render_terminal.py redacted.txt NN-slug.png` (writes the sibling text too). The renderer requires the font "DejaVu Sans Mono"; its character-width calculation is specific to that font, so another monospace font misaligns the text
6. **Image scan**: `png_meta.py`, `image_scan.py`

Make environment values such as host names match the placeholder values used in the document body. If the image and the body disagree, the reader is confused.

## Judgment after capture

Even when the script gates pass, the judge opens every image and describes it one by one. The judge looks for what scripts cannot see:

- Content that contradicts itself within a screen (for example, an action log that says a step succeeded next to output showing it failed, or a summary that disagrees with the rows above it)
- Date formats of a different locale
- A generator marker visible on the screen
- A truncated email
- A large empty span in a chart, or a meter stuck at 0% or 100%
- A shape that differs from the production reference (isolated points and spikes, weekends at the same scale as weekdays)

The judging criteria are in `fixtures/realism-checklist.md`.
