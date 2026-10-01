# Gate specification

The gates in this directory decide by script whether an artifact (record, dump, screenshot, document) may leave the clean environment. Model judgment is reserved for what scripts cannot see (plausibility, meaning). Product-specific parts enter through a policy file (`--policy`) and a roster (`--roster`); the gate code knows no product.

## Common contract

| Item | Rule |
|---|---|
| Language | Python 3.10+ standard library only. External tools: `tesseract` (OCR) and `rsvg-convert` (terminal image rendering) |
| Exit codes | `0` pass, `1` violations found, `2` usage error, unreadable input file, or required tool missing. 2 is never treated as a pass |
| Output | One line per violation, in the form `<RULE> <file>:<line>`. **The matched string itself is never printed.** Denylist entries are not printed either; they are referenced as `DENYLIST[<denylist file name>#<line number>]` |
| Paths | Paths are printed exactly as received. Relative paths are never converted to absolute paths (an operator's home path must not enter the evidence log). If a path component itself contains a denylist entry or a non-allowed email, that component is replaced by `[masked-<first 12 hex chars of sha256>]` in both violation lines and error messages |
| Order | Output is deterministic: sorted by file discovery order, line number, rule order |
| Fail closed | Input that cannot be read or interpreted is a violation (`UNSCANNABLE_*`) or exit code 2, never a pass |

## Input files

### Roster (`--roster`)

Format: `fixtures/roster.schema.json`. The gates use only these values:

- `people[].handle`: OS user names that the home-path rule allows
- `people[].accounts[].account_id`: values the UUID rule allows (accounts are an optional extension; a roster without accounts allows no roster UUIDs)
- `people[].email`, `people[].accounts[].login_email`: used to match roster identities in SQL assertions. Text scanning decides by email domain, so roster emails must also use reserved domains (enforced by `fixtures/roster_check.py`)
- `privileged_roles` (optional top-level list, default `["admin"]`): roles that count as privileged. Used by `sql_assert.py` (kind `privileged_handle`) and `fixtures/roster_check.py`

### Denylist (`--denylist`, repeatable)

One entry per line. Blank lines and lines starting with `#` are ignored. Entries shorter than 4 characters are ignored (false-positive prevention). Entries are real identifiers (person names, handles, emails, hosts, repository names), so **they are not committed to the repository.** One-time secrets first seen during a run (temporary passwords, setup tokens) are added at once to a run denylist, and that denylist is passed to every later gate invocation.

### Policy (`--policy`, repeatable)

`gates/policy.default.json` is always applied first; files passed with `--policy` override it in order. List values are merged; scalar values from later files win.

```json
{
  "allowed_email_domains": ["example.com", "example.org", "example.net"],
  "allowed_email_tlds": ["test", "example", "invalid", "localhost"],
  "allowed_ipv4": ["127.0.0.1", "0.0.0.0"],
  "allowed_ipv4_cidrs": ["192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24"],
  "allowed_home_users": [],
  "secret_key_names": [],
  "secret_key_suffix_regex": "(SECRET|PASSWORD|PASSWD|TOKEN|API_KEY|PRIVATE_KEY)$",
  "redaction_marker": "[REDACTED]",
  "extra_rules": [],
  "exemptions": [],
  "redactions": []
}
```

- `secret_key_names`: exact key names to treat as secrets in addition to the suffix regex. The default is empty; the suffix regex already catches names ending in `SECRET`, `PASSWORD`, `PASSWD`, `TOKEN`, `API_KEY`, `PRIVATE_KEY`. Adapters list product-specific names that do not match the suffixes.
- `extra_rules[]`: `{"id": "ONETIME_TOKEN", "regex": "...(?P<value>\\S+)", "description": "..."}`. If a `value` group exists, a match passes when the value is a redaction marker or placeholder, and is a violation otherwise. Without a group, the match itself is a violation. `id` is uppercase letters, digits and underscores
- `exemptions[]`: see "Exemptions" below
- `redactions[]`: used only by `capture/redact.py`. `{"regex": "...", "replace": "\\1[REDACTED]"}`

## Value judgment (shared)

- **Redaction marker**: the value equals `redaction_marker`, or starts with it and the next character cannot continue a credential (outside alphanumerics and `-_.~+/=`). `[REDACTED]realvalue` is not a redaction marker
- **Placeholder**: `<...>` (angle brackets containing letters, digits, spaces, `_.-`), `${...}`, a variable reference starting with `$(` or `$`, `...`, `…`
- Replacing a document example with `[REDACTED]` to pass a gate is forbidden (it changes the meaning). Use a placeholder

## leakscan.py: text leak scan

```
leakscan.py --roster R --denylist D [--denylist D2 ...] [--policy P ...]
            [--allow-uuids F ...] [--skip-images] [--no-path-scan] [--list-rules]
            TARGET...
```

TARGET is a file or a directory (recursive). No path inside a directory is excluded, and exclusion globs are not supported (an exclusion path lets a stale dump go unscanned; the gate scans everything it is pointed at).

### File classification

| Case | Handling |
|---|---|
| Empty file | Clean |
| Image (`.png .jpg .jpeg .gif .webp`) | Skipped when `--skip-images` is given (image scanning belongs to `image_scan.py`). Otherwise an `UNSCANNED_IMAGE <file>` violation |
| Compressed or container (gzip, zip, bzip2, xz, zstd, 7z, rar, lz4 magic, `%PDF`, zlib stream without magic) | `UNSCANNABLE_COMPRESSED <file>` violation. Decompress to plain text and submit again |
| UTF-16 or UTF-32 with BOM | Decoded with that encoding, then scanned |
| Any other file containing NUL | Read three ways and all are scanned, violations merged: extracted printable runs of length 4 or more, UTF-16LE decode, UTF-16BE decode (also UTF-32 when the NUL layout suggests it). UTF-16 text that is mostly Korean or other CJK cannot be identified by NUL ratio. Line numbers refer to the interpretation that produced the violation |
| Text | Decoded as UTF-8 (`errors="replace"`) |

### Normalization

Before denylist comparison, both entries and body text are NFKC-normalized, default-ignorable code points are removed (zero-width spaces, the combining grapheme joiner U+034F, variation selectors, and so on), runs of whitespace characters (plain space, tab, NBSP, U+3000, and so on) are collapsed to one space, and case is ignored. This catches names decomposed to NFD, names with invisible characters inserted, and names split by whitespace without a line break.

### Rules

| ID | Catches | Passes when |
|---|---|---|
| `DENYLIST` | A denylist entry (substring match after normalization) | Never |
| `EMAIL` | Email shape | The domain equals or is a subdomain of `allowed_email_domains`, or the top-level domain is in `allowed_email_tlds` |
| `EMAIL` (url) | The same check on a copy where `%40` is decoded to `@`. Line shown as `N(url)` | Same |
| `EMAIL` (wrap) | The email fragment at the end of line N joined with the fragment at the start of line N+1 (leading whitespace and the mail quote marker `>` are skipped). Joined only when the fragment at the end of N contains `@` but is not a complete email, or when N+1 starts with `@`. Line shown as `N(wrap)`. The joined string is also checked against the denylist | Same |
| `IPV4` | Four dot-separated octets, each 0-255. Excluded when preceded or followed by a digit or `.` (avoids 5-part version strings), and when the value is `N.0.0.0` right after a capitalized product token and `/` (a browser version in a User-Agent string, such as Chrome/154.0.0.0) | `allowed_ipv4`, `allowed_ipv4_cidrs` |
| `HOMEPATH` | `/Users/<u>`, `/home/<u>` (when the preceding character is not alphanumeric or `_.-`, or when inside a token that starts with `/` and not `//`: `/mnt/c/Users/<u>`, `/var/home/<u>`, `/System/Volumes/Data/Users/<u>`), JSON-escaped `\/Users\/<u>`, Windows `X:\Users\<u>`, dash-encoded `-Users-<u>` and `-home-<u>` (only when preceded by line start, whitespace, `/`, a quote, `=`, `` ` ``, `(`, `[`, `{`, `<`, `:`, `,`, `;`, `|`), and the preceding forms on a copy with `%2F` and `%5C` decoded (line shown as `N(url)`) | `<u>` is a roster `handle` or in `allowed_home_users` |
| `UUID` | 8-4-4-4-12 hex | A roster `account_id`, or a value in an `--allow-uuids` file (case-insensitive) |
| `BEARER` | The word `Bearer`/`bearer` (preceded by a character that is not alphanumeric or hyphen), followed by whitespace (including NBSP) and a value | The value is a redaction marker or placeholder |
| `AUTH_HEADER` | The credential in an `Authorization:` header (case-insensitive; includes `Proxy-Authorization`, JSON and YAML keys, and quoted curl arguments). If a scheme word is present (`Basic`, `Token`, `Digest`, and so on), the credential after it. When the scheme is `Bearer`/`bearer`, `BEARER` decides and this rule is skipped | The first token of the credential is a redaction marker or placeholder |
| `JWT` | `eyJ….….…` | Never |
| `APIKEY` | `sk-`/`sk-ant-` with 20+ characters, `ghp_ gho_ ghu_ ghs_ ghr_` with 30+ characters, `github_pat_`, `xox[abprs]-`, `AKIA` + 16 characters | Never |
| `PRIVATE_KEY` | `-----BEGIN … PRIVATE KEY-----` | Never |
| `SECRETVAR` | `KEY=value` or `KEY: value`, where KEY is in `secret_key_names` or an uppercase key matches `secret_key_suffix_regex` | The value is a redaction marker or placeholder |
| Policy `extra_rules` | The regex defined in the policy | As defined above |
| `UNSCANNED_IMAGE`, `UNSCANNABLE_COMPRESSED` | See the file classification table | Never |

`BEARER` does not match uppercase `BEARER`, so that re-scanning the gate's own output log (`BEARER file:line`) does not produce a false positive.

### Path scan

Unless `--no-path-scan` is given, the `DENYLIST` and `EMAIL` rules are also applied to each file's path string (as received). Output is `<RULE> <file>:path`. This catches real names that appear only in file names.

### `--list-rules`

Prints every rule ID, one per line, and exits 0 (including policy `extra_rules`). The selftest uses it to check that every rule has a must-fail control.

## Exemptions

When a gate catches a fixed product phrase (for example, help text that shows a placeholder such as `tok_…` after `Bearer`), add a narrow exemption to the policy instead of weakening the rule.

```json
{
  "id": "bearer-help-placeholder",
  "rule": "BEARER",
  "match_regex": "^Bearer\\s+tok_(…|\\.\\.\\.)\\.?$",
  "reason": "Fixed placeholder in the help text; not a token",
  "approved": "<reviewer>, YYYY-MM-DD",
  "controls": {
    "must_pass": ["text-bearer-help-placeholder"],
    "must_fail": ["text-bearer-real-after-prefix"]
  }
}
```

- An exemption removes a match of rule `rule` only when the entire matched string fits `match_regex`
- If `controls.must_pass` or `controls.must_fail` lacks at least one entry, policy loading fails (exit code 2)
- The selftest checks that each control appears, with its expected value (PASS/FAIL), in the `controls.tsv` of the same context
- Approving an exemption is a human decision (the human gate in `docs/concepts.md`)

## image_scan.py: image leak scan

```
image_scan.py --roster R --denylist D [...] [--policy P ...] [--allow-uuids F ...]
              [--ocr require|off] TARGET...
```

- PNG only (directories are scanned recursively for `*.png`). Other image formats yield `UNSUPPORTED_IMAGE`
- If the sibling text file `<image>.png.txt` (`.txt` appended, extension not replaced) is missing: `MISSING_SIBLING <png>`
- All `leakscan` rules are applied to the sibling text. Output `<RULE> (sibling) <png>:<line>`
- The same rules are applied to the OCR result (`tesseract <png> stdout`). Output `<RULE> (ocr) <png>:<line>`
- `--ocr require` (default): exit code 2 when `tesseract` is missing. `--ocr off`: skips OCR and writes `NOTE ocr disabled` to standard error. Allowed only when chosen explicitly
- The sibling text is the first line of defense. OCR misses characters at small sizes (below 16 physical px; see `LIMITS.md`)

## png_meta.py: file names and metadata

```
png_meta.py [--name-re REGEX] [--allow-chunk TYPE ...] PNG...
```

| Violation | Condition |
|---|---|
| `NON_ASCII_NAME` | File name contains characters outside printable ASCII |
| `BAD_NAME` | Name rule mismatch. Default `^[0-9]{2}-[a-z0-9]+(-[a-z0-9]+)*(\.light|\.dark)?\.png$` |
| `NOT_PNG` | PNG signature mismatch or damaged chunk structure |
| `METADATA_CHUNK[<type>]` | A chunk other than `IHDR PLTE IDAT IEND tRNS` and `--allow-chunk` (tEXt, iTXt, zTXt, eXIf, iCCP, sRGB, gAMA, cHRM, pHYs, bKGD, tIME, and so on) |
| `TRAILING_DATA` | Bytes remaining after `IEND` |

The decision is made per chunk, so `exiftool` is not needed. The tool that removes chunks is `capture/strip_png.py`.

## docs_images.py: document and image correspondence

```
docs_images.py --pages-root DIR [--page FILE ...] --images-dir DIR [--locale-suffix .<lang> ...]
```

- Pages: every `*.md` under `--pages-root` plus files given with `--page`
- References: Markdown images `![..](path "title")`, `<img src="">`, `<source srcset="">` (each comma-separated candidate). References starting with `http(s):`, `data:`, or `#` are skipped and reported only as an informational `EXTERNAL_REF` on standard output. Anything after `?` or `#` is stripped before resolving
- Path resolution is string-only (normalizing `.` and `..`). The file system is never used to build an absolute path
- `--locale-suffix` takes any suffix, for example `.ja` or `.fr`; each one is checked against its twin pages

| Violation | Condition |
|---|---|
| `BROKEN_REF <page> -> <ref>` | The referenced file does not exist |
| `ORPHAN_IMAGE <png>` | A PNG under `--images-dir` is referenced by no page |
| `LOCALE_MISMATCH <page> <-> <twin>` | `x.md` and `x<suffix>.md` have different reference sets. The following lines print the difference as `  diff: <path>` |

## img_diff.py: baseline image comparison

```
img_diff.py [--threshold 0.01] [--tolerance 24] BASELINE ACTUAL
```

Compares captures from a run with human-approved baseline images. BASELINE and ACTUAL are two PNG files, or two directories whose `*.png` files (recursive) are paired by relative path.

- **Metric**: the share of unmasked pixels whose largest absolute R, G or B channel difference exceeds `--tolerance` (default 24). A pair passes when the share is at or below `--threshold` (default `0.01`, 1%). Alpha is ignored
- **Masks**: `<name>.masks.json` next to either image, `{"viewport": {"dpr": N}, "rects": [{"x", "y", "w", "h"}]}` in CSS pixels; rectangles are scaled by `dpr` and the union of both files applies. A runner writes the file at capture time with the regions that move on their own: chart plot areas, relative and absolute times, durations, live counters (`capture/SPEC.md`)
- **Baselines are per engine.** Different browser engines rasterize fonts differently, and the difference between engines is larger than the default threshold. Compare only captures from the same engine (`baselines/<engine>/<scenario-id>/`); a cross-engine comparison at 1% is meaningless
- PNG decoding uses only the standard library (zlib and the five scanline filters); color types gray, RGB, palette, gray+alpha and RGBA. Interlaced images are not supported

| Violation | Condition |
|---|---|
| `IMAGE_DIFF <actual> ratio=<r>` | The metric is above `--threshold` |
| `SIZE_MISMATCH <actual>` | Dimensions differ from the baseline |
| `FULLY_MASKED <actual>` | No unmasked pixel is left to compare |
| `MISSING_ACTUAL <baseline>` | A baseline image has no capture (directory mode) |
| `NO_BASELINE <actual>` | A capture has no baseline image (directory mode). A new shot needs human approval before it becomes a baseline |
| `NOT_PNG <file>` | Signature, chunk structure, CRC or compressed data damaged |
| `UNSUPPORTED_PNG <file>` | Interlaced, or a bit depth and color type combination outside the list above |

A malformed masks file, an unreadable file or no PNG at all is exit code 2. The ratio is the only number printed; no pixel content is printed.

## sql_assert.py: data assertions

```
sql_assert.py --engine sqlite --db FILE  [--roster R] ASSERT.sql...
sql_assert.py --engine psql   --dsn DSN  [--roster R] [--timeout 60s] ASSERT.sql...
```

An assertion file is divided into blocks by `-- assert: <name>` lines. Each block is one SELECT and **passes only when it returns 0 rows**.

- With `--roster`, the CTE `roster_identity(kind, value)` is prepended to each assertion. Kinds: `handle`, `email`, `login_email`, `account_id`, `provider_account` (value is `provider|account_id`), `privileged_handle` (handles whose role is in the roster's `privileged_roles`, default `["admin"]`)
- Execution form: `WITH roster_identity(kind, value) AS (VALUES ...) SELECT count(*) FROM (<block SQL>) AS q`
- sqlite is opened with a read-only URI (`mode=ro`). psql runs `BEGIN READ ONLY`, `SET LOCAL statement_timeout`, and ends with `ROLLBACK`
- Output `ASSERT_FAIL <name> rows=<n>`. **Row contents are not printed** (they may contain identifiers)
- A file with no blocks exits with code 2

## adapter_dir.py: adapter resolution

```
adapter_dir.py [--product NAME] [--adapter DIR] [--project DIR] [--why]
```

- One rule for every tool: explicit `--adapter DIR`, else `JQA_ADAPTER_DIR`; then `<project>/.journey-qa/adapters/<product>/` (project defaults to the current directory); then the bundled `adapters/<product>/`
- An explicit path that is not a directory exits 2; resolution never falls through to a lower source after an explicit choice
- A product name must match `^[A-Za-z0-9][A-Za-z0-9._-]*$` (no path separators), otherwise exit 2
- Prints the absolute path on stdout; `--why` names the source on stderr. Exit 0 resolved, 2 anything else

## manifest.py: gate freeze

```
manifest.py freeze --root DIR --out MANIFEST.sha256 [--exclude GLOB ...]
manifest.py verify --manifest MANIFEST.sha256 [--exclude GLOB ...]
```

- `freeze`: records the sha256 of every file under `--root`, in sorted relative-path order. The manifest file itself is excluded
- `verify`: reports `MODIFIED <path>`, `MISSING <path>`, `UNLISTED <path>` (a new file not in the list) as violations
- The frozen scope is `gates/`, `capture/`, `runners/` and each adapter directory (wherever it is kept), each with a `MANIFEST.sha256` at its root. Only the top-level description documents (`SPEC.md`, `LIMITS.md`, `README.md`, `data-contracts.md`, `findings-*.md`, `VERIFY.md`) and `capture/sample/` are removed with `--exclude`. `--exclude` matches the full relative path with fnmatch and `*` also crosses `/`, so a pattern like `*.md` would also drop document fixtures. An adapter's policy, controls, SQL and scenarios are frozen too
- After freezing, an execution agent cannot edit a gate or policy to make it pass. A change requires main-session approval, a selftest rerun, then a refreeze, in that order
- Documents that are updated continuously (`LIMITS.md`) are removed with `--exclude`

## selftest/run.py: selftest

```
selftest/run.py [CONTEXT_DIR ...] [--sabotage]
```

Each CONTEXT_DIR (default `gates/selftest`) contains `context.json` and `controls.tsv`.

```json
{ "roster": "roster.test.json", "denylists": ["denylist.test.txt"],
  "policies": [], "sql_schema": "fixtures/sql/schema.sql",
  "sql_asserts": ["../sql/identity.example.sql"] }
```

`controls.tsv` columns: `control  gate  fixture  expected` (tab-separated, first line is the header). Values for gate:

| gate | Execution |
|---|---|
| `leakscan` | Scans the fixture with the context's roster, denylists and policies |
| `leakscan-allow` | Same, and adds `<fixture>.allow` as `--allow-uuids` |
| `image_scan` | `--ocr require` |
| `png_meta` | Default name rule |
| `docs_images` | The fixture directory as `--pages-root <fx>/docs`, `--page <fx>/README.md` (if present), `--images-dir <fx>/docs/assets/screenshots`, and `--locale-suffix` for each locale twin present in the fixture (`.ko` and `.ja` in the core selftest) |
| `sql_assert` | Loads `sql_schema` and the fixture (seed SQL) into a temporary sqlite DB, then runs `sql_asserts` |
| `redact` | Redacts the fixture with `capture/redact.py`, then runs `leakscan` on the result. Confirms the redaction rules actually work |
| `img_diff` | `img_diff.py <fx>/baseline <fx>/actual`, plus one extra argument per line of `<fx>/args.txt` when present |
| `strip_png` | Strips a temporary copy of the fixture PNG with `capture/strip_png.py`, then runs `png_meta` on the copy. PASS proves stripping removes what `png_meta` rejects (for example an ICC profile) |
| `evidence` | Runs `capture/evidence.py` on a temporary copy of `<fx>/evidence`, with `<fx>/secrets.txt` as `--secrets` when present and one extra argument per line of `<fx>/args.txt` |

Verdict: exit code 0 is PASS, 1 is FAIL, anything else is ERROR. ERROR can never equal an expected value, so it is always a MISMATCH (a broken gate cannot pass itself off as "caught").

Additional checks (each failure prints one MISMATCH line):

1. Rule coverage: for every ID from `leakscan --list-rules`, at least one `leakscan`/`leakscan-allow` control with `expected=FAIL` exists across all contexts (a control counts when its name starts with `text-<rule id lowercased, _ replaced by ->`)
2. Gate coverage: each gate kind has at least one PASS control and one FAIL control
3. Exemption controls: for every exemption in a context's policy, each `must_pass` is in the same `controls.tsv` with `expected=PASS` and each `must_fail` with `expected=FAIL`

Output: `<control> expected=<E> actual=<A> OK|MISMATCH` per control, then `controls: N, mismatches: M`. N of 0 is a failure. Exit code 0 only when M=0 and N>0.

`--sabotage`: copies the gates to a temporary directory, replaces each gate with a fake that always exits 0, and runs the same controls. Each gate must produce at least one MISMATCH (this proves the selftest does not trust the gates and actually checks them). The gate location is overridden with the environment variable `JQA_GATES_DIR`.

## capture/ tools

| Tool | Behavior |
|---|---|
| `strip_png.py IN [OUT]` | Rewrites keeping only `IHDR PLTE IDAT IEND tRNS`. Removes bytes after `IEND`. Re-checks the result with the chunk check of `png_meta` and prints the sha256 |
| `redact.py FILE [--policy P ...]` | Writes a redacted copy to standard output. Rules: the whole credential after an `Authorization:` header (any scheme; the scheme word is kept), the value after a standalone `Bearer` (case-insensitive), JWTs, `APIKEY` shapes, whole `PRIVATE_KEY` blocks, `SECRETVAR` keys (both `=` and `:` forms), `password:` and `passphrase:` prompt values, policy `redactions[]`. The result must pass `leakscan` |
| `evidence.py DIR --roster R --denylist D [...] [--policy P ...] [--secrets F] [--drop GLOB ...] [--ocr require\|off]` | Redacts and gates a failure-evidence directory, and keeps it only if it passes. Unpacks archives; deletes files no gate can scan (JPEG, GIF, WebP, video, fonts, PNGs inside archives) and files whose relative path matches a `--drop` glob (an engine's records of the runner's own source locations); strips top-level PNGs; replaces every `--secrets` entry in raw form and in nested JSON escaping and URL encoding (up to three levels); applies `redact.py`; then runs `leakscan --skip-images`, `png_meta` and `image_scan` with the `--secrets` entries added to the denylists. On a pass the archives are packed again from the scanned files; on any violation or gate error the directory is deleted (`DISCARDED <DIR>`). Exit 0 kept, 1 violation, 2 error |
| `render_terminal.py TRANSCRIPT OUT.png [--cols 100]` | Draws the redacted transcript as a monospace SVG, rasterizes it with `rsvg-convert -z 2`, runs `strip_png`, and saves a copy of the transcript as the sibling `OUT.png.txt`. Requires the font "DejaVu Sans Mono" (the character-width calculation is specific to it). Exit code 2 when `rsvg-convert` is missing |
