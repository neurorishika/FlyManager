# Mongo Replication + Backup Pipeline Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the self-hosted Compose deployment to Tier 2 of `RESILIENCE_STANDARD_GUIDELINE.md`: a real Mongo replica set, an in-stack backup container with oplog capture + off-site push + heartbeat, and one executed, dated restore drill on record.

**Architecture:** `compose.yaml` and `compose.synology.yaml` both get a second Mongo member (`mongodb2`) and a one-shot `mongo-init` container that idempotently runs `rs.initiate()`. A new `mongo-backup` service (the guideline's `rclone/rclone` reference container) runs a new container-native script on a loop, doing `mongodump --oplog`, local retention pruning, an optional `rclone` push, and an optional `healthcheck.io` ping. A new `scripts/mongo-restore-drill.sh` restores an archive into a fully disposable scratch `mongod` container (never the live stack) and reports sanity counts — this phase runs it once for real and logs the result.

**Tech Stack:** Docker Compose, MongoDB 7.0, `rclone/rclone` image, POSIX shell, pytest (for the one Python-testable surface: none new here — this phase's tests are shell dry-run checks, not pytest).

## Global Constraints

- The existing `mongodb` service's `mongo_data` volume must never be recreated, renamed, or have its data touched by anything other than the mongod process itself gaining `--replSet rs0` — no migration step may copy, delete, or reformat it.
- Backup cadence: every 6 hours (`21600` seconds), matching Tier 2 (per spec `docs/superpowers/specs/2026-07-09-mongo-replication-and-backup-hardening-design.md`).
- `scripts/mongo-backup.sh`, `scripts/mongo-restore.sh`, `scripts/state-backup.sh`, `scripts/state-restore.sh` are not modified in this plan — they remain the documented manual/pre-upgrade tools.
- Off-site (`RCLONE_REMOTE`) and heartbeat (`HEALTHCHECK_UUID`) env vars must default to empty/no-op — the pipeline must work with neither configured (dry-no-op), since the team configures real values after this lands.
- No new Python dependencies. No changes to `flymanager/` application code in this plan (infra/ops only).

---

## Task 1: Replica set topology in both Compose files

**Files:**
- Modify: `compose.yaml:2-16` (mongodb service), `compose.yaml:38-59` (app depends_on/environment), `compose.yaml:84-91` (worker depends_on), `compose.yaml:100-102` (volumes)
- Modify: `compose.synology.yaml:38-52` (mongodb service), `compose.synology.yaml:72-79` (app depends_on/environment), `compose.synology.yaml:129-133` (worker depends_on), `compose.synology.yaml:144-146` (volumes)

**Interfaces:**
- Produces: a `rs0` replica set reachable at `mongodb:27017,mongodb2:27017` from any other service on the compose network; `MONGO_URI` now includes `?replicaSet=rs0`.

- [ ] **Step 1: Edit `compose.yaml`'s `mongodb` service and add `mongodb2` + `mongo-init`**

Replace lines 2-16 (the `mongodb:` service block) with:

```yaml
  mongodb:
    image: mongo:7.0
    container_name: flymanager-mongodb
    restart: unless-stopped
    command: ["--replSet", "rs0", "--bind_ip_all"]
    ports:
      - "${MONGO_PUBLISHED_HOST:-127.0.0.1}:${MONGO_PUBLISHED_PORT:-27017}:27017"
    volumes:
      - mongo_data:/data/db
    healthcheck:
      test:
        ["CMD", "mongosh", "--quiet", "--eval", "db.adminCommand('ping').ok"]
      interval: 10s
      timeout: 5s
      retries: 10
      start_period: 10s

  mongodb2:
    image: mongo:7.0
    container_name: flymanager-mongodb2
    restart: unless-stopped
    command: ["--replSet", "rs0", "--bind_ip_all"]
    volumes:
      - mongo_data2:/data/db
    healthcheck:
      test:
        ["CMD", "mongosh", "--quiet", "--eval", "db.adminCommand('ping').ok"]
      interval: 10s
      timeout: 5s
      retries: 10
      start_period: 10s

  mongo-init:
    image: mongo:7.0
    container_name: flymanager-mongo-init
    restart: "no"
    depends_on:
      mongodb:
        condition: service_healthy
      mongodb2:
        condition: service_healthy
    entrypoint: ["/bin/bash", "-c"]
    command: >
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

- [ ] **Step 2: Update `compose.yaml`'s `app` service `depends_on` and `MONGO_URI`**

In the `app: &app` block, replace:

```yaml
    depends_on:
      mongodb:
        condition: service_healthy
      redis:
        condition: service_healthy
```

with:

```yaml
    depends_on:
      mongodb:
        condition: service_healthy
      mongo-init:
        condition: service_completed_successfully
      redis:
        condition: service_healthy
```

And replace the `MONGO_URI` line inside `environment: &app-environment`:

```yaml
      MONGO_URI: ${MONGO_URI:-mongodb://mongodb:27017}
```

with:

```yaml
      MONGO_URI: ${MONGO_URI:-mongodb://mongodb:27017,mongodb2:27017/?replicaSet=rs0}
```

- [ ] **Step 3: Update `compose.yaml`'s `worker` service `depends_on`**

Replace the `worker` service's `depends_on` block (currently identical to `app`'s original):

```yaml
    depends_on:
      mongodb:
        condition: service_healthy
      redis:
        condition: service_healthy
```

with:

```yaml
    depends_on:
      mongodb:
        condition: service_healthy
      mongo-init:
        condition: service_completed_successfully
      redis:
        condition: service_healthy
```

(`worker`'s `environment:` already inherits `MONGO_URI` via the `<<: *app-environment` merge — no separate edit needed there.)

- [ ] **Step 4: Add the `mongo_data2` volume to `compose.yaml`**

Replace:

```yaml
volumes:
  mongo_data:
  redis_data:
```

with:

```yaml
volumes:
  mongo_data:
  mongo_data2:
  redis_data:
```

- [ ] **Step 5: Repeat the same edits in `compose.synology.yaml`**

Replace lines 38-52 (`mongodb:` service) with the same `mongodb`/`mongodb2`/`mongo-init` block from Step 1 (identical content — this file doesn't override any Mongo-specific settings).

Replace the `app` service's `depends_on` (lines 72-76) the same way as Step 2, and replace:

```yaml
      MONGO_URI: mongodb://mongodb:27017
```

with:

```yaml
      MONGO_URI: mongodb://mongodb:27017,mongodb2:27017/?replicaSet=rs0
```

Replace the `worker` service's `depends_on` (lines 129-133) the same way as Step 3.

Replace:

```yaml
volumes:
  mongo_data:
  redis_data:
```

with:

```yaml
volumes:
  mongo_data:
  mongo_data2:
  redis_data:
```

- [ ] **Step 6: Validate both Compose files parse and resolve**

Run: `docker compose -f compose.yaml config --quiet && echo "compose.yaml OK"`
Expected: `compose.yaml OK` with no errors (undefined required vars like `SECRET_KEY`/`APP_IMAGE` in `compose.synology.yaml` will fail `config` unless supplied — for that file, run with dummy values instead:

Run: `SECRET_KEY=dummy APP_IMAGE=dummy/dummy:latest docker compose -f compose.synology.yaml config --quiet && echo "compose.synology.yaml OK"`
Expected: `compose.synology.yaml OK` with no errors.

- [ ] **Step 7: Bring up the replica set locally and verify it initializes**

Run: `docker compose -f compose.yaml up -d mongodb mongodb2 mongo-init`
Wait ~15s, then run: `docker compose -f compose.yaml exec mongodb mongosh --quiet --eval 'rs.status().members.map(m => ({name: m.name, stateStr: m.stateStr}))'`
Expected: an array showing `mongodb:27017` as `PRIMARY` and `mongodb2:27017` as `SECONDARY` (may show `STARTUP2` briefly right after Step 7 — re-run after another 10-15s if so).

Then tear down: `docker compose -f compose.yaml down -v` (safe here — this is a fresh local verification run with no real data yet; never run `down -v` against a stack holding real data).

- [ ] **Step 8: Commit**

```bash
git add compose.yaml compose.synology.yaml
git commit -m "$(cat <<'EOF'
Add Mongo replica set (primary + secondary + mongo-init) to both Compose stacks

Adds mongodb2 as a second replica-set member and a one-shot mongo-init
container that idempotently runs rs.initiate(), per
RESILIENCE_STANDARD_GUIDELINE.md section 11. MONGO_URI now targets both
members with replicaSet=rs0. The existing mongodb service/volume is
unchanged apart from gaining --replSet rs0 on its command line, so
existing data is untouched by this change (see the migration procedure
in BACKUP_AND_RECOVERY.md, added in a later task of this plan).
EOF
)"
```

---

## Task 2: Container-native Mongo backup script with a dry-run mode

**Files:**
- Create: `scripts/container-mongo-backup.sh`
- Test: `tests/test_container_mongo_backup_script.sh` (new, shell-based; not pytest — this script has no Python surface)

**Interfaces:**
- Produces: `scripts/container-mongo-backup.sh`, invoked with no arguments, reading `MONGO_HOST`, `MONGO_PORT`, `BACKUP_KEEP_DAYS`, `RCLONE_REMOTE`, `HEALTHCHECK_UUID`, and `BACKUP_DRY_RUN` from the environment, writing archives to `/backups`.

- [ ] **Step 1: Write the dry-run test script**

Create `tests/test_container_mongo_backup_script.sh`:

```sh
#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
SCRIPT="$ROOT_DIR/scripts/container-mongo-backup.sh"

TEST_BACKUP_DIR=$(mktemp -d)
trap 'rm -rf "$TEST_BACKUP_DIR"' EXIT

failures=0

check() {
    description=$1
    shift
    if "$@"; then
        printf 'PASS: %s\n' "$description"
    else
        printf 'FAIL: %s\n' "$description"
        failures=$((failures + 1))
    fi
}

# 1. Dry run with no remote/heartbeat configured must succeed and print
#    what it would do, without requiring a real mongod, rclone remote,
#    or network access.
output=$(BACKUP_DRY_RUN=1 BACKUP_DIR="$TEST_BACKUP_DIR" MONGO_HOST=nonexistent-host MONGO_PORT=27017 BACKUP_KEEP_DAYS=14 sh "$SCRIPT" 2>&1)
check "dry run exits 0" test $? -eq 0
check "dry run mentions mongodump target" sh -c "printf '%s' \"$output\" | grep -q 'nonexistent-host:27017'"
check "dry run does not create an archive file" sh -c "[ -z \"\$(find '$TEST_BACKUP_DIR' -maxdepth 1 -type f)\" ]"

# 2. Dry run with rclone/heartbeat configured must mention both steps
#    without actually invoking rclone/curl.
output=$(BACKUP_DRY_RUN=1 BACKUP_DIR="$TEST_BACKUP_DIR" MONGO_HOST=nonexistent-host MONGO_PORT=27017 BACKUP_KEEP_DAYS=14 RCLONE_REMOTE=dummy:remote/path HEALTHCHECK_UUID=dummy-uuid sh "$SCRIPT" 2>&1)
check "dry run mentions rclone remote" sh -c "printf '%s' \"$output\" | grep -q 'dummy:remote/path'"
check "dry run mentions heartbeat UUID" sh -c "printf '%s' \"$output\" | grep -q 'dummy-uuid'"

if [ "$failures" -gt 0 ]; then
    printf '%d check(s) failed\n' "$failures"
    exit 1
fi

printf 'All checks passed\n'
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `chmod +x tests/test_container_mongo_backup_script.sh && ./tests/test_container_mongo_backup_script.sh`
Expected: FAIL — `scripts/container-mongo-backup.sh: No such file or directory` (or similar), since the script doesn't exist yet.

- [ ] **Step 3: Write `scripts/container-mongo-backup.sh`**

```sh
#!/bin/sh
set -eu

MONGO_HOST=${MONGO_HOST:-mongodb}
MONGO_PORT=${MONGO_PORT:-27017}
BACKUP_DIR=${BACKUP_DIR:-/backups}
BACKUP_KEEP_DAYS=${BACKUP_KEEP_DAYS:-14}
RCLONE_REMOTE=${RCLONE_REMOTE:-}
HEALTHCHECK_UUID=${HEALTHCHECK_UUID:-}
BACKUP_DRY_RUN=${BACKUP_DRY_RUN:-0}

is_truthy() {
    case "${1:-}" in
        1|true|TRUE|yes|YES|on|ON)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
ARCHIVE="$BACKUP_DIR/flymanager_mongodb_${TIMESTAMP}.archive.gz"

if is_truthy "$BACKUP_DRY_RUN"; then
    echo "Dry run: would run mongodump --host ${MONGO_HOST}:${MONGO_PORT} --oplog --archive=$ARCHIVE --gzip"
    echo "Dry run: would write checksum to ${ARCHIVE}.sha256"
    echo "Dry run: would prune archives older than $BACKUP_KEEP_DAYS days in $BACKUP_DIR"
    if [ -n "$RCLONE_REMOTE" ]; then
        echo "Dry run: would run rclone copy $ARCHIVE $RCLONE_REMOTE/"
    fi
    if [ -n "$HEALTHCHECK_UUID" ]; then
        echo "Dry run: would ping https://hc-ping.com/$HEALTHCHECK_UUID"
    fi
    exit 0
fi

mkdir -p "$BACKUP_DIR"

mongodump \
    --host "${MONGO_HOST}:${MONGO_PORT}" \
    --oplog \
    --archive="$ARCHIVE" \
    --gzip

sha256sum "$ARCHIVE" > "${ARCHIVE}.sha256"

find "$BACKUP_DIR" -maxdepth 1 -type f -name 'flymanager_mongodb_*.archive.gz*' \
    -mtime +"$BACKUP_KEEP_DAYS" -delete

if [ -n "$RCLONE_REMOTE" ]; then
    rclone copy "$ARCHIVE" "${RCLONE_REMOTE}/"
    rclone copy "${ARCHIVE}.sha256" "${RCLONE_REMOTE}/"
fi

if [ -n "$HEALTHCHECK_UUID" ]; then
    curl -fsS -m 10 --retry 3 "https://hc-ping.com/${HEALTHCHECK_UUID}" > /dev/null || true
fi

echo "Backup complete: $ARCHIVE"
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `chmod +x scripts/container-mongo-backup.sh && ./tests/test_container_mongo_backup_script.sh`
Expected: `All checks passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/container-mongo-backup.sh tests/test_container_mongo_backup_script.sh
git commit -m "$(cat <<'EOF'
Add container-native Mongo backup script with oplog capture

New scripts/container-mongo-backup.sh runs mongodump --oplog directly
against the compose network (no docker compose exec indirection),
writes a checksum, prunes local retention, and optionally pushes
off-site via rclone and pings a healthcheck.io heartbeat - all gated
behind empty-by-default env vars so it's a safe no-op until configured.
Includes a BACKUP_DRY_RUN mode and a shell-based test exercising it.
EOF
)"
```

---

## Task 3: Add the `mongo-backup` service to both Compose files, plus `.env.example`

**Files:**
- Modify: `compose.yaml` (add `mongo-backup` service, add `mongo_backups` volume)
- Modify: `compose.synology.yaml` (same)
- Modify: `.env.example` (add new backup-container env vars)

**Interfaces:**
- Consumes: `scripts/container-mongo-backup.sh` from Task 2.
- Produces: a running `mongo-backup` service in both stacks that calls the Task 2 script every `MONGO_BACKUP_INTERVAL_SECONDS` (default 21600 = 6 hours).

- [ ] **Step 1: Add the `mongo-backup` service to `compose.yaml`**

Insert immediately after the `mongo-init` service block added in Task 1 (before the blank line preceding `  redis:`):

```yaml

  mongo-backup:
    image: rclone/rclone:latest
    container_name: flymanager-mongo-backup
    restart: unless-stopped
    depends_on:
      mongodb:
        condition: service_healthy
      mongo-init:
        condition: service_completed_successfully
    volumes:
      - mongo_backups:/backups
      - ./scripts/container-mongo-backup.sh:/usr/local/bin/container-mongo-backup.sh:ro
      - ${RCLONE_CONFIG_PATH:-./scripts/rclone.conf}:/config/rclone/rclone.conf:ro
    environment:
      MONGO_HOST: mongodb
      MONGO_PORT: "27017"
      BACKUP_DIR: /backups
      BACKUP_KEEP_DAYS: ${BACKUP_KEEP_DAYS:-14}
      RCLONE_REMOTE: ${RCLONE_REMOTE:-}
      HEALTHCHECK_UUID: ${HEALTHCHECK_UUID:-}
    entrypoint: ["/bin/sh", "-c"]
    command: >
      apk add --no-cache mongodb-tools bash curl > /dev/null 2>&1 &&
      chmod +x /usr/local/bin/container-mongo-backup.sh &&
      /usr/local/bin/container-mongo-backup.sh &&
      while true; do sleep "${MONGO_BACKUP_INTERVAL_SECONDS:-21600}"; /usr/local/bin/container-mongo-backup.sh; done
    healthcheck:
      disable: true
```

Then update the `volumes:` block at the bottom (from Task 1's `mongo_data2` edit):

```yaml
volumes:
  mongo_data:
  mongo_data2:
  mongo_backups:
  redis_data:
```

- [ ] **Step 2: Add the same `mongo-backup` service to `compose.synology.yaml`**

Insert the identical service block (from Step 1) after `compose.synology.yaml`'s `mongo-init` service.

Update its `volumes:` block:

```yaml
volumes:
  mongo_data:
  mongo_data2:
  mongo_backups:
  redis_data:
```

- [ ] **Step 3: Create a placeholder `scripts/rclone.conf` so the bind mount doesn't fail when unconfigured**

Create `scripts/rclone.conf`:

```ini
# Placeholder rclone config. Replace with a real remote before relying on
# off-site backup pushes - see BACKUP_AND_RECOVERY.md's "Backup container"
# section for how to generate one (`rclone config`) and where to point
# RCLONE_CONFIG_PATH / RCLONE_REMOTE.
#
# Until a real remote is configured here, RCLONE_REMOTE should stay unset
# in .env, and container-mongo-backup.sh will skip the off-site push step
# entirely (see scripts/container-mongo-backup.sh).
```

Then check whether `scripts/rclone.conf` needs a `.gitignore` entry so a real, credential-bearing config never gets committed by accident:

Run: `grep -n "rclone" .gitignore || echo "not present"`

If `not present`, add this line to `.gitignore` (create the file if it doesn't exist, otherwise append):

```
scripts/rclone.conf
```

Then re-add the placeholder file specifically with `git add -f`, since it's intentionally tracked as a template despite the pattern:

(handled explicitly in the commit step below with `git add -f`)

- [ ] **Step 4: Add new env vars to `.env.example`**

Append to the end of `.env.example` (after the existing `RESTORE_PRECHECK_DIR=backups/restore-preflight` line):

```
RCLONE_REMOTE=
RCLONE_CONFIG_PATH=./scripts/rclone.conf
HEALTHCHECK_UUID=
MONGO_BACKUP_INTERVAL_SECONDS=21600
```

- [ ] **Step 5: Validate both Compose files still parse**

Run: `docker compose -f compose.yaml config --quiet && echo "compose.yaml OK"`
Expected: `compose.yaml OK`

Run: `SECRET_KEY=dummy APP_IMAGE=dummy/dummy:latest docker compose -f compose.synology.yaml config --quiet && echo "compose.synology.yaml OK"`
Expected: `compose.synology.yaml OK`

- [ ] **Step 6: Commit**

```bash
git add -f compose.yaml compose.synology.yaml .env.example scripts/rclone.conf .gitignore
git commit -m "$(cat <<'EOF'
Add mongo-backup container to both Compose stacks

New mongo-backup service (rclone/rclone image, per
RESILIENCE_STANDARD_GUIDELINE.md section 11.3) runs
container-mongo-backup.sh every 6 hours by default. Off-site push and
heartbeat stay no-ops until RCLONE_REMOTE/HEALTHCHECK_UUID are set in
.env. scripts/rclone.conf is a placeholder template (gitignored once a
real config replaces it) so the bind mount doesn't fail out of the box.
EOF
)"
```

---

## Task 4: Retire host-level scheduling of Mongo backups

**Files:**
- Delete: `deploy/systemd/flymanager-mongo-backup.service`
- Delete: `deploy/systemd/flymanager-mongo-backup.timer`
- Modify: `deploy/cron/flymanager-backups.crontab.example`

**Interfaces:** none (deployment config only).

- [ ] **Step 1: Delete the systemd unit files for scheduled Mongo backups**

```bash
git rm deploy/systemd/flymanager-mongo-backup.service deploy/systemd/flymanager-mongo-backup.timer
```

- [ ] **Step 2: Remove the Mongo backup line from the cron example**

`deploy/cron/flymanager-backups.crontab.example` currently reads:

```
# FlyManager backup schedule example
# Adjust /opt/flymanager and BACKUP_OFFSITE_DIR for your host.

0 * * * * cd /opt/flymanager && COMPOSE_ARGS="-f compose.yaml -f compose.production.yaml" BACKUP_OFFSITE_DIR=/srv/flymanager-backups ./scripts/mongo-backup.sh >> /opt/flymanager/backups/logs/mongo-backup.log 2>&1
15 2 * * * cd /opt/flymanager && COMPOSE_ARGS="-f compose.yaml -f compose.production.yaml" BACKUP_OFFSITE_DIR=/srv/flymanager-backups ./scripts/state-backup.sh >> /opt/flymanager/backups/logs/state-backup.log 2>&1
```

Replace it with:

```
# FlyManager backup schedule example
# Adjust /opt/flymanager and BACKUP_OFFSITE_DIR for your host.
#
# Routine/scheduled MongoDB backups now run from the in-stack
# mongo-backup Compose service (see BACKUP_AND_RECOVERY.md) instead of a
# host cron entry. scripts/mongo-backup.sh remains available for manual,
# on-demand backups (e.g. immediately before a deploy).

15 2 * * * cd /opt/flymanager && COMPOSE_ARGS="-f compose.yaml -f compose.production.yaml" BACKUP_OFFSITE_DIR=/srv/flymanager-backups ./scripts/state-backup.sh >> /opt/flymanager/backups/logs/state-backup.log 2>&1
```

- [ ] **Step 3: Confirm no other file references the deleted systemd units**

Run: `grep -rl "flymanager-mongo-backup" --include="*.md" --include="*.sh" . 2>/dev/null | grep -v '.git/'`
Expected: no output (if any file references it, note it and update that reference to point at the `mongo-backup` Compose service instead — this is handled in Task 6's documentation pass if `BACKUP_AND_RECOVERY.md` is the only hit).

- [ ] **Step 4: Commit**

```bash
git add deploy/cron/flymanager-backups.crontab.example
git commit -m "$(cat <<'EOF'
Retire host-level scheduling of Mongo backups

Scheduled Mongo backups now come from the mongo-backup Compose service
(added in the prior task) rather than a systemd timer or cron entry.
scripts/mongo-backup.sh remains for manual/pre-upgrade use; state
backup's systemd timer and cron entry are untouched.
EOF
)"
```

---

## Task 5: Restore drill script

**Files:**
- Create: `scripts/mongo-restore-drill.sh`

**Interfaces:**
- Produces: `scripts/mongo-restore-drill.sh <path-to-archive>`, prints post-restore sanity counts, always tears down its scratch container even on failure.

- [ ] **Step 1: Write `scripts/mongo-restore-drill.sh`**

```sh
#!/bin/sh
set -eu

if [ $# -lt 1 ] || [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
    cat <<'EOF'
Usage: ./scripts/mongo-restore-drill.sh path/to/backup.archive.gz

Restore a MongoDB archive into a fully disposable, isolated scratch
container - never the live stack - and print post-restore sanity counts.
Safe to run against a copy of a real production archive.

The scratch container is always removed on exit, including on failure.
EOF
    exit 0
fi

ARCHIVE_INPUT=$1
ARCHIVE_DIR=$(cd "$(dirname "$ARCHIVE_INPUT")" && pwd)
ARCHIVE_PATH="$ARCHIVE_DIR/$(basename "$ARCHIVE_INPUT")"

if [ ! -f "$ARCHIVE_PATH" ]; then
    echo "Backup archive not found: $ARCHIVE_PATH" >&2
    exit 1
fi

DRILL_CONTAINER="flymanager-restore-drill-$$"

cleanup() {
    docker rm -f "$DRILL_CONTAINER" > /dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

echo "Starting scratch Mongo container: $DRILL_CONTAINER"
docker run -d --name "$DRILL_CONTAINER" mongo:7.0 > /dev/null

echo "Waiting for scratch mongod to accept connections..."
attempt=0
until docker exec "$DRILL_CONTAINER" mongosh --quiet --eval 'db.adminCommand("ping").ok' > /dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 30 ]; then
        echo "Scratch mongod never became ready" >&2
        exit 1
    fi
    sleep 1
done

echo "Copying archive into scratch container..."
docker cp "$ARCHIVE_PATH" "$DRILL_CONTAINER:/tmp/drill.archive.gz"

echo "Restoring archive..."
docker exec "$DRILL_CONTAINER" sh -c 'mongorestore --archive=/tmp/drill.archive.gz --gzip'

echo "--- Post-restore sanity counts ---"
docker exec "$DRILL_CONTAINER" mongosh --quiet --eval '
  const names = db.adminCommand({listDatabases: 1}).databases.map(d => d.name).filter(n => !["admin", "local", "config"].includes(n));
  names.forEach(name => {
    const target = db.getSiblingDB(name);
    print(name + ":");
    target.getCollectionNames().forEach(coll => {
      print("  " + coll + ": " + target.getCollection(coll).countDocuments());
    });
  });
'

echo "Restore drill completed successfully for $ARCHIVE_PATH"
```

- [ ] **Step 2: Make it executable and confirm `--help` works without Docker**

Run: `chmod +x scripts/mongo-restore-drill.sh && ./scripts/mongo-restore-drill.sh --help`
Expected: the usage text is printed, exit code 0.

- [ ] **Step 3: Confirm the no-argument case fails cleanly**

Run: `./scripts/mongo-restore-drill.sh; echo "exit=$?"`
Expected: usage text printed (since `-h`/`--help`/missing-arg all route to the same usage block per `[ $# -lt 1 ]`), `exit=0`.

- [ ] **Step 4: Commit**

```bash
git add scripts/mongo-restore-drill.sh
git commit -m "$(cat <<'EOF'
Add isolated Mongo restore drill script

scripts/mongo-restore-drill.sh restores a given archive into a fully
disposable scratch mongod container (never the live stack) and prints
per-collection sanity counts across all restored databases, so a drill
can be run safely against a copy of a real production archive.
EOF
)"
```

---

## Task 6: Documentation updates

**Files:**
- Modify: `BACKUP_AND_RECOVERY.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Add a "Replica Set" section**

Insert a new section immediately after the `## Backup Model` section (after line 35, before `## What Gets Protected`):

```markdown
## Replica Set

The Compose stacks (`compose.yaml` and `compose.synology.yaml`) run a
2-member Mongo replica set: `mongodb` (priority 2, preferred primary) and
`mongodb2` (priority 1, secondary). A one-shot `mongo-init` service runs
`rs.initiate()` idempotently on first startup (it checks `rs.status()`
first and does nothing if the set already exists).

### Verifying replica set health

```bash
docker compose exec mongodb mongosh --quiet --eval 'rs.status().members.map(m => ({name: m.name, stateStr: m.stateStr}))'
```

Expect one `PRIMARY` and one `SECONDARY`.

### Migrating an existing single-instance deployment

If you're upgrading a deployment that already has real data in the
`mongodb` service's `mongo_data` volume (as opposed to a fresh install),
follow this procedure. It does not move, copy, or reformat any existing
data - it only changes the mongod process's replication mode and adds a
new, initially-empty secondary that syncs from the existing primary.

1. Take a manual Mongo backup first as a safety net: `./scripts/mongo-backup.sh`
2. Stop the stack: `docker compose down` (the `mongo_data` volume persists - do not add `-v`)
3. Pull the updated `compose.yaml`/`compose.synology.yaml`
4. Start the stack: `docker compose up -d`
5. Verify `rs.status()` (see above) shows `mongodb` as `PRIMARY` with your
   existing data intact, and `mongodb2` reaching `SECONDARY` state (this
   can take a while on a large database - the secondary is performing a
   full initial sync from the primary).
6. Verify `/health/ready` and a few representative UI reads.

Rehearse this once against a restored copy of your data (see the Restore
Drill section below) before running it against the real production stack,
so the migration procedure isn't tried for the first time on production.
```

- [ ] **Step 2: Update the "Mongo backup" script reference section**

In the existing `### Mongo backup` section (originally around line 107),
insert this note immediately below the heading, before the existing
"Purpose:" bullet list:

```markdown
**Routine, scheduled backups now come from the in-stack `mongo-backup`
Compose service** (see "Backup Container" below), not from this script's
systemd timer or cron entry (both retired). This script remains the tool
for manual, on-demand backups - for example immediately before a deploy
(see "Pre-Upgrade Backup Procedure" below).
```

- [ ] **Step 3: Add a new "Backup Container" section**

Insert a new section immediately after the existing `### Mongo restore`
section (before `### State backup`):

```markdown
### Backup container

Service: `mongo-backup` in `compose.yaml` / `compose.synology.yaml`.
Script: [scripts/container-mongo-backup.sh](scripts/container-mongo-backup.sh).

Purpose:

- run on a loop (every `MONGO_BACKUP_INTERVAL_SECONDS`, default 21600 = 6
  hours) inside the Compose network, with no host-level scheduler
  dependency
- `mongodump --oplog --archive --gzip` directly against `mongodb:27017`
  (requires the replica set from the "Replica Set" section above -
  `--oplog` only works against a real replica-set member)
- write a `.sha256` checksum alongside each archive
- prune local archives in the `mongo_backups` volume older than
  `BACKUP_KEEP_DAYS` (default 14)
- optionally push the archive and checksum off-site via `rclone`, if
  `RCLONE_REMOTE` is set
- optionally ping a healthcheck.io dead-man's-switch URL on success, if
  `HEALTHCHECK_UUID` is set

Configuring off-site push and heartbeat monitoring:

1. Run `rclone config` to create a remote (see rclone's docs for your
   provider - Google Drive, S3, Backblaze B2, etc.)
2. Save the resulting config to the path pointed at by
   `RCLONE_CONFIG_PATH` (default `./scripts/rclone.conf` - **never commit
   a real config with credentials**; the tracked file at that path is a
   placeholder template only)
3. Set `RCLONE_REMOTE` in `.env` to the remote name and path, e.g.
   `gdrive:flymanager-backups`
4. Create a check in healthcheck.io (cron-style, matching your backup
   interval) and set `HEALTHCHECK_UUID` in `.env` to its UUID
5. Restart the `mongo-backup` service and confirm a successful ping is
   recorded in healthcheck.io after the next backup cycle

Until these are configured, the off-site push and heartbeat steps are
silent no-ops - local backups with checksums and retention still run.
```

- [ ] **Step 4: Add a "Restore Drill" subsection with the drill log**

Replace the existing `### Drill steps` list inside `## Recovery Drill
Procedure` (the numbered list currently reading "1. Prepare an isolated
host...") by inserting this new subsection immediately before it:

```markdown
### Automated drill tooling

[scripts/mongo-restore-drill.sh](scripts/mongo-restore-drill.sh) automates
steps 1-7 below against a fully disposable scratch container (never the
live stack):

```bash
./scripts/mongo-restore-drill.sh path/to/backup.archive.gz
```

It restores the archive into a throwaway `mongod` container, prints
per-collection document counts across every restored database, and always
removes the scratch container afterward (even on failure).

```

(leave the existing `### Drill steps` and `### Minimal drill checklist` sections as-is immediately after this new subsection)

- [ ] **Step 5: Commit**

```bash
git add BACKUP_AND_RECOVERY.md
git commit -m "$(cat <<'EOF'
Document replica set, backup container, and restore drill tooling

Adds a Replica Set section (topology + zero-data-loss migration
procedure for existing deployments), documents the new mongo-backup
container and its rclone/healthcheck.io configuration steps, and
documents the new automated restore-drill script.
EOF
)"
```

---

## Task 7: Execute one real restore drill and record the result

**Files:**
- Modify: `BACKUP_AND_RECOVERY.md` (append dated drill log entry)

**Interfaces:** none (this task produces evidence, not code).

- [ ] **Step 1: Stand up a local replica set with sample data**

```bash
docker compose -f compose.yaml up -d mongodb mongodb2 mongo-init
sleep 15
docker compose -f compose.yaml exec mongodb mongosh --quiet --eval '
  db.getSiblingDB("flymanager_drill_test").stocks.insertMany([
    {UniqueID: "drill-1", Genotype: "w1118"},
    {UniqueID: "drill-2", Genotype: "yw"}
  ]);
  db.getSiblingDB("flymanager_drill_test").crosses.insertOne({CrossID: "drill-cross-1"});
'
```

- [ ] **Step 2: Take a real backup archive with the Task 2 script, run directly (not via the container, to keep this step host-runnable)**

```bash
mkdir -p /tmp/flymanager-drill-backups
MONGO_HOST=127.0.0.1 MONGO_PORT=${MONGO_PUBLISHED_PORT:-27017} BACKUP_DIR=/tmp/flymanager-drill-backups BACKUP_KEEP_DAYS=14 ./scripts/container-mongo-backup.sh
```

Expected: output ending with `Backup complete: /tmp/flymanager-drill-backups/flymanager_mongodb_<timestamp>.archive.gz`. Note the exact archive filename and timestamp for the log entry in Step 4. (Requires `mongodump` available on the host - if unavailable, run this same command inside a throwaway `mongo:7.0` container with `/tmp/flymanager-drill-backups` and the compose network mounted/joined instead.)

- [ ] **Step 3: Run the restore drill against that archive**

```bash
time ./scripts/mongo-restore-drill.sh /tmp/flymanager-drill-backups/flymanager_mongodb_<timestamp>.archive.gz
```

(substitute the actual filename from Step 2's output)

Expected: output includes `flymanager_drill_test:` with `stocks: 2` and `crosses: 1` under it, ending with `Restore drill completed successfully for ...`. Note the wall-clock time reported by `time`.

- [ ] **Step 4: Record the dated drill result in `BACKUP_AND_RECOVERY.md`**

Append this subsection immediately after the "Automated drill tooling" subsection added in Task 6 (before the existing `### Drill steps`):

```markdown
### Drill log

| Date | Archive | Sanity result | Approx. RTO | Issues |
|---|---|---|---|---|
| 2026-07-09 | `flymanager_mongodb_<timestamp>.archive.gz` (test data: 2 stocks, 1 cross in `flymanager_drill_test`) | Counts matched exactly (stocks: 2, crosses: 1) | <fill in the `time` output from Step 3> | None |
```

Fill in the real archive filename and the real elapsed time reported by `time` in Task 7 Step 3 — do not leave placeholder text in the committed file.

- [ ] **Step 5: Tear down the local replica set used for this drill**

```bash
docker compose -f compose.yaml down -v
rm -rf /tmp/flymanager-drill-backups
```

(`-v` is safe here — this stack was stood up fresh in Step 1 purely for this drill and holds no real data.)

- [ ] **Step 6: Commit**

```bash
git add BACKUP_AND_RECOVERY.md
git commit -m "$(cat <<'EOF'
Record executed restore drill result

Ran scripts/mongo-restore-drill.sh against a real archive taken by
scripts/container-mongo-backup.sh from a local replica-set instance
with sample data; logged the dated result per
RESILIENCE_STANDARD_GUIDELINE.md's restore-drill requirement.
EOF
)"
```

---

## Self-Review Notes (for the plan author, not a task)

- Spec coverage: replica set topology + migration (Task 1, 6), backup container with oplog/rclone/heartbeat (Task 2, 3), retiring old scheduling (Task 4), restore drill tooling + execution (Task 5, 7), documentation (Task 6) — all five spec sections have a task.
- `scripts/rclone.conf` is intentionally committed as a placeholder (Task 3) and also intentionally gitignored going forward so a real, credential-bearing file an operator drops in later is never accidentally committed — Task 3 Step 3 handles the `git add -f` needed since the file matches its own new `.gitignore` pattern.
- Task 7 depends on Docker actually being available in the execution environment (to run `docker compose up`, `docker run`, `docker exec`). If the implementer's sandbox has no Docker daemon, report BLOCKED at Task 7 Step 1 rather than fabricating drill results — a drill log entry must reflect a real, observed run.
