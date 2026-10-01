# Sample terminal image verification record

A record of running the `capture/` tool flow (record, redact, scan, render, image scan) once end to end. The transcript is invented; people, emails, handles, paths and account IDs use only values from `fixtures/roster.example.json`. All secret values are fakes of the `fake-fixture` form. The IP is in a documentation range (192.0.2.0/24).

The sample is rendered at `--scale 1` to keep the repository small. Real captures keep the default scale of 2 (`capture/SPEC.md`). The transcript holds only the lines that show redaction: an exported secret variable, an authorization header, two `.env` values and a typed password.

Run from the repository root. Tools used: Python 3, tesseract, rsvg-convert. Hashes and image sizes below were measured on one machine and can differ on another (font and rasterizer differences).

| File | Content |
|---|---|
| `01-sample-terminal.raw.txt` | Transcript before redaction. Kept as proof that redaction is needed, because leakscan catches it (section 1). In real operation the raw file goes in `secrets/` (`capture/SPEC.md`) |
| `01-sample-terminal.txt` | `redact.py` output |
| `01-sample-terminal.png` | `render_terminal.py --scale 1 --cols 126` output (1252x168, 49471 bytes, metadata removed) |
| `01-sample-terminal.png.txt` | Sibling text. Byte-identical to `01-sample-terminal.txt` |

## 1. The raw transcript is caught by leakscan

```
$ python3 gates/leakscan.py --roster fixtures/roster.example.json --denylist gates/selftest/denylist.test.txt capture/sample/01-sample-terminal.raw.txt
SECRETVAR capture/sample/01-sample-terminal.raw.txt:1
BEARER capture/sample/01-sample-terminal.raw.txt:2
SECRETVAR capture/sample/01-sample-terminal.raw.txt:4
APIKEY capture/sample/01-sample-terminal.raw.txt:5
SECRETVAR capture/sample/01-sample-terminal.raw.txt:5
[exit 1]
```

The password prompt value on line 6 has a shape that no leakscan rule covers, so it is not in the list above. `redact.py` redacts it.

## 2. After redaction it passes

```
$ python3 capture/redact.py capture/sample/01-sample-terminal.raw.txt > capture/sample/01-sample-terminal.txt
[exit 0]
$ python3 gates/leakscan.py --roster fixtures/roster.example.json --denylist gates/selftest/denylist.test.txt capture/sample/01-sample-terminal.txt
[exit 0]
```

## 3. Render and metadata

```
$ python3 capture/render_terminal.py --scale 1 --cols 126 capture/sample/01-sample-terminal.txt capture/sample/01-sample-terminal.png
sha256 d34edcb786a4d1efdc233f171a309ba5ceaf65a388fe42e6127eec302a6588b2 capture/sample/01-sample-terminal.png
sibling capture/sample/01-sample-terminal.png.txt
[exit 0]
$ python3 gates/png_meta.py capture/sample/01-sample-terminal.png
[exit 0]
$ cmp capture/sample/01-sample-terminal.txt capture/sample/01-sample-terminal.png.txt
[exit 0]
```

Rendering the same input twice gives the same sha256 on the same machine.

## 4. image_scan

```
$ python3 gates/image_scan.py --roster fixtures/roster.example.json --denylist gates/selftest/denylist.test.txt capture/sample/01-sample-terminal.png
[exit 0]
$ python3 gates/image_scan.py --roster fixtures/roster.example.json --denylist gates/selftest/denylist.test.txt --ocr off capture/sample/01-sample-terminal.png
NOTE ocr disabled
[exit 0]
```

At `--scale 1` this transcript passes with OCR on. This is a property of this rendering, not a fix of the gate: the same transcript rendered at `--scale 2` still produced `SECRETVAR (ocr)` on line 6 in the check made for this record (tesseract misreads the redaction marker next to an underscore key name). Real captures use scale 2, so the false positive documented in `gates/LIMITS.md` still applies to them. The gate was not widened.
