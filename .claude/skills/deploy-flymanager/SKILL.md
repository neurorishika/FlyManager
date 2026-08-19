---
name: deploy-flymanager
description: Use when asked to deploy, ship, release, or push out the latest FlyManager changes to the Synology/Portainer production instance, or to build and push the FlyManager Docker image to Docker Hub.
---

FlyManager's production instance runs on a Synology NAS as a Portainer
stack named `flymanager` (Portainer stack id 11, `compose.synology.yaml`),
pulling its `app`/`worker` image from Docker Hub
(`neurorishika/flymanager`). There is no git checkout on the NAS — code
changes only reach production through build → push → redeploy.
`.claude/skills/deploy-flymanager/deploy.sh` runs the whole pipeline:
backs up Mongo, builds/pushes the image, redeploys via the Portainer API,
verifies the result, and automatically rolls back (data + image) if
verification fails.

**Note:** an unrelated app called "miniventory" runs on the same NAS with
a similar compose layout (`mongo`/`mongo2`/`app` containers, no
`container_name:` overrides) — it is a different project, not an old name
for FlyManager. Don't confuse `miniventory-*` containers/images with
`flymanager-*` ones.

**This deploys to a live, shared production instance — confirm with the
user before running anything past `build`/`backup` (i.e. `push`,
`redeploy`, `verify`, `rollback`, or `all`).**

## Prerequisites

- Docker Desktop with `buildx` (verified: v0.33.0-desktop).
- Logged in to Docker Hub with push access to `neurorishika/flymanager`
  (`docker login`; if push fails with `unauthorized`, run `docker login`
  again).
- SSH access to the NAS as a user in the `docker` group (passwordless —
  confirmed working via `ssh saraswati@saraswati.taild08eb9.ts.net
  "/usr/local/bin/docker ps"`). `docker` is not on `PATH` for
  non-interactive SSH commands on this NAS — always invoke it as
  `/usr/local/bin/docker`, not bare `docker` (bare `docker` fails with
  `command not found` over `ssh host "cmd"`, even though it works fine in
  an interactive login shell). Override the target with `NAS_SSH_TARGET`
  if it changes.
- `PORTAINER_URL` and `PORTAINER_API_KEY` — the script auto-loads both from
  the repo's tracked `.env` if they aren't already set in the environment
  (env vars still win if both are set). Portainer CE has **no stack
  webhooks** (Business Edition only — confirmed unavailable here); the API
  + a personal access token (Portainer UI → user avatar → **My account** →
  **Access tokens**) is the CE-compatible equivalent this script uses
  instead.
- Optionally `PORTAINER_STACK_NAME` (default `flymanager`) if the stack
  gets renamed in Portainer.

## Run

```bash
./.claude/skills/deploy-flymanager/deploy.sh all
```

| command | what it does |
|---|---|
| `backup` | SSHes in, runs the existing `container-mongo-backup.sh` inside `flymanager-mongo-backup` (on-demand `mongodump --oplog --archive --gzip`, same script the container already runs every 6h), records the archive path and every collection's document count into `.state/last-deploy.json` |
| `build` | `docker buildx build --platform linux/amd64 --load`, tags `neurorishika/flymanager:<git-short-sha>` and `:latest` |
| `push` | Pushes both tags to Docker Hub |
| `redeploy` | Looks up the `flymanager` stack via `GET /api/stacks`, fetches its current file + env, **overwrites the `APP_IMAGE` entry** with the new `<sha>` tag (recording the old value into `.state/last-deploy.json` for rollback), then `PUT`s the stack back with `pullImage: true` — everything else in `Env` (`SECRET_KEY`, admin/SMTP credentials, etc.) round-trips unchanged |
| `verify` | Polls `http://127.0.0.1:12754/health/ready` on the NAS (via SSH) for up to 120s, confirms `flymanager-app`'s running image matches the new tag, then re-counts every collection and fails if any count **decreased** versus the pre-deploy backup. Sets a failure-kind flag (`deploy` for health/image failures, `data` for count regressions) so `all` knows which rollback is actually warranted. |
| `rollback-image` | Cheap: reverts `APP_IMAGE` in Portainer to the pre-deploy value and redeploys. No data touched, no outage beyond the container recreate. |
| `rollback-data` | Expensive: stops `flymanager-app`/`flymanager-worker`, restores Mongo from the recorded backup archive (`mongorestore --archive --gzip --drop --writeConcern '{"w":1}'`), restarts the app/worker containers. Only warranted when data actually regressed. |
| `rollback` | Full rollback — `rollback-data` then `rollback-image`. What a human asking to roll back almost certainly wants. |
| `all` (default) | `backup` → `build` → `push` → `redeploy` → `verify`; on failure, runs **only** the rollback the failure warrants — `rollback-image` alone for a health/image failure (nothing touched the data), both for a data-count regression — then exits non-zero |

Verified end-to-end against the real NAS/Portainer instance, including one
real (if accidental) production incident during this session: see
"Incident: an unnecessary full rollback" below. `backup` (produced real
archives and a correct `.state/last-deploy.json` with per-collection
counts matching a direct `mongosh` query), `rollback-data` (a real Mongo
restore completed successfully and containers came back healthy with data
counts exactly matching pre-incident), the Portainer stack lookup and
`Env` contents (confirmed `APP_IMAGE` is pinned to a specific tag, not
`:latest` — so `redeploy` updating it is required, not optional), and
`build` (produces a working `linux/amd64` image) are all confirmed working
against production. `push`/`redeploy`/`rollback-image` were exercised in
design/against a mock Portainer API but not yet confirmed end-to-end
together in one real `all` run — do the first real `all` run under
supervision.

## Incident: an unnecessary full rollback (2026-08-19)

A real deploy attempt hit `redeploy`'s old behavior of silently no-op'ing
when Portainer credentials weren't set (this was before the `.env`
auto-load fix below existed, and before `redeploy` was changed to hard-fail
instead). Sequence: `backup` ran fine, `build`/`push` succeeded, `redeploy`
printed instructions and returned success without changing anything, then
`verify` correctly noticed `flymanager-app` was still running the *old*
image and failed — and the old, undifferentiated `rollback` ran a full
Mongo restore anyway, even though no data had ever been at risk (nothing
had actually been redeployed). That cost ~14 minutes of app downtime for a
restore that accomplished nothing a `rollback-image` no-op wouldn't have.
Two fixes landed as a direct result:

1. `redeploy` now hard-fails (`exit 1`) instead of silently returning 0
   when Portainer credentials are missing — under `set -e` this stops `all`
   before `verify`/rollback ever run, instead of limping forward into a
   guaranteed-to-fail verify.
2. `verify` now classifies *why* it failed (`deploy`: health/image
   mismatch, nothing touched the data; `data`: a real collection-count
   regression) and `all` only pays for the expensive `rollback-data` when
   the failure kind is actually `data`. A health/image failure gets
   `rollback-image` only — cheap, no outage beyond a container recreate.

Also fixed: the Mongo restore itself was slower than necessary —
`mongorestore`'s default write concern is `majority`, which waits for both
replica-set nodes to ack *every* batch. Observed throughput during the real
restore was ~1,600 docs/sec for a ~1M-document restore (~14 min total).
Since `rollback-data` always stops the app first (no live readers to
protect), `--writeConcern '{"w":1}'` is now passed explicitly to skip that
wait — the replica set still catches up asynchronously afterward.

If `verify`/`rollback` ever behave unexpectedly again, `.state/last-deploy.json`
is gitignored but not deleted between runs — check it (`backup_archive`,
`pre_counts`, `previous_app_image`) to see exactly what a given `redeploy`/
`rollback` believed the prior state was.

## Gotchas

- **Build on Apple Silicon defaults to `arm64` — the Synology NAS is
  `amd64`.** A plain `docker build` (no `--platform`) on an M-series Mac
  produces an image that fails on the NAS with `exec format error`. The
  script always passes `--platform linux/amd64` via `buildx`; override
  with `DOCKER_PLATFORM` only if the target NAS is actually ARM.
- **The image tag is the git short SHA, and `APP_IMAGE` is always pinned
  to it** (never left on `:latest`) — this is what makes rollback
  deterministic: `redeploy` records whatever `APP_IMAGE` was before it
  overwrites it, and `rollback` puts that exact value back. If `APP_IMAGE`
  is ever hand-edited in the Portainer UI to something this script didn't
  push (e.g. back to `:latest`), the next `redeploy` will still capture it
  correctly as "previous", so rollback stays correct either way.
- **`docker` is not on `PATH` over non-interactive SSH** on this NAS —
  every remote command uses `/usr/local/bin/docker` explicitly. Bare
  `docker` in a new script/command will silently fail with
  `command not found`.
- **`redeploy` recreates containers with whatever is already pushed to
  Docker Hub for the resolved tag** — it does not run `docker build`. The
  push step must complete first or the re-pull grabs a stale/missing
  image.
- **`verify`'s data check only catches document-count regressions**, not
  content corruption — it compares collection counts before/after, so a
  deploy that silently mutates existing documents without changing counts
  would not be caught. It's a safety net against data loss, not a full
  integrity check.
- **`rollback-data` stops `flymanager-app` and `flymanager-worker` for the
  duration of the Mongo restore** (mirrors `scripts/mongo-restore.sh`'s
  approach) — expect several minutes of outage (~1,600 docs/sec even at
  relaxed write concern; a ~1M-document restore took ~14 minutes in
  practice). `rollback-image` alone has no comparable outage — just a
  normal container recreate. `all` only reaches for `rollback-data` when
  `verify` specifically detected a data regression — see the incident
  writeup above for why that distinction exists.
- No git checkout exists on the NAS (see `compose.synology.yaml`'s header
  comment) — code changes only reach production through this image
  build/push/redeploy path, never a `git pull` on the NAS.
- **The `miniventory` app on the same NAS is unrelated** — don't reuse
  its container names, image, or backup script path for FlyManager.

## Troubleshooting

- **`denied: requested access to the resource is denied` on `push`**: not
  logged in, or logged in as the wrong account. Run `docker login`.
- **`redeploy` exits with "PORTAINER_URL and/or PORTAINER_API_KEY not
  set"**: neither the environment nor the repo's `.env` had both — export
  them explicitly, or add them to `.env` — see Prerequisites. This is now
  a hard failure (`exit 1`), not a silent no-op, specifically because a
  silent no-op here once caused `all` to run `verify` against an unchanged
  deploy and trigger a needless full rollback — see the incident writeup
  above. Manual alternative: redeploy via Portainer's **Pull and redeploy**
  button on the stack.
- **`ERROR: no stack named 'flymanager' found via the Portainer API`**:
  the stack has a different name — set `PORTAINER_STACK_NAME` to match
  what's shown in Portainer's Stacks list.
- **`curl: (22) The requested URL returned error: 401`** (Portainer):
  the access token is invalid/expired — generate a new one (Portainer →
  My account → Access tokens) and re-export `PORTAINER_API_KEY`.
- **App container crash-loops with `exec format error` after deploy**:
  the pushed image was built for the wrong architecture — rebuild with
  `DOCKER_PLATFORM=linux/amd64` (the default) and re-push.
- **`verify` fails with "collection count(s) decreased"**: something in
  this deploy dropped data — do not re-run `redeploy` to "fix" it; let the
  automatic `rollback` (via `all`) restore from the pre-deploy backup, or
  run `rollback` manually if you ran steps individually.
- **`ERROR: no state file at .state/last-deploy.json`** on `rollback`:
  `backup`/`redeploy` were never run this session (state isn't persisted
  across unrelated deploys) — there's nothing recorded to roll back to;
  restore manually from the NAS's `/backups` volume
  (`flymanager_mongo_backups`) instead.
