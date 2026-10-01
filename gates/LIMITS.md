# Gate limits

What the gates miss and what they flag wrongly, as measured. Generalities that were not measured are not listed. Numbers were measured on a development workstation; re-measure with the selftest (`gates/selftest/run.py`) on your own machine. This file is updated continuously, so it is excluded from the manifest (`manifest.py --exclude LIMITS.md`).

## image_scan.py (OCR)

Fixtures are black DejaVu Sans Mono text on a white background, drawn as SVG, rasterized with `rsvg-convert -z <zoom>`, then cleaned with `strip_png`. The sentence is "Invite sent to " followed by an email on a fake domain.

- OCR accuracy depends on text size and is not monotonic. At zoom 1: 6px and 7px produce an empty result; 8, 9, 10 and 12px read `@` as another character so the email shape breaks; 11px and 14px read correctly. At zoom 2, 6 to 14px all read `@` and the domain. Do not rely on OCR alone.
- 11px at zoom 1 and zoom 2 (`selftest/fixtures/image/small-email-zoom1`): the OCR engine inserts one space inside the local part but reads `@` and the domain correctly, so both `EMAIL (sibling)` and `EMAIL (ocr)` fire.
- 9px at zoom 1 (`image/tiny-email-zoom1`): OCR misses, only the sibling text catches it as `EMAIL (sibling)`. This fixture pins the SPEC position that the sibling text is the first line of defense.
- 40px name (`image/ocr-denylist-big`): OCR reads the name exactly and catches it as `DENYLIST (ocr)` even when the sibling text lacks it.
- Accepted false positive: on a dark background at 16px and zoom 2 (`capture/sample/01-sample-terminal.png`), OCR reads the underscore in a key name as a space and puts a space before the closing bracket of the redaction marker, which produces a `SECRETVAR (ocr)` false positive. The sibling text and the redacted transcript pass. The direction is fail closed, so the gate was not widened; a render of the sample transcript at zoom 2 does not pass `image_scan`, while the committed sample, rendered at zoom 1, does (see `capture/sample/VERIFY.md`).

## png_meta.py and capture/strip_png.py

- Tools that strip metadata with a tag-based approach (for example `exiftool -all=`) leave chunks that the rasterizer or a resizing tool adds (bKGD, cHRM), and re-encoding tools can add others (eXIf, sRGB). `strip_png.py` rewrites the file from an allowlist of chunks and `png_meta.py` decides per chunk, so neither depends on such tool differences.
- These checks look at file names and chunk structure only; image content is checked by `image_scan.py`.

## leakscan.py

### What it does not catch

Each item below was confirmed by constructing an input and running the gate (exit code 0 = not caught).

- A name split across lines: when the words of a denylist name are on different lines, `DENYLIST` does not fire. Line joining applies only to email fragments (as in SPEC).
- Emails written as HTML entities (`&#64;`) or wrapped in base64.
- A YAML password value under a lowercase key: `SECRETVAR` looks only at uppercase keys (as in SPEC). `redact.py` redacts password-prompt shapes, but `leakscan` does not judge them.
- Line N ends with a complete email on an allowed domain and line N+1 continues that domain (a line ending in `mail.test` followed by a line starting with `corp-probe.org`): exit code 0. This is kept because of the rule that a fragment that is already a complete email is not joined, and the pass control that pins it (`pass-email-wrap-complete-not-joined`).
- Paths under lowercase `/users/`: exit code 0. They cannot be told apart from REST paths (`/api/users/42` and so on), so only uppercase `Users` is checked.
- CamelCase JSON keys (the value after `"apiKey":`): exit code 0. `SECRETVAR` looks only at uppercase keys.
- Lowercase keys (the value after `db_password:`): exit code 0. Same reason.
- A hyphen-joined name (the denylist has the name split by a space, the body has a hyphen): exit code 0.
- A 32-digit UUID without hyphens: exit code 0.
- IPv6 addresses: no rule.
- A raw deflate stream without a zlib header (an email inside): exit code 0. It has neither magic nor zlib header, so it is not recognized as compressed, and printable-run extraction cannot read it.
- One-time values without a fixed prefix (temporary passwords, tokens typed at a prompt): caught only by the run denylist (`capture/SPEC.md`).
- A real value inside placeholder angle brackets (`Bearer` followed by `<`, an alphanumeric string that looks like a token, and `>`): exit code 0. This follows the SPEC rule that angle brackets containing letters, digits, spaces and `_.-` are a placeholder. Accepted limit.

### Accepted false positives

- A 4-part version string with three dots (each part 0 to 255) cannot be told apart from IPv4 and is caught as `IPV4`. 3-part and 5-or-more-part strings pass.
- A file name with a retina scale suffix (`@2x.png` right after the name) reads png as a top-level domain and is caught as `EMAIL`.
- Prose with lowercase `bearer` followed by an ordinary word is caught as `BEARER`. Documents use a placeholder such as `<token>`.
- System directories such as linuxbrew under `/home/` or Shared under `/Users/` are caught as `HOMEPATH`. Add them to policy `allowed_home_users` when needed.
- Prose where `Authorization:` is followed by an ordinary word that is not a credential is caught as `AUTH_HEADER` (measured: a one-line example exits 1). Documents use a placeholder such as `<credentials>`.
- An angle-bracket placeholder with a space in the value position of an `Authorization` header (for example, `base64 user:pass` wrapped in angle brackets after Basic) is caught as `AUTH_HEADER`, because only the first token, `<base64`, is treated as the value (measured: exit code 1). `BEARER` behaves the same way. Write placeholders without spaces (`<base64-user-pass>`).
- The OCR redaction-marker false positive described under `image_scan.py`.

### Scan time

- Synthetic log of 52,428,922 bytes (377,547 lines, five kinds of repeated lines, three violations at the end): about 7 to 10 seconds per run; all three violations reported with exact line numbers. A 10 MB binary file containing NUL, read three ways: about 5 seconds.
- Header values are truncated at 4096 characters. Without truncation, a repeated header forming one 10 MB line took over 9 minutes.
- Design: most rules start with a literal so the search is linear. Rules that swallow long runs (`SECRETVAR`, `sk-`, `gh*_`) keep their look-behind inside the regex so they stay linear; a naive search on one 10 MB line of repeated `A_` was quadratic and did not finish in 2 minutes.
- Eight kinds of 10 MB pathological input (one base64 line, repeated `1.`, repeated `A_`, repeated `a@`, repeated `/home/`, repeated `-home-`, repeated `xsk-`, 120,000 lines of 80 characters of `a@`): 0.9 to 2.7 seconds each.
- When changing a rule's regex for speed, compare outputs between the old and new implementations on all fixtures and on a few thousand random strings. One difference was observed: when url decoding changes only the local part of an already-caught email, the older implementation emitted one extra `N(url)` line. The violation decision was the same.

## docs_images.py

- References are resolved as strings only. External references (`http(s):`, `data:`, `#`) are reported but not checked.
- Locale parity compares reference sets, not image contents or translated text.

## sql_assert.py

- The sqlite engine opens the database read-only. The psql engine was checked against a temporary PostgreSQL cluster: the same seeds gave the same verdicts as sqlite; with `--timeout 500ms` a 3-second query ended with exit code 2 (statement timeout); a data-modifying CTE was rejected by PostgreSQL at the subquery position and ended with exit code 2.
- Assertions return only row counts, so a failing assertion tells which assertion failed but not which row.

## Selftest

- `selftest/run.py` (core): the run takes a few seconds; `--sabotage` takes about 20 seconds and reports DETECTED for every gate.
- `selftest/gen_fixtures.py --check`: generated files are compared byte for byte with the tree. Byte equality holds on a machine with the same zlib, `rsvg-convert` and font; on another machine the OCR PNGs can differ because of font differences.
