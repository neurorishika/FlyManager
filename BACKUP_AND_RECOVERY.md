# FlyManager Backup And Recovery Guide

This document is the canonical backup and recovery reference for FlyManager.

Use it with [DEPLOYMENT.md](DEPLOYMENT.md) and [PLATFORM_DEPLOYMENT.md](PLATFORM_DEPLOYMENT.md) when you need to:

- understand what FlyManager backs up and what it does not
- configure backup retention and offsite copies
- run routine MongoDB and deployment-state backups
- restore after operator error, bad deploys, or host replacement
- perform isolated recovery drills
- explain the current limits of the system's data-loss posture

## Backup Model

FlyManager currently uses a two-part backup model:

1. MongoDB archive backups

   Created by [scripts/mongo-backup.sh](scripts/mongo-backup.sh).

   These protect the live application database, including stocks, crosses, settings, users, activities, schedules, and other Mongo-backed collections.

2. Deployment-state backups

   Created by [scripts/state-backup.sh](scripts/state-backup.sh).

   These protect non-Mongo state needed to recover a working deployment, including:

   - the active runtime env file
   - the `data/` directory
   - Compose and proxy configuration snapshots
   - optional Caddy runtime state when the production proxy is part of the selected stack

You need both archive types for a full recovery.

## What Gets Protected

### MongoDB archive scope

Mongo archives include the contents of the configured MongoDB database.

Typical protected data includes:

- stock records
- cross records
- tray metadata and placements stored in MongoDB
- user accounts and settings
- activity logs
- scheduler-related records stored in MongoDB
- imported workbook data after it has been swapped into place

### Deployment-state archive scope

State archives include file-backed deployment assets outside MongoDB.

Protected items include:

- the runtime env file resolved from `ENV_FILE`
- `data/`
- `compose.yaml`
- `compose.production.yaml`
- `deploy/Caddyfile`
- optional Caddy `/data` and `/config` volume snapshots when `BACKUP_INCLUDE_CADDY` allows it and the selected Compose stack defines the `proxy` service

### What is intentionally not included

The current scripts intentionally do not treat every generated file as critical state.

Notably excluded or treated as non-critical:

- `flask_session/`
- temporary files under `temp/`
- generated labels under `flymanager/app/static/generated_labels/`
- Git history and uncommitted working tree changes

Those exclusions are deliberate because these areas are transient, regenerable, or should be recovered through source control rather than backup archives.

## Current Data-Loss Guarantees

FlyManager does not currently provide true zero-data-loss guarantees for infrastructure failure.

Important constraint:

- the deployed architecture is a single-host Docker Compose stack with one MongoDB container and one primary app container

That means:

- if the host fails between scheduled backups, some recent writes can still be lost
- if the filesystem is corrupted before offsite copies complete, recent local backups can also be lost
- if the host is destroyed and no offsite copy exists, recovery is limited to whatever archives survive elsewhere

What the current backup work does provide:

- rolling logical database backups
- rolling file-state backups
- retention pruning
- checksum files
- optional second-copy export through `BACKUP_OFFSITE_DIR`
- safer restore procedures with preflight snapshots
- safer import behavior in the application so workbook imports no longer wipe collections before replacement is ready

Operationally, this is a strong single-host backup posture, but not synchronous replication or high-availability storage.

## Script Reference

### Mongo backup

Script: [scripts/mongo-backup.sh](scripts/mongo-backup.sh)

Purpose:

- create a compressed logical MongoDB archive
- write a `.sha256` checksum alongside the archive when a checksum utility is available
- prune older archives using the configured retention rules
- optionally copy the archive and checksum to `BACKUP_OFFSITE_DIR`

Default output:

- `backups/mongodb/`

Default name format:

- `flymanager_mongodb_YYYYMMDDTHHMMSSZ.archive.gz`

### Mongo restore

Script: [scripts/mongo-restore.sh](scripts/mongo-restore.sh)

Purpose:

- restore a Mongo archive into the selected Compose stack
- stop the `app` container before restore when it is running
- restart the `app` container after restore if it was previously running

Important behavior:

- restore uses `mongorestore --drop`
- this replaces the current database contents with the contents of the archive

### State backup

Script: [scripts/state-backup.sh](scripts/state-backup.sh)

Purpose:

- create a tarball archive for deployment state outside MongoDB
- embed a manifest describing what was included
- optionally capture Caddy runtime state
- prune old archives and optionally copy them offsite

Default output:

- `backups/state/`

Default name format:

- `flymanager_state_YYYYMMDDTHHMMSSZ.tar.gz`

Archive contents:

- `manifest.txt`
- `runtime/env-file`
- `runtime/data/`
- optional `runtime/caddy_data/`
- optional `runtime/caddy_config/`
- `config/` snapshots for selected deployment files

### State restore

Script: [scripts/state-restore.sh](scripts/state-restore.sh)

Purpose:

- restore deployment file state from a state archive
- preserve the current local env/data state under a preflight snapshot before overwrite
- optionally push archived Caddy runtime data back into the proxy volumes
- extract configuration snapshots for operator reference instead of automatically overwriting version-controlled files

Important behavior:

- the current `.env` and `data/` are preserved under `RESTORE_PRECHECK_DIR/<timestamp>/`
- archived config files are extracted into `reference-config/` under the preflight snapshot
- when `RESTORE_INCLUDE_CADDY=auto`, Caddy restore only happens if archived Caddy content exists and the selected Compose stack can actually support that restore path

## Environment Variables

### Shared path and retention variables

- `BACKUP_ROOT`
  Top-level backup directory. Defaults to `backups`.

- `MONGO_BACKUP_DIR`
  Mongo archive output directory. Defaults to `backups/mongodb`.

- `STATE_BACKUP_DIR`
  State archive output directory. Defaults to `backups/state`.

- `BACKUP_LOG_DIR`
  Intended location for host-level scheduler logs. Defaults to `backups/logs`.

- `BACKUP_OFFSITE_DIR`
  Optional directory where the scripts place a second copy of each archive and checksum.

- `BACKUP_KEEP_RECENT`
  Number of newest archives always retained.

- `BACKUP_KEEP_DAILY`
  Number of day-level checkpoints retained after the recent window.

- `STATE_BACKUP_KEEP_RECENT`
  Override for state backup recent retention.

- `STATE_BACKUP_KEEP_DAILY`
  Override for state backup daily retention.

### Caddy and restore behavior variables

- `BACKUP_INCLUDE_CADDY`
  Accepts `auto`, `1`, or `0`.

  - `auto`: include Caddy runtime state only if the selected Compose stack defines the `proxy` service and the capture path works
  - `1`: require Caddy runtime capture and fail if it cannot be completed
  - `0`: skip Caddy runtime capture entirely

- `RESTORE_INCLUDE_CADDY`
  Accepts `auto`, `1`, or `0`.

  - `auto`: restore Caddy runtime state only when the archive contains it and the restore path is available
  - `1`: require Caddy restore and fail if it cannot be completed
  - `0`: never attempt Caddy restore

- `RESTORE_PRECHECK_DIR`
  Directory used by [scripts/state-restore.sh](scripts/state-restore.sh) to store the local state snapshot before overwrite.

### Compose targeting variable

- `COMPOSE_ARGS`
  Extra Compose arguments used to target the correct stack.

Examples:

```bash
COMPOSE_ARGS="-f compose.yaml -f compose.production.yaml"
```

## Routine Backup Commands

### Base stack Mongo backup

```bash
./scripts/mongo-backup.sh
```

### Production stack Mongo backup

```bash
COMPOSE_ARGS="-f compose.yaml -f compose.production.yaml" ./scripts/mongo-backup.sh
```

### Base stack state backup

```bash
./scripts/state-backup.sh
```

### Production stack state backup

```bash
COMPOSE_ARGS="-f compose.yaml -f compose.production.yaml" ./scripts/state-backup.sh
```

### Custom output path

Mongo:

```bash
./scripts/mongo-backup.sh /srv/flymanager-backups/manual-pre-upgrade.archive.gz
```

State:

```bash
./scripts/state-backup.sh /srv/flymanager-backups/manual-pre-upgrade-state.tar.gz
```

### Backup dry-run and help

Mongo:

```bash
./scripts/mongo-backup.sh --help
BACKUP_DRY_RUN=1 ./scripts/mongo-backup.sh
```

State:

```bash
./scripts/state-backup.sh --help
BACKUP_DRY_RUN=1 ./scripts/state-backup.sh
```

## Restore Commands

### Mongo restore examples

Base stack:

```bash
./scripts/mongo-restore.sh backups/mongodb/flymanager_mongodb_YYYYMMDDTHHMMSSZ.archive.gz
```

Production stack:

```bash
COMPOSE_ARGS="-f compose.yaml -f compose.production.yaml" ./scripts/mongo-restore.sh backups/mongodb/flymanager_mongodb_YYYYMMDDTHHMMSSZ.archive.gz
```

### State restore examples

Base stack:

```bash
./scripts/state-restore.sh backups/state/flymanager_state_YYYYMMDDTHHMMSSZ.tar.gz
```

Production stack:

```bash
COMPOSE_ARGS="-f compose.yaml -f compose.production.yaml" ./scripts/state-restore.sh backups/state/flymanager_state_YYYYMMDDTHHMMSSZ.tar.gz
```

### Restore dry-run and help

```bash
./scripts/state-restore.sh --help
BACKUP_DRY_RUN=1 ./scripts/state-restore.sh backups/state/flymanager_state_YYYYMMDDTHHMMSSZ.tar.gz
```

## Restore Order

For full-host recovery, use this order:

1. Recover or recreate the repository checkout on the target host.
2. Restore deployment state with [scripts/state-restore.sh](scripts/state-restore.sh).
3. Start the Docker Compose stack.
4. Restore MongoDB with [scripts/mongo-restore.sh](scripts/mongo-restore.sh).
5. Verify `/health` and `/health/ready`.
6. Verify representative UI operations.

Why this order matters:

- the state restore puts the env file and file-backed runtime data back first
- the stack can then start with the correct runtime configuration
- the Mongo restore runs against the intended database and application configuration

## Pre-Upgrade Backup Procedure

Before any production upgrade:

1. Confirm the current stack is healthy.
2. Run a Mongo backup.
3. Run a state backup.
4. Confirm the new archives and checksum files exist.
5. If `BACKUP_OFFSITE_DIR` is configured, confirm the second copy exists there as well.
6. Only then pull code and redeploy.

Suggested command sequence:

```bash
COMPOSE_ARGS="-f compose.yaml -f compose.production.yaml" ./scripts/mongo-backup.sh
COMPOSE_ARGS="-f compose.yaml -f compose.production.yaml" ./scripts/state-backup.sh
git pull
docker compose -f compose.yaml -f compose.production.yaml up -d --build
```

## Recommended Backup Policy

Recommended minimum policy for single-host production:

- Mongo archive every hour
- state backup once per day
- one additional Mongo and state backup immediately before every deploy
- second-copy export to `BACKUP_OFFSITE_DIR` or equivalent mounted target
- periodic isolated recovery drills

Example starting retention:

- `BACKUP_KEEP_RECENT=48`
- `BACKUP_KEEP_DAILY=14`
- `STATE_BACKUP_KEEP_RECENT=14`
- `STATE_BACKUP_KEEP_DAILY=30`

That gives:

- roughly two days of dense Mongo restore points
- two weeks of daily Mongo checkpoints
- two weeks of recent state archives
- one month of daily state checkpoints

Adjust upward if the system is heavily used or if maintenance windows are infrequent.

## Offsite Copy Strategy

`BACKUP_OFFSITE_DIR` is intentionally simple: it copies the finished archive and checksum into a second directory visible on the host.

Common uses:

- a second Synology shared folder
- a mounted external drive on Ubuntu
- a mounted backup filesystem on AWS or GCP
- a host-managed sync path that another tool mirrors to object storage

Important limitation:

- the FlyManager scripts do not push directly to S3, GCS, or another remote object store

If you need object storage, mount or sync a local path at the host level and point `BACKUP_OFFSITE_DIR` there.

## Checksums And Verification

When a SHA-256 utility is available, backup scripts write a sibling checksum file:

- Mongo archive: `.archive.gz.sha256`
- state archive: `.tar.gz.sha256`

On macOS and many Unix systems, you can verify a checksum with:

```bash
shasum -a 256 backups/state/flymanager_state_YYYYMMDDTHHMMSSZ.tar.gz
cat backups/state/flymanager_state_YYYYMMDDTHHMMSSZ.tar.gz.sha256
```

On Linux systems that provide `sha256sum`:

```bash
sha256sum backups/mongodb/flymanager_mongodb_YYYYMMDDTHHMMSSZ.archive.gz
cat backups/mongodb/flymanager_mongodb_YYYYMMDDTHHMMSSZ.archive.gz.sha256
```

## Interpreting The State Manifest

Each state archive contains `manifest.txt`.

Typical fields:

- `generated_at_utc`
- `root_dir`
- `compose_args`
- `includes_env`
- `env_restore_path`
- `includes_data`
- `includes_caddy`

The most important field during restore is `env_restore_path`.

It tells the restore script where the archived runtime env file should be written relative to the selected root.

## Failure Modes And Safe Handling

### Backup succeeds but Caddy is not included

This is expected when:

- `BACKUP_INCLUDE_CADDY=0`
- `BACKUP_INCLUDE_CADDY=auto` and the selected stack does not define `proxy`
- Docker Compose is not available and the script is only backing up plain files

In those cases, the manifest records `includes_caddy=no`.

### State restore warns and skips Caddy

This is expected when:

- `RESTORE_INCLUDE_CADDY=auto`
- the archive contains Caddy content
- the selected environment cannot actually perform the Caddy restore path

The restore still applies env/data and preserves local state in the preflight snapshot.

### Mongo restore is destructive

Mongo restore uses `--drop`.

That means:

- the target database contents are replaced by the archive contents
- you should always know which archive you are restoring before running the command

### State restore overwrites env/data

This is intentional.

The safety control is the preflight snapshot under `RESTORE_PRECHECK_DIR`, which captures the pre-restore local state before overwrite.

## Recovery Drill Procedure

Perform this on a safe host or sandbox, not on the live production deployment.

### Goal

Prove that:

- state archives are usable
- Mongo archives are usable
- the documented restore order produces a working system

### Drill steps

1. Prepare an isolated host or disposable copy of the repo.
2. Copy in a known-good state archive and Mongo archive.
3. Restore deployment state.
4. Start the stack.
5. Restore MongoDB.
6. Verify readiness endpoints.
7. Log in and inspect representative records.
8. Record the exact archive names and drill date.

### Minimal drill checklist

- env file restored to the expected path
- `data/` restored with expected contents
- state restore preflight snapshot created
- config snapshot extracted under `reference-config/`
- Mongo restore completed successfully
- `/health` returned `200`
- `/health/ready` returned `200`
- user login worked

## Platform Notes

### Synology

- use DSM Task Scheduler to call the shared scripts from the repository checkout
- point `BACKUP_OFFSITE_DIR` at another shared folder or mounted backup target
- keep a copy of recent archives off the main NAS volume when possible

### Ubuntu

- use cron or systemd timers
- use the versioned templates under `deploy/cron/` and `deploy/systemd/` as the starting point

### AWS and GCP

- keep host-local archives for fast restore
- also copy or sync them to a second durable location outside the VM lifecycle
- combine logical backups with volume or disk snapshot policies if faster infrastructure recovery is needed

## Files To Review During Backup Operations

- [scripts/backup-common.sh](scripts/backup-common.sh)
- [scripts/mongo-backup.sh](scripts/mongo-backup.sh)
- [scripts/mongo-restore.sh](scripts/mongo-restore.sh)
- [scripts/state-backup.sh](scripts/state-backup.sh)
- [scripts/state-restore.sh](scripts/state-restore.sh)
- [.env.example](.env.example)
- [deploy/cron/flymanager-backups.crontab.example](deploy/cron/flymanager-backups.crontab.example)
- [deploy/systemd/flymanager-mongo-backup.service](deploy/systemd/flymanager-mongo-backup.service)
- [deploy/systemd/flymanager-mongo-backup.timer](deploy/systemd/flymanager-mongo-backup.timer)
- [deploy/systemd/flymanager-state-backup.service](deploy/systemd/flymanager-state-backup.service)
- [deploy/systemd/flymanager-state-backup.timer](deploy/systemd/flymanager-state-backup.timer)

## Operator Validation Checklist

Before calling backup setup complete, verify all of the following:

- the target host has both backup scripts available
- backup directories exist and are writable
- a Mongo archive can be created successfully
- a state archive can be created successfully
- checksum files are written when expected
- retention pruning behaves as expected
- `BACKUP_OFFSITE_DIR` copies are present when configured
- a state restore dry-run behaves as expected
- a real isolated state restore has been tested
- a real isolated Mongo restore has been tested
- the application returns `200` from `/health` and `/health/ready` after restore

## Quick Reference

Production backup pair:

```bash
COMPOSE_ARGS="-f compose.yaml -f compose.production.yaml" ./scripts/mongo-backup.sh
COMPOSE_ARGS="-f compose.yaml -f compose.production.yaml" ./scripts/state-backup.sh
```

Production restore pair:

```bash
COMPOSE_ARGS="-f compose.yaml -f compose.production.yaml" ./scripts/state-restore.sh backups/state/flymanager_state_YYYYMMDDTHHMMSSZ.tar.gz
COMPOSE_ARGS="-f compose.yaml -f compose.production.yaml" ./scripts/mongo-restore.sh backups/mongodb/flymanager_mongodb_YYYYMMDDTHHMMSSZ.archive.gz
```

Help and dry-run:

```bash
./scripts/mongo-backup.sh --help
./scripts/state-backup.sh --help
./scripts/state-restore.sh --help
BACKUP_DRY_RUN=1 ./scripts/mongo-backup.sh
BACKUP_DRY_RUN=1 ./scripts/state-backup.sh
BACKUP_DRY_RUN=1 ./scripts/state-restore.sh backups/state/flymanager_state_YYYYMMDDTHHMMSSZ.tar.gz
```
