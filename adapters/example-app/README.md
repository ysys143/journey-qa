# example-app adapter

A worked example adapter for **example-app**, an invented product. Nothing here describes a real product; every command, table, key and value was made up so the adapter can exercise every core mechanism (policy rules and exemptions, identity SQL, scenarios, selector map, run-mode hooks). Copy its shape when writing an adapter for a real product, and replace every fact.

## Product

example-app is a self-hosted team web app: a server (`exampled`, PostgreSQL) with a dashboard, and a CLI client (`exampleapp`) on each member's machine. The client reports usage of external provider accounts (`provider-a`, `provider-b`), each on a plan, and the dashboard charts usage per user and per plan.

| Fact | Effect on the journey |
|---|---|
| The public install path builds the images from source with Docker Compose. There is no registry | Builds take long. The clean-room check includes the image list |
| The install source is a public mirror tree, not the internal repository. Real users clone it | Inside the clean environment, run `git init` and commit the exported tree so it matches a clone. A missing `.git` is not a documentation defect |
| With zero users, the server log prints a one-time setup token | Add it to the run denylist before capture. The `SETUP_TOKEN` rule in `policy.json` |
| There is no separate `login` command. `exampleapp init` signs in and creates the local profile together | The client step of the onboarding journey is `init` alone |
| A new user changes the temporary password in the dashboard, then enters the changed password in `init` | Step order: the password change comes before `init` |
| The admin created by `/setup` has an empty team and user ID and cannot use the client until edited | A first-run journey needs an admin-edit step afterward |
| The identity keys (`user_id`, `profile_email`, `login_email`, `account_id`) have no foreign keys; they are joined as strings | Identity replacement and checks are per table. Collect targets from the schema catalog |
| The docs site has only a light theme; the README has light and dark | The theme rule in the capture specification |

## Environment

| Role | Layout |
|---|---|
| Server | A fresh isolated environment (a VM or a disposable host) with only Docker installed. Two compose projects in it: A (reproduces the install from an empty state) and B (synthetic data for the operating screens). Host ports differ from the defaults |
| Client | A fresh isolated environment separate from the server. Its own `HOME`; the user's full name and home directory name are set explicitly to roster values. A real provider-account login happens only here, by a human |
| Reconstruction workspace | A temporary database on the host. Delete the volume after the work |
| Capture | A browser with a fixed viewport at scale 2 |

A local development stack may already use the default ports (8080, 5432). Use other ports and record where they differ from the documentation.

## Product-specific human gates

- Real provider-account login in the client environment
- Confirming the scope in which real account displays are replaced by roster values
- Production dashboard reference capture (uses a production login session)
- Public mirror export

## Files

| File | Content |
|---|---|
| `roster.json` | Five fictional people, `provider` (`provider-a`, `provider-b`) and the stored shape of plan keys (`plan-large`, `plan-medium`, `plan-pro`, `plan-plus`, `plan-basic`) |
| `policy.json` | Setup-token rule, secret key names (`JWT_SECRET`, `DB_PASSWORD`, `POSTGRES_PASSWORD`, `EXAMPLEAPP_SETUP_TOKEN`), and an exemption for the settings-page help text (`exa_…` after `Bearer`) |
| `selftest/` | Controls for the policy above and the identity SQL |
| `scenarios/first-run-install.yaml` | Server install and first admin. Commands are copied from the product docs. Terminal steps run through the exec driver in step order; the adapter's wrapper and other machine settings come from the hook config's `terminal` object |
| `scenarios/team-tour.yaml` | Read-only tour of the operating team on instance B: admin and member log in, overview, plan usage, sessions, the member's own sessions |
| `scenarios/team-tour-injected.yaml` | The same screens with no login, for `--state-from` |
| `scenarios/onboarding.yaml` | The admin adds a temporary persona, who signs in with the one-time password, must change it, and cannot use the old one again |
| `roster.onboarding.json` | `roster.json` plus the temporary onboarding persona |
| `selectors.json` | Dashboard selectors and extra masks for run mode |
| `runner/hooks.mjs` | Run-mode hooks: re-anchor and freshness, product assertions, onboarding lock and cleanup |
| `runner/cleanup-onboarding.sql` | Removes the temporary persona (API tokens, then the user row). Idempotent |
| `runner/hook-config.example.json` | Shape of the local hook config |
| `sql/identity.sql` | Identity assertions for `app_users`, `app_sessions`, `app_usage_samples` |
| `data-contracts.md` | Data contract of the dashboard charts |

There is no `generator/`. Capturing the operating screens of a real product needs a generator that satisfies its `data-contracts.md`.

## Run mode

```
runners/run.sh --engine <engine> [engine options] --scenario adapters/example-app/scenarios/team-tour.yaml \
  --out <run dir>/tour-1 --denylist <real denylist> --policy adapters/example-app/policy.json \
  --hooks adapters/example-app/runner/hooks.mjs --hook-config <local hook config> \
  --secrets-dir <dir with b-ivynonrealton, b-marcusfablewright> --timeout 15000
```

| Fact | Effect on run mode |
|---|---|
| Instance B listens on the loopback address. Cookies ignore ports, so in a browser profile that other local servers also use, a login on `127.0.0.1` would overwrite their session cookies | Scenarios use `example-b.localhost:38080`, a `*.localhost` alias that resolves to the loopback address and holds its own cookies. Health checks from Node use `127.0.0.1` (`health_url`) |
| Login issues a 15-minute access token and a longer-lived refresh cookie scoped to `/api/auth/refresh`; the dashboard refreshes access silently | Injected state older than 15 minutes still works without a login page; the hooks' engine settings clear and export cookies on the refresh path too. A state whose refresh cookie has expired needs a fresh login run |
| The dashboard polls the server | Never wait for network idle; scenarios wait for elements |
| Re-anchoring moves generated data to now, but the generator's last event sits some minutes before the anchor | `freshness_max_seconds` is that tail gap plus a margin (`fixtures/realism-checklist.md`) |
| Re-anchoring changes operational session IDs | For phase 4 gating, extract the IDs of the data now loaded after every re-anchor and pass them with `--allow-uuids`; an allowlist from an earlier load goes stale |
| There is no delete-user API | The onboarding teardown runs `runner/cleanup-onboarding.sql` through `psql_command`, also after a failure. If a run dies before teardown, run the SQL by hand. The setup refuses to start while the persona exists or another onboarding run holds the lock |
| Dates in session lists follow the browser locale | Scenarios pin `en-US` and a hook asserts the English date format |
| Captures include charts and live counters | `selectors.json` masks the chart containers and the `N / M items` counters in addition to the core defaults |

Passwords for `{{secret.b-<handle>}}` are files in `--secrets-dir` (mode 600, outside the repository). The onboarding persona's one-time password is captured from the dialog in memory; its new password is generated by the hooks.
