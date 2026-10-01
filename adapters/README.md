# Adapters

The core (`gates/`, `capture/`, `scenarios/`, `fixtures/`, `docs/`) knows no product. Facts that differ per product live in an adapter directory. If a core file seems to need a change for one product, first check whether the difference can be expressed in an adapter.

## Where an adapter lives

An adapter is a directory path. Every tool resolves it with one rule (`gates/adapter_dir.py`); first hit wins:

1. Explicit: `--adapter DIR`, or the `JQA_ADAPTER_DIR` environment variable. A path that is not a directory is an error; the search does not fall through to a lower source.
2. The target project: `<target project>/.journey-qa/adapters/<product>/`.
3. Bundled: `adapters/<product>/` in this skill. Bundled adapters are examples.

Keep your own adapter in the target project (source 2), under version control there. The directory of an installed skill is replaced on every update, so anything added to it is lost. Do not edit a bundled adapter; copy it to the project and change the copy.

Everything an adapter needs is inside its directory, including its `MANIFEST.sha256` (freeze it with the adapter directory as the root). Relative paths between adapter files (`../roster.json` in a scenario, `../policy.json` in `selftest/context.json`) stay valid wherever the directory is. A selftest context that needs the shared test material in this skill writes `${JQA_REPO}` for the skill root (`${JQA_REPO}/gates/selftest/denylist.test.txt`).

To check adapters outside this repository with the repository's checks: `JQA_ADAPTER_DIRS=<dir>[:<dir>...] ./check.sh`. Each directory gets its selftest context, sabotage run, manifest verification, scenario validation, runner syntax check and self-scan, in addition to the bundled `adapters/*`. A listed path that is not a directory fails the run. Paths in an adapter's `self-scan.accepted` are written as `<adapter directory name>/<file>:<line>` for an external adapter and `adapters/<name>/<file>:<line>` for a bundled one.

## What an adapter holds

| File | Content | Required |
|---|---|---|
| `README.md` | Product overview, install path, environment layout, product-specific human gates, open questions | Yes |
| `roster.json` | The roster written in the product's stored shape (roles, providers, plan values) | Yes |
| `policy.json` | Additions to the gate policy: extra rules, secret key names, exemptions (with controls), redactions | When fixed product phrases trip a gate |
| `self-scan.accepted` | Adapter-owned list of accepted findings when `check.sh` scans the adapter's own files. `check.sh` reads the `self-scan.accepted` of every adapter it checks | When the adapter's documents or fixtures trip the self-scan |
| `selftest/` | `context.json`, `controls.tsv`, control fixtures. Covers every policy exemption and extra rule | Yes if `policy.json` or `sql/` exists |
| `scenarios/*.yaml` | Journey scenarios | Yes |
| `selectors.json` | Selector map for run mode: every product selector, plus extra mask regions and text patterns (`runners/README.md`, "Locators") | For run mode |
| `runner/hooks.mjs` | Run-mode hooks: setup items, product assertions, secrets no file provides, cleanup (`runners/README.md`, "Hooks"). Machine-specific values come from a local hook config file, not from the repository | For run mode |
| `sql/identity.sql` | Identity assertions (`gates/sql_assert.py`) | If there is a database |
| `data-contracts.md` | Rules for how charts and tables draw data. What the generator must obey | If synthetic data is used |
| `generator/` | Synthetic data generator (interface in `fixtures/README.md`) | If synthetic data is used |
| `findings-*.md` | Defect list from a previous run, for regression checks | After a run |

## Selftest

```
python3 gates/selftest/run.py gates/selftest "$ADAPTER/selftest"
```

Adapter controls are judged by the same rules as the core. When an adapter adds an exemption, it must add a must-pass control and a must-fail control together. Without them, policy loading fails.

## Creating a new adapter

0. Create the directory `<target project>/.journey-qa/adapters/<product>/` (or any directory you pass with `--adapter`)
1. Read the install and onboarding path in the public documentation and write a draft `scenarios/first-run-*.yaml`. Copy commands from the documentation as they are
2. Find the **stored shape** values of roles, providers and plans in the product code and write `roster.json`. Validate with `fixtures/roster_check.py --allowed-roles ... --allowed-plans ...`
3. If the screens contain a fixed phrase that looks like a secret, add a narrow exemption and controls to `policy.json`. A human approves the exemption
4. Find the identity columns of the database in the schema catalog and write `sql/identity.sql`
5. Fill in `selftest/` and pass it together with the core selftest
