# FlyManager Deployment Guide

This document is the canonical deployment reference for FlyManager.

It covers:

- local Docker deployment
- public production deployment with HTTPS
- platform-specific deployment runbooks
- required environment variables
- routine operations
- upgrades
- MongoDB backup and restore
- troubleshooting

For machine-specific setup instructions, see [PLATFORM_DEPLOYMENT.md](PLATFORM_DEPLOYMENT.md).
For the detailed backup and recovery runbook, see [BACKUP_AND_RECOVERY.md](BACKUP_AND_RECOVERY.md).

## Deployment Modes

FlyManager ships with two deployment modes:

1. Base deployment

   Use [compose.yaml](compose.yaml) when running the app locally, on a lab workstation, or on a server where you only want local host access.

   In this mode:

   - the Flask app binds to `127.0.0.1:${APP_PORT}` on the host by default
   - MongoDB binds to `127.0.0.1:${MONGO_PUBLISHED_PORT}` on the host by default
   - no public HTTPS endpoint is created

2. Production deployment

   Use [compose.yaml](compose.yaml) together with [compose.production.yaml](compose.production.yaml) when you want a public deployment with automatic HTTPS.

   In this mode:

   - Caddy terminates TLS on ports `80` and `443`
   - the public domain is routed to the internal `app` service
   - the app container and MongoDB stay private behind the proxy unless you intentionally widen host bindings

## Files Involved

- [compose.yaml](compose.yaml): base app and MongoDB stack
- [compose.production.yaml](compose.production.yaml): Caddy HTTPS overlay
- [Dockerfile](Dockerfile): app image build
- [deploy/Caddyfile](deploy/Caddyfile): reverse proxy and TLS config
- [.env.example](.env.example): configuration template
- [scripts/install.sh](scripts/install.sh): base install helper
- [scripts/install-production.sh](scripts/install-production.sh): production install helper
- [scripts/mongo-backup.sh](scripts/mongo-backup.sh): MongoDB archive backup
- [scripts/state-backup.sh](scripts/state-backup.sh): deployment state backup for data, `.env`, and optional Caddy state
- [scripts/state-restore.sh](scripts/state-restore.sh): deployment state restore for `.env`, `data/`, and optional Caddy state
- [scripts/mongo-restore.sh](scripts/mongo-restore.sh): MongoDB archive restore
- [BACKUP_AND_RECOVERY.md](BACKUP_AND_RECOVERY.md): detailed backup policy, restore order, and recovery drill runbook

## Prerequisites

Required for all Docker deployments:

- Docker Engine or Docker Desktop
- Docker Compose v2

Required for public production deployment:

- a DNS record pointing your deployment domain to the server
- inbound ports `80` and `443` open to the internet
- outbound internet access so Caddy can obtain TLS certificates

## Environment Configuration

Create the runtime environment file from the example:

```bash
cp .env.example .env
```

Important variables:

- `SECRET_KEY`

  Required for Flask session signing. This should be a long random string and should not change between restarts unless you intentionally want to invalidate existing sessions.

- `SESSION_LIFETIME_SECONDS`, `SESSION_COOKIE_SAMESITE`, `SESSION_COOKIE_SECURE`, `TRUST_PROXY_HEADERS`

  Optional session and proxy hardening controls. The defaults keep sessions valid for one hour with `SameSite=Lax`. Set `SESSION_COOKIE_SECURE=1` when the app is deployed behind HTTPS so browsers only send the session cookie over secure transport. Set `TRUST_PROXY_HEADERS=1` when the app is behind Caddy or another trusted reverse proxy so Flask correctly honors `X-Forwarded-*` headers.

- `MAX_UPLOAD_SIZE_BYTES`

  Maximum accepted upload size for database import workbooks. The default is `8388608` bytes (8 MiB).

- `CORS_ALLOWED_ORIGINS`

  Optional comma-separated list of origins that may make cross-origin requests. Leave this empty unless you intentionally expose FlyManager to a separate frontend or trusted automation origin.

- `MONGO_URI`

  Defaults to the bundled MongoDB container:

  ```env
  MONGO_URI=mongodb://mongodb:27017
  ```

- `MONGO_DB_NAME`

  Logical database name inside MongoDB.

- `APP_PORT`

  Container service port exposed on the host for the base deployment.

- `APP_PUBLISHED_HOST`

  Host bind address for the base deployment. Default is `127.0.0.1`, which keeps the app private to the local machine.

- `MONGO_PUBLISHED_HOST`

  Host bind address for MongoDB. Default is `127.0.0.1`, which keeps MongoDB private to the local machine.

- `FLYMANAGER_DOMAIN`

  Required for production HTTPS deployment. This must be a real domain that resolves to the deployment server.

- `SMTP_SERVER`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_SENDER`

  Optional. If these are left blank, scheduled reminder emails are skipped instead of failing the scheduler.

- `FLYMANAGER_ADMIN_USERNAME`, `FLYMANAGER_ADMIN_PASSWORD`, `FLYMANAGER_ADMIN_INITIALS`

  Optional. If all three are provided, the app bootstrap step creates the initial operator account if it does not already exist.

- `ENABLE_SCHEDULER`

  Default is `1`. Keep this enabled only on one running app instance. The current design assumes a single scheduler process.

- `BACKUP_ROOT`, `MONGO_BACKUP_DIR`, `STATE_BACKUP_DIR`, `BACKUP_LOG_DIR`

  Cross-platform backup output locations. The defaults keep everything under `backups/` inside the repository checkout, but production hosts can point these to a larger mounted filesystem.

- `BACKUP_OFFSITE_DIR`

  Optional mounted directory used by the backup scripts to place a second copy of each archive. On Synology this can be another shared folder or mounted target; on cloud VMs it can be a mounted object-storage sync path or another attached volume.

- `BACKUP_KEEP_RECENT`, `BACKUP_KEEP_DAILY`, `STATE_BACKUP_KEEP_RECENT`, `STATE_BACKUP_KEEP_DAILY`

  Rolling retention controls for recent and day-level checkpoints.

- `BACKUP_INCLUDE_CADDY`

  Controls whether deployment-state backups attempt to capture Caddy's `/data` and `/config` volumes. Use `auto` to include them whenever the production overlay is active, `1` to require them, or `0` to skip them.

- `RESTORE_INCLUDE_CADDY`, `RESTORE_PRECHECK_DIR`

  Controls whether state restores push archived Caddy runtime data back into the proxy volumes and where the current local `.env` and `data/` snapshot is stored before overwrite.

## First-Time Base Deployment

Use this when you want FlyManager running locally on a workstation or privately on a server.

### Quick path

```bash
./scripts/install.sh
```

This script:

- creates needed local directories
- creates `.env` if missing
- generates a `SECRET_KEY`
- builds the app image
- starts the base stack

### Manual path

```bash
cp .env.example .env
docker compose up -d --build
```

### Verify startup

Check services:

```bash
docker compose ps
```

Check health:

```bash
curl http://127.0.0.1:5234/health
curl http://127.0.0.1:5234/health/ready
```

If you changed `APP_PORT`, replace `5234` with the configured port.

### Access the app

Open:

```text
http://localhost:5234
```

## First-Time Production Deployment

Use this when FlyManager should be publicly reachable at a real domain with automatic HTTPS.

### Step 1: Prepare the server

Make sure the target machine:

- has Docker installed
- has Docker Compose v2 available
- allows inbound traffic on ports `80` and `443`
- can resolve and reach the internet for certificate issuance

### Step 2: Point DNS to the server

Create an `A` record or equivalent pointing your chosen domain to the public IP of the server.

Example:

```text
flymanager.example.com -> 203.0.113.10
```

Wait until the record resolves correctly from the internet before expecting certificate issuance to succeed.

### Step 3: Configure `.env`

Create the environment file and set the deployment domain:

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

```env
SECRET_KEY=replace-with-a-real-random-secret
FLYMANAGER_DOMAIN=flymanager.example.com
```

Recommended production values:

```env
APP_PUBLISHED_HOST=127.0.0.1
MONGO_PUBLISHED_HOST=127.0.0.1
ENABLE_SCHEDULER=1
SESSION_COOKIE_SECURE=1
TRUST_PROXY_HEADERS=1
```

Those defaults keep the app and database private behind the reverse proxy.

If you need cross-origin access in production, also set:

```env
CORS_ALLOWED_ORIGINS=https://flymanager.example.com
```

If you do not need it, leave it blank and the app will stay same-origin only.

### Step 4: Start the production stack

Recommended helper:

```bash
./scripts/install-production.sh flymanager.example.com
```

Equivalent manual command:

```bash
docker compose -f compose.yaml -f compose.production.yaml up -d --build
```

### Step 5: Verify production health

Check the combined stack:

```bash
docker compose -f compose.yaml -f compose.production.yaml ps
```

Check logs if needed:

```bash
docker compose -f compose.yaml -f compose.production.yaml logs -f proxy
docker compose -f compose.yaml -f compose.production.yaml logs -f app
docker compose -f compose.yaml -f compose.production.yaml logs -f mongodb
```

Then verify the public URL:

```text
https://flymanager.example.com
```

## Security Notes

- Database import and export utilities are administrator-only because they operate on the full dataset.
- Keep `APP_PUBLISHED_HOST` and `MONGO_PUBLISHED_HOST` bound to `127.0.0.1` unless you have a deliberate network exposure requirement.
- If MongoDB must be reachable off-host, add both network-layer controls and MongoDB authentication; the default deployment assumes host-local access only.
- Do not rotate `SECRET_KEY` casually in production. Changing it invalidates active sessions immediately.

## Routine Operations

### Start the base stack

```bash
docker compose up -d
```

### Stop the base stack

```bash
docker compose down
```

### Start the production stack

```bash
docker compose -f compose.yaml -f compose.production.yaml up -d
```

### Stop the production stack

```bash
docker compose -f compose.yaml -f compose.production.yaml down
```

### View app logs

Base stack:

```bash
docker compose logs -f app
```

Production stack:

```bash
docker compose -f compose.yaml -f compose.production.yaml logs -f app
```

### View proxy logs

```bash
docker compose -f compose.yaml -f compose.production.yaml logs -f proxy
```

## Upgrades And Redeployments

When you pull new code:

### Base deployment

```bash
git pull
docker compose up -d --build
```

### Production deployment

```bash
git pull
docker compose -f compose.yaml -f compose.production.yaml up -d --build
```

Recommended upgrade sequence for production:

1. Take a MongoDB backup before deploying.
2. Pull the new code.
3. Rebuild and restart the stack.
4. Verify `/health/ready` and the public site.
5. Keep the backup until the upgrade is confirmed stable.

## MongoDB Backup

For the complete backup policy, restore sequencing, offsite strategy, and drill checklist, see [BACKUP_AND_RECOVERY.md](BACKUP_AND_RECOVERY.md).

Backups use `mongodump --archive --gzip` and write a compressed archive under `backups/mongodb/` by default.

The script now also:

- writes a SHA-256 checksum file next to each archive when a checksum utility is available
- prunes older archives according to the configured rolling retention policy
- optionally copies the archive and checksum to `BACKUP_OFFSITE_DIR`

### Base stack backup

```bash
./scripts/mongo-backup.sh
```

### Production stack backup

```bash
COMPOSE_ARGS="-f compose.yaml -f compose.production.yaml" ./scripts/mongo-backup.sh
```

### Backup to a custom path

```bash
./scripts/mongo-backup.sh /path/to/flymanager_backup.archive.gz
```

### Backup help and dry run

```bash
./scripts/mongo-backup.sh --help
BACKUP_DRY_RUN=1 ./scripts/mongo-backup.sh
```

## Deployment State Backup

For archive contents, manifest interpretation, offsite-copy guidance, and full recovery workflow, see [BACKUP_AND_RECOVERY.md](BACKUP_AND_RECOVERY.md).

Use the deployment-state backup to protect non-Mongo state needed for recovery on any supported platform.

It captures:

- `.env`
- `data/`
- selected deployment files such as the Compose manifests and Caddyfile
- optional Caddy runtime volumes from the production overlay

It intentionally does not include Flask session files or generated label PDFs because those are regenerable or short-lived.

### Base stack state backup

```bash
./scripts/state-backup.sh
```

### Production stack state backup

```bash
COMPOSE_ARGS="-f compose.yaml -f compose.production.yaml" ./scripts/state-backup.sh
```

### State backup help and dry run

```bash
./scripts/state-backup.sh --help
BACKUP_DRY_RUN=1 ./scripts/state-backup.sh
```

## MongoDB Restore

For restore order and safety guidance, see [BACKUP_AND_RECOVERY.md](BACKUP_AND_RECOVERY.md).

Restore uses `mongorestore --archive --gzip --drop`.

Important behavior:

- the restore script stops the `app` container first if it is running
- it restarts the `app` container afterward
- `--drop` replaces the current database contents with the archive contents

### Base stack restore

```bash
./scripts/mongo-restore.sh backups/mongodb/flymanager_mongodb_YYYYMMDD_HHMMSS.archive.gz
```

### Production stack restore

```bash
COMPOSE_ARGS="-f compose.yaml -f compose.production.yaml" ./scripts/mongo-restore.sh backups/mongodb/flymanager_mongodb_YYYYMMDD_HHMMSS.archive.gz
```

## Deployment State Restore

For detailed restore behavior, preflight snapshot expectations, and isolated drill guidance, see [BACKUP_AND_RECOVERY.md](BACKUP_AND_RECOVERY.md).

Use deployment-state restore when you need to recover `.env`, `data/`, or Caddy runtime state after host loss or operator error.

Important behavior:

- the current local `.env` and `data/` are preserved under `RESTORE_PRECHECK_DIR/<timestamp>/` before overwrite
- config files from the archive are extracted into the restore snapshot as reference material instead of overwriting version-controlled files automatically
- Caddy runtime volumes are restored only when the archive contains them and `RESTORE_INCLUDE_CADDY` allows it

### Base stack state restore

```bash
./scripts/state-restore.sh backups/state/flymanager_state_YYYYMMDDTHHMMSSZ.tar.gz
```

### Production stack state restore

```bash
COMPOSE_ARGS="-f compose.yaml -f compose.production.yaml" ./scripts/state-restore.sh backups/state/flymanager_state_YYYYMMDDTHHMMSSZ.tar.gz
```

### State restore help and dry run

```bash
./scripts/state-restore.sh --help
BACKUP_DRY_RUN=1 ./scripts/state-restore.sh backups/state/flymanager_state_YYYYMMDDTHHMMSSZ.tar.gz
```

## Isolated Recovery Drill

Recommended drill order for every supported platform:

1. Restore the target host checkout and runtime environment.
2. Run `scripts/state-restore.sh` with the chosen state archive.
3. Bring up the stack with Docker Compose.
4. Run `scripts/mongo-restore.sh` with the chosen Mongo archive.
5. Verify `docker compose ps`, `/health`, and `/health/ready`.
6. Confirm a representative login and spot-check current records in the UI.

## Security Guidance

Recommended production practices:

- keep `.env` out of version control
- rotate any credentials that were ever stored insecurely
- do not expose MongoDB publicly unless there is a strong operational reason
- keep `APP_PUBLISHED_HOST=127.0.0.1` and `MONGO_PUBLISHED_HOST=127.0.0.1` for public deployments
- use strong SMTP credentials if enabling mail
- keep Docker and the host OS patched
- take regular backups and test restore procedures

Recommended backup baseline for all supported single-host deployments:

- run `scripts/mongo-backup.sh` hourly on active systems
- run `scripts/state-backup.sh` at least daily and before upgrades
- keep `scripts/state-restore.sh` available on the host and test it during recovery drills
- copy every archive to `BACKUP_OFFSITE_DIR` or an equivalent mounted secondary target
- test Mongo restore regularly and perform a full isolated recovery drill on a schedule

Current architecture note:

- the scheduler runs inside the app container
- do not scale the app service horizontally without redesigning scheduled job ownership and Socket.IO behavior

## Troubleshooting

### The app container starts but the site is not reachable

Check:

- `docker compose ps`
- `docker compose logs -f app`
- `curl http://127.0.0.1:5234/health/ready`

Common causes:

- the app is still waiting for MongoDB
- `SECRET_KEY` or other required values are missing
- a port bind conflict exists on the host

### Production HTTPS certificates are not being issued

Check:

- the domain resolves to the correct server
- ports `80` and `443` are reachable from the internet
- `docker compose -f compose.yaml -f compose.production.yaml logs -f proxy`

Common causes:

- DNS not propagated yet
- firewall blocking `80` or `443`
- `FLYMANAGER_DOMAIN` still set to the placeholder value

### `/health/ready` returns a non-200 response

That endpoint checks MongoDB connectivity.

Check:

- `docker compose logs -f mongodb`
- `MONGO_URI`
- `MONGO_DB_NAME`

### Email reminders are not being sent

If SMTP is not configured, the app intentionally skips mail delivery.

Check:

- `SMTP_SERVER`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `SMTP_SENDER`
- app logs for mail-related messages

### Backup or restore fails

Check:

- the `mongodb` container is running
- the archive path exists for restore
- the correct `COMPOSE_ARGS` value is being used when targeting the production overlay

If a state backup fails:

- verify `.env` exists and is readable
- verify `data/` exists and is readable
- if you expect Caddy state, verify the production overlay is included in `COMPOSE_ARGS` and the `proxy` service can be started or inspected

If a state restore fails:

- inspect the preserved local snapshot under `RESTORE_PRECHECK_DIR`
- verify the archive contains `manifest.txt` and the expected `runtime/` content
- if the failure is in Caddy restore, retry with `RESTORE_INCLUDE_CADDY=0` and restore proxy state separately after the app is back

## Validation Checklist

Before calling a deployment complete, verify:

- `docker compose ps` shows healthy services
- `/health` returns `200`
- `/health/ready` returns `200`
- the web UI loads successfully
- login works
- MongoDB backup completes successfully
- deployment-state backup completes successfully
- deployment-state restore has been tested in a safe environment
- at least one restore procedure has been tested in a safe environment

## Summary Commands

Base deployment:

```bash
./scripts/install.sh
docker compose ps
docker compose logs -f app
```

Production deployment:

```bash
./scripts/install-production.sh flymanager.example.com
docker compose -f compose.yaml -f compose.production.yaml ps
docker compose -f compose.yaml -f compose.production.yaml logs -f proxy
```

Database maintenance:

```bash
./scripts/mongo-backup.sh
./scripts/state-backup.sh
./scripts/state-restore.sh backups/state/flymanager_state_YYYYMMDDTHHMMSSZ.tar.gz
./scripts/mongo-restore.sh backups/mongodb/flymanager_mongodb_YYYYMMDD_HHMMSS.archive.gz
```
