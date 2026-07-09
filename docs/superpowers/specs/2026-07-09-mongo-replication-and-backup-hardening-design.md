# Mongo Replication + Backup Pipeline Hardening (Resilience Standard, Phase 2)

## Context

`RESILIENCE_STANDARD_GUIDELINE.md` Tier 2 (the target tier for this app,
per team decision) requires: database replication for live redundancy,
backups every 6 hours with off-site copies, a dead-man's-switch heartbeat,
oplog capture for point-in-time replay, and a monthly restore drill that has
actually been executed and logged.

The audit found the current state:

- Single-instance MongoDB (`mongodb` service) in both `compose.yaml` and
  `compose.synology.yaml` — no replica set, no secondary.
- `scripts/mongo-backup.sh` + `scripts/backup-common.sh` already implement
  logical Mongo backups with checksums, local retention/pruning, and an
  optional local-directory "off-site" copy (`BACKUP_OFFSITE_DIR`) — but no
  `--oplog` flag, no push to a real remote (rclone/S3/etc.), and no
  heartbeat ping. Scheduling is via `deploy/systemd/flymanager-mongo-backup.*`
  or the cron example in `deploy/cron/flymanager-backups.crontab.example`,
  run from outside the Docker stack.
- `scripts/mongo-restore.sh` already restores an archive into the running
  Compose stack's `mongodb` service (stopping/restarting `app` around it),
  but always targets the live stack, not an isolated scratch copy — not
  safe to use for a drill.
- `BACKUP_AND_RECOVERY.md` documents a full drill procedure, but no drill
  has actually been executed and logged.
- Production data lives in the self-hosted Compose stack (confirmed with
  the team), not MongoDB Atlas — so this phase's compose changes are the
  actual production topology, not a dev-only path. The existing `mongodb`
  service and its `mongo_data` volume hold real data and must not be
  recreated or discarded when adding replication.

Decisions made with the team before this spec:

- Add the replica set now (not deferred).
- Adopt the guideline's reference-architecture backup container (§11.3)
  rather than extending the existing host-level systemd/cron scripts for
  *scheduled* Mongo backups.
- Off-site target is rclone + a healthcheck.io heartbeat, wired generically
  (env-var driven); the team will supply the actual remote name and UUID
  after this lands.
- Build restore-drill tooling and actually execute one drill in this phase,
  against a disposable scratch environment — never the live stack.

## Goal

Bring the self-hosted Compose deployment (`compose.yaml` and
`compose.synology.yaml`) to Tier 2: a real Mongo replica set, an in-stack
backup container with oplog capture + off-site push + heartbeat, and one
executed, dated restore drill on record.

## Design

### 1. Replica set topology

Both `compose.yaml` and `compose.synology.yaml` get:

- `mongodb` (existing service, existing `mongo_data` volume, unchanged
  container name) becomes replica-set member `_id: 0, priority: 2` —
  i.e. the preferred primary. Its command gains `--replSet rs0`.
- A new `mongodb2` service (new `mongo_data2` volume), member
  `_id: 1, priority: 1`, same image/healthcheck pattern as `mongodb`,
  also with `--replSet rs0`.
- A new one-shot `mongo-init` service, `restart: "no"`, depending on both
  Mongo services being `service_healthy`, running the guideline's
  idempotent initiate check:

  ```sh
  mongosh --host mongodb:27017 --eval '
  try { rs.status(); }
  catch (e) {
    rs.initiate({
      _id: "rs0",
      members: [
        { _id: 0, host: "mongodb:27017", priority: 2 },
        { _id: 1, host: "mongodb2:27017", priority: 1 }
      ]
    });
  }'
  ```

- `MONGO_URI` (in `compose.yaml`'s `app`/`worker` environment block, and in
  `compose.synology.yaml`'s hardcoded equivalent) changes from
  `mongodb://mongodb:27017` to
  `mongodb://mongodb:27017,mongodb2:27017/?replicaSet=rs0`.
- `app`/`worker` `depends_on` gains `mongo-init: condition:
  service_completed_successfully` (in addition to the existing
  `mongodb: condition: service_healthy`), so the app never starts before
  the replica set is initialized on a fresh deploy.

**Migration procedure for the existing production stack** (documented in
`BACKUP_AND_RECOVERY.md`, not automated — this is a one-time, operator-run
step with a brief window where the app is down):

1. Take a Mongo backup with the existing `scripts/mongo-backup.sh` first
   (belt-and-suspenders, even though the migration itself doesn't touch
   data files).
2. Stop the stack (`docker compose down` — `mongo_data` volume persists).
3. Pull the updated compose file (adds `--replSet rs0` to `mongodb`, adds
   `mongodb2` + `mongo-init`, updates `MONGO_URI`).
4. Start the stack (`docker compose up -d`). The existing `mongodb`
   container restarts against its **same, untouched** `mongo_data` volume,
   now with `--replSet rs0` — this does not move, copy, or reformat any
   data, it only changes the mongod process's replication mode.
   `mongodb2` starts fresh/empty and `mongo-init` runs `rs.initiate()`,
   after which `mongodb2` syncs a full initial copy from `mongodb` via
   normal replica-set initial sync (this is where the existing data
   actually gets copied to the secondary — a read-only operation on the
   primary, not a mutation).
5. Verify `rs.status()` on `mongodb` shows both members, `mongodb` as
   `PRIMARY` with its pre-migration data intact, and `mongodb2` reaching
   `SECONDARY` state.
6. Verify `/health/ready` and representative UI reads.

This procedure will be rehearsed once against a **copy** of the data
(restored into the scratch environment from Task-level testing, see
Restore Drill below) before being run against the real Synology stack, so
the first real execution isn't the first time it's tried.

### 2. Backup container (guideline §11.3)

Added to both compose files as a new `mongo-backup` service, following the
reference architecture directly (`rclone/rclone:latest` image, no custom
build needed):

```yaml
mongo-backup:
  image: rclone/rclone:latest
  restart: unless-stopped
  depends_on:
    mongodb:
      condition: service_healthy
  volumes:
    - mongo_backups:/backups
    - ./scripts/container-mongo-backup.sh:/usr/local/bin/container-mongo-backup.sh:ro
    - ${RCLONE_CONFIG_PATH:-./scripts/rclone.conf}:/config/rclone/rclone.conf:ro
  environment:
    MONGO_HOST: mongodb
    MONGO_PORT: "27017"
    MONGO_REPLICA_SET: rs0
    BACKUP_KEEP_DAYS: ${BACKUP_KEEP_DAYS:-14}
    RCLONE_REMOTE: ${RCLONE_REMOTE:-}
    HEALTHCHECK_UUID: ${HEALTHCHECK_UUID:-}
    BACKUP_INTERVAL_SECONDS: ${MONGO_BACKUP_INTERVAL_SECONDS:-21600}
  entrypoint: ["/bin/sh", "-c"]
  command: >
    apk add --no-cache mongodb-tools bash curl > /dev/null 2>&1 &&
    chmod +x /usr/local/bin/container-mongo-backup.sh &&
    /usr/local/bin/container-mongo-backup.sh &&
    while true; do sleep "$$BACKUP_INTERVAL_SECONDS"; /usr/local/bin/container-mongo-backup.sh; done
```

New script `scripts/container-mongo-backup.sh` (new file, not a
modification of the existing host-side `scripts/mongo-backup.sh` — this
one runs *inside* the compose network against `mongodb:27017` directly,
with no `docker compose exec` indirection):

```sh
#!/bin/sh
set -eu

TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
ARCHIVE="/backups/flymanager_mongodb_${TIMESTAMP}.archive.gz"

mongodump \
  --host "${MONGO_HOST}:${MONGO_PORT}" \
  --oplog \
  --archive="$ARCHIVE" \
  --gzip

sha256sum "$ARCHIVE" > "${ARCHIVE}.sha256"

# Prune local archives older than BACKUP_KEEP_DAYS.
find /backups -maxdepth 1 -type f -name 'flymanager_mongodb_*.archive.gz*' \
  -mtime +"${BACKUP_KEEP_DAYS}" -delete

if [ -n "${RCLONE_REMOTE:-}" ]; then
  rclone copy "$ARCHIVE" "${RCLONE_REMOTE}/"
  rclone copy "${ARCHIVE}.sha256" "${RCLONE_REMOTE}/"
fi

if [ -n "${HEALTHCHECK_UUID:-}" ]; then
  curl -fsS -m 10 --retry 3 "https://hc-ping.com/${HEALTHCHECK_UUID}" > /dev/null || true
fi

echo "Backup complete: $ARCHIVE"
```

`--oplog` requires `mongodump` to run against a real replica-set member
(works once Task 1's replica set exists) — it captures oplog entries
spanning the dump, enabling point-in-time-consistent restores.

New volume `mongo_backups` added to both compose files' `volumes:` block.

**What this replaces, what it doesn't:**

- Replaces the *scheduling* of routine Mongo backups: remove
  `deploy/systemd/flymanager-mongo-backup.service`,
  `deploy/systemd/flymanager-mongo-backup.timer`, and the Mongo line from
  `deploy/cron/flymanager-backups.crontab.example` (state backup's
  systemd/cron entries are untouched).
- Does **not** replace `scripts/mongo-backup.sh` /
  `scripts/mongo-restore.sh` themselves — those remain the documented
  tool for **manual**, on-demand backups (e.g. the existing "Pre-Upgrade
  Backup Procedure" in `BACKUP_AND_RECOVERY.md` still runs
  `./scripts/mongo-backup.sh` by hand before a deploy). `BACKUP_AND_RECOVERY.md`
  is updated to state clearly: routine/scheduled backups now come from the
  `mongo-backup` container; `scripts/mongo-backup.sh` is for manual/ad hoc
  use and pre-upgrade snapshots.

### 3. Restore drill

New script `scripts/mongo-restore-drill.sh` (new file): spins up a
throwaway, isolated `mongod` container (its own Docker network/volume,
never touching the live stack's containers or volumes), restores a given
archive into it with `mongorestore --archive --gzip --oplogReplay`, runs a
handful of read-only sanity queries (collection counts for `stocks`,
`crosses`, `users`), and tears the scratch container down. This is safe to
run against a copy of a real production archive without any risk to the
live deployment.

```sh
#!/bin/sh
set -eu
# Usage: ./scripts/mongo-restore-drill.sh path/to/backup.archive.gz

ARCHIVE_PATH=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
DRILL_CONTAINER="flymanager-restore-drill-$$"

docker run -d --name "$DRILL_CONTAINER" mongo:7.0 > /dev/null
trap 'docker rm -f "$DRILL_CONTAINER" > /dev/null 2>&1 || true' EXIT

# Wait for the scratch mongod to accept connections.
for _ in $(seq 1 30); do
  docker exec "$DRILL_CONTAINER" mongosh --quiet --eval 'db.adminCommand("ping").ok' > /dev/null 2>&1 && break
  sleep 1
done

docker cp "$ARCHIVE_PATH" "$DRILL_CONTAINER:/tmp/drill.archive.gz"
docker exec "$DRILL_CONTAINER" sh -c 'mongorestore --archive=/tmp/drill.archive.gz --gzip'

echo "--- Post-restore sanity counts ---"
docker exec "$DRILL_CONTAINER" mongosh --quiet --eval '
  const dbName = db.getSiblingDB("flymanager");
  print("stocks:", dbName.stocks.countDocuments());
  print("crosses:", dbName.crosses.countDocuments());
  print("users:", dbName.users.countDocuments());
'
```

(`--oplogReplay` is omitted from the plain restore call above since a
single-node scratch `mongod` isn't itself a replica set target for oplog
replay in this minimal form — the drill validates data restorability and
counts, which is the guideline's actual stated goal ("prove archives are
usable"); full point-in-time oplog replay is exercised separately if/when
a real incident requires it, and this is called out explicitly in the
drill log rather than silently assumed.)

This phase's implementation includes **actually running** this drill once
against a real backup archive taken during Task testing, with the dated
result (archive name, sanity counts, any issues, approximate RTO) appended
to `BACKUP_AND_RECOVERY.md`'s "Recovery Drill Procedure" section as a
"Drill Log" subsection.

### 4. Documentation updates

`BACKUP_AND_RECOVERY.md` updates:

- New "Replica Set" section describing the topology, the migration
  procedure (verbatim from Design §1), and how to verify `rs.status()`.
- "Mongo backup" script reference section gains a note that routine/
  scheduled backups now run from the `mongo-backup` container, describes
  its env vars (`RCLONE_REMOTE`, `HEALTHCHECK_UUID`, `BACKUP_KEEP_DAYS`,
  `MONGO_BACKUP_INTERVAL_SECONDS`), and the `--oplog` behavior.
- New "Restore Drill" subsection: how to run
  `scripts/mongo-restore-drill.sh`, plus the dated log entry from this
  phase's executed drill.
- `.env.example` (or wherever env vars are documented, confirmed during
  implementation) gains `RCLONE_REMOTE`, `HEALTHCHECK_UUID`,
  `RCLONE_CONFIG_PATH`, `BACKUP_KEEP_DAYS`, `MONGO_BACKUP_INTERVAL_SECONDS`
  with comments explaining they're optional until the team configures a
  real remote/account.

### 5. Tests

Given this phase is primarily infrastructure (Docker Compose, shell
scripts), the automated-test surface is smaller than Phase 1's. Coverage
added:

- A shell-level test/dry-run mode for `scripts/container-mongo-backup.sh`
  (mirroring the existing `BACKUP_DRY_RUN` pattern in
  `scripts/mongo-backup.sh`/`backup-common.sh`) so it can be exercised in
  CI without a real Mongo/rclone/healthchecks.io present.
- A `compose config` validation step (`docker compose -f compose.yaml
  config`, `docker compose -f compose.synology.yaml config`) confirming
  both files still parse and resolve after the edits — run manually during
  implementation, not added as a pytest test (no existing pattern for
  shelling out to `docker compose config` in this repo's pytest suite).
- The executed restore drill itself (Design §3) is the primary evidence
  requirement from the guideline ("tested restore verification"), not a
  unit test.

## Out of scope (deferred to later phases or explicitly not done)

- `scripts/state-backup.sh` / state backup scheduling: untouched.
- Multi-region/geo redundancy beyond a single rclone remote (Tier 3 only).
- Automated CI execution of the restore drill on a schedule (this phase
  executes it once, manually, as required; recurring automated drills are
  a follow-up if the team wants them).
- Any change to app-level idempotency, atomic writes, or the reversible-ops
  work — those are Phases 3 and 4.
