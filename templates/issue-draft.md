# <defect ID>: <one-line title>

<!-- Issue draft. File it only after a human approves. Do not include real names, real emails, internal hosts or production figures. This file must also pass leakscan before filing. -->

- Class: <docs | product | UX>
- Detail: <code from the table below>
- Journey / step: <scenario id> / <step id>
- Persona: <roster id> (<role>)
- Environment: <product version/commit, install path, browser/OS>

## Reproduction

1.
2.
3.

## Expected

## Actual

## Evidence

- Command and output: `evidence/...` (redacted transcript)
- Screenshot: `shots/NN-slug.png` (sibling text `shots/NN-slug.png.txt`)

## Suggestion

## Detail codes

| Code | Meaning |
|---|---|
| DOC-MISSING-CMD | Only a placeholder; no runnable command |
| DOC-MISSING-STEP | A required step is missing (such as copying an example file) |
| DOC-MISSING-PATH | Another install path or platform is not covered |
| DOC-WRONG-EXAMPLE | The example is fixed to one platform or overwrites other settings |
| DOC-NO-COMPOSE | Adjacent sections do not chain (the earlier section's result cannot feed the next) |
| DOC-ORDER | Step order differs from product behavior |
| DOC-MISMATCH | A value or behavior in the docs differs from reality (port, redirect method, flag) |
| DOC-UNDOCUMENTED | A command or flag the journey needs appears nowhere in the docs |
| DOC-SCOPE | Prerequisites are not stated |
| PROD-SCOPE | A screen element ignores the page's scope (date, permission) |
| PROD-LABEL | The name or label of a different object is shown |
| PROD-DATA | A value is not stored or is always 0, or the screen is empty without a particular record |
| PROD-LEAK | A background component keeps sending identity information |
| UX-TRUNCATE | An email, name or path is truncated |
| UX-STATE | A filter or state persists silently across pages |
| UX-FORMAT | Date or number format differs from the locale, or a raw value is exposed |
| UX-SECRET-LOOKALIKE | Fixed wording looks like a secret |
| UX-DEFAULT-HIDES | A default hides the result the docs say to look at |
