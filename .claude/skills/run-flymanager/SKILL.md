---
name: run-flymanager
description: Build, run, and drive FlyManager locally via Docker Compose. Use when asked to start FlyManager, launch the app, run its background job queue, take a screenshot of its UI, log in and check pages, run its tests, or otherwise interact with the running app.
---

FlyManager is a Flask + MongoDB (2-member replica set) + Redis/RQ web app
(server-rendered Jinja templates, session-cookie auth, a background job
queue for heavy admin actions). Drive it via
`.claude/skills/run-flymanager/driver.sh` — it builds/launches the full
Docker Compose stack, bootstraps a test admin, and logs in via `curl`
with a real CSRF-token flow. All paths below are relative to the repo
root.

## Prerequisites

Verified in this environment: Docker Desktop with Compose v2
(`docker compose version` → v5.1.3), Poetry 2.2.1, Python 3.10 (project
requires `>=3.9,<3.13`; the repo's own `.venv` already uses 3.10.20 — use
`poetry install` rather than assuming the system Python matches). No
`apt-get` packages needed — this repo was driven on macOS with Docker
Desktop, not a Linux container.

For the screenshot command: Google Chrome installed at
`/Applications/Google Chrome.app` (macOS). On Linux, `google-chrome-stable`
or `chromium` on `PATH` works too (the driver falls back to those, but
that fallback path is untested in this environment — only the macOS path
above has actually been run).

## Setup

Nothing to install beyond `poetry install` if you want the host-side
Python env (only needed for direct `pytest`/`flymanager/app/run.py` use,
not for the Docker-driven path below).

**Known gotcha, handled automatically by the driver — read this before
debugging a Mongo connection failure:** this repo's tracked `.env` has a
stale MongoDB Atlas SRV URI left over from an earlier setup. `docker
compose` auto-loads `.env` for variable substitution, so a bare `docker
compose -f compose.yaml up -d --build` silently picks up that Atlas URL
instead of the local replica set and the `app` container crash-loops
with `pymongo.errors.ConfigurationError: The DNS query name does not
exist: _mongodb._tcp.cluster0.two2erm.mongodb.net`. `driver.sh` exports
`MONGO_URI`/`MONGO_DB_NAME` explicitly before every `docker compose`
call specifically to route around this — don't "fix" it by editing
`.env` (unclear if that file is needed for something else); just always
go through the driver, or export those two vars yourself first.

## Build

No separate build step — `driver.sh up` builds the `app`/`worker` images
as part of bringing the stack up (see below).

## Run (agent path)

```bash
./.claude/skills/run-flymanager/driver.sh up
```

Builds the `app`/`worker` images from source, starts the full stack
(`mongodb`, `mongodb2`, `mongo-init`, `mongo-backup`, `redis`, `app`,
`worker`), and polls `/health/ready` until the app answers (verified: ~15s
cold start once images are built). Idempotent — safe to re-run; it reuses
existing volumes and only rebuilds/recreates what changed. Confirmed by
tearing the whole stack down (`docker compose down`, no `-v`) and
bringing it back up through this exact command with no manual
intervention.

Create a known-password test admin (the driver invokes
`python -m flymanager.app.bootstrap` directly inside the running `app`
container with explicit `-e` overrides — relying on `docker compose up`'s
own entrypoint-triggered bootstrap was flaky in practice, this path was
not):

```bash
./.claude/skills/run-flymanager/driver.sh bootstrap
```

Idempotent — prints `Bootstrap user 'devtest' already exists.` on repeat
runs rather than erroring. Default credentials: `devtest` /
`DevTestPassw0rd` (override via `TEST_ADMIN_USERNAME` /
`TEST_ADMIN_PASSWORD` / `TEST_ADMIN_INITIALS` env vars before calling).

Log in via `curl` and verify real authenticated pages load with real
data:

```bash
./.claude/skills/run-flymanager/driver.sh verify
```

This is the actual interaction harness: it GETs `/auth/login`, extracts
the CSRF token from the page's `<meta name="csrf-token">` tag (**not** a
hidden form field — grepping for `name="csrf_token"` finds nothing and
the POST fails with `400 The CSRF token is missing`), POSTs the login
form, then confirms `/home`, `/stock/explorer`, and
`/jobs/status.json` all return `200` for the authenticated session.
Prints `All checks passed.` on success, exits non-zero with a specific
`FAILED: ...` line on any failure. Verified output on a real run:

```
--- GET /auth/login ---
--- POST /auth/login ---
Login OK (http_code=200)
--- GET /home (authenticated) ---
/home OK
--- GET /stock/explorer (real seeded data) ---
/stock/explorer OK
--- GET /jobs/status.json (job-queue API) ---
/jobs/status.json OK: {"jobs":[]}
All checks passed.
```

Screenshot the (public, unauthenticated) login page:

```bash
./.claude/skills/run-flymanager/driver.sh screenshot
```

Writes to `.claude/skills/run-flymanager/shots/login.png` by default
(pass a path as `$1` to override). Uses headless Chrome
(`--headless=new --disable-gpu --no-sandbox`) — confirmed to render the
real page correctly (branding, lab name pulled from seeded settings
data, working form), not a blank/error page.

| command | what it does |
|---|---|
| `up` | Build + start the full stack, wait for `/health/ready` |
| `bootstrap` | Create/confirm a known-password test admin user |
| `verify` | `curl`-based login + authenticated-page smoke test |
| `screenshot [path]` | Headless-Chrome screenshot of `/auth/login` |
| `status` | `docker compose ps` |
| `logs <service>` | `docker compose logs -f <service>` |
| `down` | Stop the stack (keeps data volumes — no `-v`) |
| `dev-fixtures` | Idempotently apply DB state `css_audit.py`'s `PAGES` list depends on (currently: `AssignedTo: "devtest"` on cross `18d73b2cc8`, needed for the `view-cross` route to render instead of silently redirecting) |

**Run `driver.sh dev-fixtures` before `css_audit.py capture`/`tapcheck`
whenever the Mongo volume is fresh or was reseeded.** There is no tracked
seed/fixture script for the dev dataset in this repo — `mongo-init` in
`compose.yaml` only initializes the replica set, the actual records live
purely in the persistent volume — so `dev-fixtures` is the one tracked,
idempotent place that records the DB-state dependencies the visual-regression
harness's `PAGES` list has. Safe to re-run any time; it just re-asserts the
same field value.

## Run (human path)

```bash
docker compose -f compose.yaml up -d --build
```

Then open `http://localhost:5234` in a browser. Meaningfully different
from the agent path only in that there's no automated login/verification
— you click through manually. Same `.env`-shadowing gotcha applies; export
`MONGO_URI`/`MONGO_DB_NAME` first or the app container will crash-loop.

For pure Flask-process iteration without rebuilding a Docker image at
all (fastest edit-test loop, documented in `README.md`'s Local
Development section):

```bash
docker compose -f compose.yaml up -d mongodb mongodb2 mongo-init redis
poetry run python flymanager/app/run.py
```

## Test

```bash
MONGO_URI="mongodb://127.0.0.1:27017" MONGO_DB_NAME="flymanager_test" ENABLE_SCHEDULER=0 \
  poetry run pytest tests/test_background_jobs.py -v
```

8/8 passed on a verified run. **Do not include `?replicaSet=rs0` or the
`mongodb2` host** in the test `MONGO_URI` — `mongodb2` is only reachable
by that hostname from inside the Docker network; pytest running directly
on the host gets `ServerSelectionTimeoutError: mongodb2:27017: nodename
nor servname provided, or not known` if you include it (confirmed by
hitting this exact error). A plain single-host URI against `mongodb`'s
published port (`127.0.0.1:27017`) works because pymongo doesn't need to
resolve the rest of the replica set for a non-replicaSet-aware
connection string.

Most of the rest of the suite (anything importing `flymanager.app`
directly rather than the isolated modules) has this same Docker-network
constraint plus needs a real MongoDB reachable at import time — not all
of it was re-verified in this pass; the command above is the one that
was actually run and confirmed passing.

## Gotchas

- **CSRF token is in a meta tag, not a form field.** `flymanager/app/templates/base.html` renders `<meta name="csrf-token" content="{{ csrf_token() }}">` and client-side JS injects it into forms before submit. A `curl`-only flow (no JS execution) must scrape the meta tag itself — grepping for the more obvious `name="csrf_token" value="..."` pattern silently finds nothing and every POST fails with `400 The CSRF token is missing`.
- **The login route is `/auth/login`, not `/login`.** `auth.py` registers its blueprint with `url_prefix="/auth"`. `GET /login` 404s.
- **`docker compose` silently trusts the tracked `.env`'s stale Atlas URI** unless you override `MONGO_URI`/`MONGO_DB_NAME` explicitly — see Setup above. This is the single most likely failure mode for a fresh agent trying to run this app; the symptom is an `app`/`worker` crash-loop with a DNS resolution error mentioning `cluster0.two2erm.mongodb.net`, not anything that looks like a local config problem.
- **`docker compose up`'s automatic entrypoint bootstrap can silently no-op.** `docker/app-entrypoint.sh` calls `python -m flymanager.app.bootstrap` before starting gunicorn, and in practice a `FLYMANAGER_ADMIN_*`-prefixed `docker compose up` invocation sometimes didn't result in the user being created (no error, just silence — `ensure_default_admin()` returns early and prints nothing if `FLYMANAGER_ADMIN_USERNAME` reads as empty inside that specific process). Calling bootstrap directly via `docker compose exec -e ... app python -m flymanager.app.bootstrap` (what `driver.sh bootstrap` does) was reliable across repeated runs; relying on `up` alone was not.
- **`mongodb2` has no published host port.** Only `mongodb`'s `27017` is exposed to the host (`compose.yaml`'s `ports:` mapping). Anything running outside Docker (host-side `pytest`, a host-side `python` REPL) can reach `mongodb` at `127.0.0.1:27017` but cannot resolve `mongodb2` by hostname at all — relevant for both the `Test` section above and for any ad hoc host-side script that wants full replica-set awareness (it doesn't get it; only a container on the compose network does).

## Troubleshooting

- **`pymongo.errors.ConfigurationError: The DNS query name does not exist: _mongodb._tcp.cluster0.two2erm.mongodb.net`** in `app`/`worker` logs: the stale-`.env` gotcha above. Export `MONGO_URI`/`MONGO_DB_NAME` before `docker compose up`, or just use `driver.sh up`.
- **`400 The CSRF token is missing`** on a scripted login POST: you're grabbing the CSRF token from the wrong place — see the meta-tag gotcha above.
- **`404` on `POST /login`**: wrong path — it's `/auth/login`.
- **A newly-`docker compose exec`'d bootstrap says `Bootstrap user '<name>' already exists.` but you don't remember creating it** and the login you expect fails: someone/something created that username with a different password than you're assuming. Either pick a different `TEST_ADMIN_USERNAME`, or delete the stale user directly (`docker exec flymanager-mongodb mongosh --quiet --eval 'db.getSiblingDB("flymanager").users.deleteOne({Username:"<name>"})'`) and re-run `bootstrap`.
- **`failed to do request: Head "https://registry-1.docker.io/v2/library/python/manifests/3.11-slim": net/http: TLS handshake timeout`** on `driver.sh up`: transient Docker Hub registry connectivity, not a driver/repo problem — hit this once during actual verification of this skill, a plain retry ~30s later succeeded. If images were already built once (`docker images | grep flymanager`), `docker compose -f compose.yaml up -d` (no `--build`) reuses them without touching the registry at all, which is a viable workaround if the registry stays flaky.
