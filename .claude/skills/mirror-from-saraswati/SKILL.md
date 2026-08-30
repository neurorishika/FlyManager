---
name: mirror-from-saraswati
description: Use when asked to mirror, sync, refresh, or pull production FlyManager data (MongoDB and/or uploads/labels) from the Synology NAS "saraswati" down into the local Docker Compose stack for testing against real data.
---

Refreshes the **local** FlyManager stack with a snapshot of **production**
data from the Synology NAS. Everything runs through
`.claude/skills/mirror-from-saraswati/mirror.sh`, which streams a fresh
`mongodump` out of the production mongo container, rsyncs runtime files,
rebuilds the local Compose stack, and restores the dump into it.

Direction is one-way: **NAS → laptop, never the reverse.** The NAS side is
strictly read-only — the script does not trigger the production backup
script, write any file on the NAS, or touch the Portainer stack. To push
changes *up* to production, use the separate `deploy-flymanager` skill.

**This pulls real lab data — real user accounts, emails, stock records — onto
the local machine.** No sanitization is applied. That is a deliberate choice
so local testing matches production behavior; treat the local stack as
holding production data once mirrored.

**`restore` is destructive locally: it drops the local `flymanager`
database.** Nothing on the NAS is at risk, but any local-only test data is
gone. The script confirms interactively and refuses to run
non-interactively without `--yes`.

## Prerequisites

Verified working in this environment:

- Passwordless SSH to `saraswati@saraswati.taild08eb9.ts.net` (Tailscale).
- `docker` is **not** on `PATH` for non-interactive SSH on the NAS — the
  script always invokes it as `/usr/local/bin/docker`.
- `mongodump`/`mongorestore` exist inside the `flymanager-mongodb`
  containers on both ends (`/usr/bin/mongodump`), so no tooling image is
  needed.
- `rsync` on both the NAS (`/usr/bin/rsync`) and locally.
- Docker Desktop with Compose v2 locally.

## Usage

From the repo root:

```bash
.claude/skills/mirror-from-saraswati/mirror.sh          # full mirror (interactive confirm)
.claude/skills/mirror-from-saraswati/mirror.sh --yes    # full mirror, no prompt
```

Individual phases, in the order `all` runs them:

| Command   | What it does |
|-----------|--------------|
| `dump`    | `mongodump --db flymanager --archive --gzip` streamed over SSH into `backups/mongodb/saraswati_flymanager_<ts>.archive.gz`; keeps the newest 5 |
| `files`   | `rsync -az` of `data/uploads`, `data/backup`, `data/job_exports`, and `generated_labels` |
| `up`      | `docker compose -f compose.yaml up -d --build`, then waits for mongo healthy **and** a primary elected |
| `restore` | Drops the local `flymanager` DB, restores the newest archive, prints per-collection counts, restarts `app` + `worker` |

Options: `--yes` (skip confirmation), `--with-reference` (also rsync
`data/flybase` + `bloomington.csv`, ~311 MB of static reference data),
`--archive PATH` (restore a specific saved archive instead of the newest).

Overridable env vars: `NAS_SSH_TARGET`, `NAS_ROOT`, `MONGODB_CONTAINER`,
`MONGO_DB_NAME`, `COMPOSE_FILE`, `DUMP_DIR`, `KEEP_DUMPS`, `WAIT_TIMEOUT`.

## Design notes worth not re-deriving

- **The dump is scoped to `--db flymanager`.** An unscoped `mongodump`
  carries `admin` and `config`; restoring those over the local stack would
  clobber its replica-set configuration. `restore` additionally passes
  `--nsInclude 'flymanager.*'` as a second guard.
- **`rsync` runs without `--delete`.** Local `data/` holds directories that
  do not exist on the NAS (`phenotype_images/`, `markers/`, `client.key`).
  Mirroring deletions would destroy them.
- **`restore` drops the whole DB before restoring**, not just `--drop` per
  collection. `--drop` alone leaves behind local-only collections that
  aren't in the archive, which is not a mirror.
- **`--writeConcern '{"w":1}'`**, not majority: the local set has two
  members and the secondary may still be catching up right after `up`.
- **`up` refuses to run when another checkout owns the stack.** `compose.yaml`
  hardcodes `container_name: flymanager-*`, so the repo root and any
  `.claude/worktrees/*` worktree cannot run the stack simultaneously — a
  second `up` dies with an opaque "container name is already in use". The
  preflight names the owning project and directory, and offers both fixes:
  run `restore` from that directory instead, or take that stack down first.
  `restore` deliberately has no such check — mirroring into whichever stack
  is currently up is a legitimate use.
- **`up` waits for a writable primary, not just a healthy container.**
  `mongo-init` initiates `rs0`, and a restore issued before the election
  completes fails with "not primary".

## Cadence

There is no scheduler — this is invoked by hand, whenever local testing
needs fresh production data. (A cloud-scheduled Claude agent cannot run it:
it has neither local Docker nor Tailscale access to the NAS. If a timer is
ever wanted, `mirror.sh --yes` is fully deterministic and drops straight
into a launchd job.)

## Verifying it worked

`restore` already prints per-collection counts. To spot-check the app:

```bash
docker compose -f compose.yaml ps
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5234/health/ready
```

Then use the `run-flymanager` skill to log in and drive the UI.
