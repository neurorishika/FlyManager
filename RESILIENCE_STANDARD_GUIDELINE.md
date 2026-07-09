# Resilience Standard Guideline

Purpose: define a default, reusable methodology for software that must resist duplicate operations, maintain recoverable data, and minimize data loss.

Scope: use this for any service that stores business data (web apps, APIs, automations, internal tools, batch systems).

## 1) Core Standard (Non-Negotiable)

1. Every write path must be idempotent.
2. Every critical mutation must be atomic at the database level.
3. Every deployment must have automated backups with retention and restore verification.
4. Every scheduled/background job must be safe under multi-worker execution.
5. Every externally-triggered maintenance endpoint must be authenticated.
6. Every system must provide a recovery runbook and a tested restore drill.

If any one of these is missing, the system is not production-ready.

## 2) Data Loss Prevention Layers

Design with layered protection. No single control is trusted alone.

### Layer A: Real-time data durability

1. Use database replication for live redundancy (primary + secondary at minimum).
2. Keep data on persistent volumes, not container filesystems.
3. Define health checks for app and database services.
4. Treat replication as high availability, not backup.

### Layer B: Point-in-time recoverability

1. Run logical backups on a schedule (at least every 6 hours for active systems).
2. Include oplog/binlog/WAL-equivalent capture for point-in-time replay where supported.
3. Compress and timestamp artifacts in UTC.
4. Keep local retention (for fast restores) and off-site retention (for host/ransomware scenarios).
5. Use immutable/append-friendly storage where possible.

### Layer C: Operational assurance

1. Dead-man's switch/heartbeat for backup jobs.
2. Alerting on backup failure or stale backup age.
3. Monthly restore drill from backup artifact to a clean environment.

## 3) Duplicate and Replay Protection Standard

Implement both client-side friction reduction and server-side correctness.

### Client-side (UX guard, not trust boundary)

1. Disable submit buttons immediately on form submit.
2. Mark a form/request as "already submitted" in page state.
3. Show progress state to prevent repeated taps/clicks.

### Server-side (authoritative)

1. Require one-time idempotency tokens for mutation requests.
2. Store tokens in durable storage with:
- unique index on token key
- TTL expiration for cleanup
3. Reject duplicate token usage as a no-op success or explicit duplicate response.
4. Never rely solely on frontend duplicate prevention.

### Write-path atomicity

1. Use compare-and-set or conditional updates for stock/counter/value transitions.
2. Log before/after values for every material mutation.
3. Ensure side effects (email/webhooks) never block core write success.

## 4) Scheduled Job and Cron Safety Standard

Background jobs must be idempotent and single-effective per tick.

1. Use internal scheduler for primary execution when possible.
2. Guard each job with distributed locking (database lock document + TTL).
3. Make lock TTL slightly shorter than schedule interval.
4. Set scheduler max_instances=1 and coalesce=true (or equivalents).
5. Keep external cron endpoints as fallback, protected by shared secret or signed auth.
6. Return structured JSON status from task endpoints for observability and testing.

## 5) Reversible Operations and Data Repair Standard

1. Provide explicit undo for recent user actions where practical.
2. Store enough metadata to reverse operations safely.
3. Build admin cleanup tools for known corruption patterns (for example, rapid duplicate runs).
4. Preview-first for destructive cleanup; apply only after explicit confirmation.
5. Record cleanup effects in logs/audit trail.

## 6) Security Standard for Reliability Features

1. Protect task/maintenance endpoints with strong secrets.
2. Never hardcode secrets in scripts or docs.
3. Rotate operational secrets regularly.
4. Restrict backup credentials to least privilege.
5. Keep credential/config files out of source control.

## 7) Observability and Testing Standard

### Observability

1. Health endpoint must report machine-readable status.
2. Log each backup run start/end, artifact size, and push result.
3. Log each scheduled job run decision (executed/skipped/locked).
4. Track and alert on duplicate submission rejection rates.

### Testing

1. Unit tests for:
- idempotency token consume logic
- distributed lock behavior
- scheduled job lock contention
- unauthorized task endpoint access
- duplicate cleanup clustering and restore math
2. Integration tests for:
- backup artifact generation
- restore workflow correctness
- failover or degraded-database behavior

## 8) Standard Defaults by Risk Tier

Use this as your baseline when starting a new project.

### Tier 1 (Low risk internal tools)

1. Daily backup
2. 7-day retention
3. Local + one remote copy
4. Idempotency for all writes

### Tier 2 (Operationally important systems)

1. Backup every 6 hours
2. 14-30 day retention
3. Local + off-site remote + heartbeat monitoring
4. Distributed lock for scheduled jobs
5. Monthly restore drill

### Tier 3 (Business-critical systems)

1. Hourly backups or continuous snapshots
2. 30-90 day retention + archive tier
3. Multi-region/offline copy strategy
4. Signed task auth + secret rotation policy
5. Quarterly disaster recovery simulation

## 9) New Project Implementation Checklist

Copy this into each repo and mark complete before go-live.

- [ ] All mutation endpoints accept idempotency keys/tokens
- [ ] Token uniqueness + TTL indexes implemented
- [ ] Atomic conditional write pattern implemented for critical state
- [ ] Append-only transaction log includes before/after values
- [ ] Automated scheduled backups configured
- [ ] Off-site backup replication configured
- [ ] Backup retention and pruning configured
- [ ] Backup heartbeat/monitoring configured
- [ ] Restore runbook documented
- [ ] Restore drill executed successfully
- [ ] Scheduler lock mechanism implemented
- [ ] Background jobs idempotent by design
- [ ] Task endpoints authenticated
- [ ] Health endpoint available
- [ ] Duplicate cleanup/repair path defined
- [ ] Tests added for idempotency, lock, auth, and recovery

## 10) Reusable Pattern Snippets (Pseudo-Implementation)

### One-time token consume

```text
insert token record (token, created_at)
if unique-key violation:
  reject duplicate request
else:
  continue mutation
```

### Distributed scheduler lock

```text
insert lock record (_id=job_key, expires=now+ttl)
if duplicate-key:
  skip run
else:
  execute job
```

### Atomic mutation

```text
read current value
update where (id == target and current_value == expected_old)
if modified_count != 1:
  retry or return conflict
```

### Backup lifecycle

```text
dump with point-in-time metadata
compress and checksum
store local
copy off-site
prune old local and remote artifacts by retention
ping heartbeat monitor
```

## 11) Reference Implementation: Flask + Mongo + Docker + Portainer + healthcheck.io + GDrive

This is the standard template for projects like this one.

### 11.1 Reference architecture

1. Flask app container (Gunicorn, internal scheduler enabled)
2. Mongo primary container (replica set member 1)
3. Mongo secondary container (replica set member 2)
4. One-shot mongo-init container (safe rs.initiate)
5. Backup container (mongodump --oplog + compression + retention + off-site push + heartbeat ping)

### 11.2 Minimal environment template

Use this as the baseline .env for new projects:

```bash
SECRET_KEY=replace_with_long_random
ADMIN_PIN=123456
MONGO_DB=app_db

CRON_TOKEN=replace_with_long_random

APP_PORT=2152

BACKUP_KEEP_DAYS=14
RCLONE_REMOTE=gdrive:project-backups/app
HEALTHCHECK_UUID=replace_with_healthchecks_uuid

SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_USE_SSL=false
SMTP_FROM=alerts@example.org
ADMIN_EMAIL=ops@example.org
```

### 11.3 Docker Compose reference (Portainer-friendly)

```yaml
version: "3.9"
services:
  mongo:
    image: mongo:7
    restart: unless-stopped
    command: ["--replSet", "rs0", "--bind_ip_all"]
    volumes:
      - mongo_data:/data/db
    healthcheck:
      test: ["CMD", "mongosh", "--eval", "db.adminCommand('ping')"]
      interval: 10s
      timeout: 5s
      retries: 10

  mongo2:
    image: mongo:7
    restart: unless-stopped
    command: ["--replSet", "rs0", "--bind_ip_all"]
    volumes:
      - mongo_data2:/data/db
    healthcheck:
      test: ["CMD", "mongosh", "--eval", "db.adminCommand('ping')"]
      interval: 10s
      timeout: 5s
      retries: 10

  mongo-init:
    image: mongo:7
    restart: "no"
    depends_on:
      mongo:
        condition: service_healthy
      mongo2:
        condition: service_healthy
    entrypoint: ["/bin/bash", "-c"]
    command: >
      mongosh --host mongo:27017 --eval '
      try { rs.status(); }
      catch (e) {
        rs.initiate({
          _id: "rs0",
          members: [
            { _id: 0, host: "mongo:27017", priority: 2 },
            { _id: 1, host: "mongo2:27017", priority: 1 }
          ]
        });
      }'

  mongo-backup:
    image: rclone/rclone:latest
    restart: unless-stopped
    depends_on:
      mongo:
        condition: service_healthy
    volumes:
      - mongo_backups:/backups
      - ./scripts/mongo_backup.sh:/usr/local/bin/mongo_backup.sh:ro
      - ./scripts/rclone.conf:/config/rclone/rclone.conf:ro
    environment:
      MONGO_HOST: mongo
      MONGO_PORT: "27017"
      BACKUP_KEEP_DAYS: "${BACKUP_KEEP_DAYS:-14}"
      RCLONE_REMOTE: "${RCLONE_REMOTE:-}"
      HEALTHCHECK_UUID: "${HEALTHCHECK_UUID:-}"
    entrypoint: ["/bin/sh", "-c"]
    command: >
      apk add --no-cache mongodb-tools bash curl > /dev/null 2>&1 &&
      chmod +x /usr/local/bin/mongo_backup.sh &&
      /usr/local/bin/mongo_backup.sh &&
      while true; do sleep 21600; /usr/local/bin/mongo_backup.sh; done

  app:
    image: your-registry/your-app:latest
    restart: unless-stopped
    depends_on:
      mongo:
        condition: service_healthy
    env_file:
      - .env
    environment:
      MONGO_URI: mongodb://mongo:27017,mongo2:27017/?replicaSet=rs0
      APP_PORT: "2152"
    ports:
      - "2152:2152"
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS http://127.0.0.1:2152/health || exit 1"]
      interval: 30s
      timeout: 5s
      retries: 10

volumes:
  mongo_data:
  mongo_data2:
  mongo_backups:
```

### 11.4 Portainer deployment guide

1. Prepare host paths before creating the stack:
- scripts/mongo_backup.sh
- scripts/rclone.conf
- .env
2. In Portainer, create a new Stack and paste the compose file.
3. Configure environment variables in Portainer (or provide env_file).
4. Deploy stack and confirm all services become healthy.
5. Verify app health endpoint returns ok.
6. Verify mongo-init exits successfully after rs initialization.
7. Verify backup container creates .tar.gz artifacts in backup volume.

### 11.5 healthcheck.io integration guide

1. Create a check in healthcheck.io (cron style, every 6 hours).
2. Copy UUID into HEALTHCHECK_UUID in .env.
3. Backup script pings https://hc-ping.com/<UUID> on successful run.
4. Configure alert channels (email, Slack, etc.) in healthcheck.io.
5. Trigger one manual backup and confirm a successful ping is recorded.

### 11.6 GDrive off-site backup guide (rclone)

1. Install rclone on the Docker host once.
2. Run rclone config and create remote named gdrive.
3. Save generated rclone.conf to scripts/rclone.conf (never commit secrets).
4. Set RCLONE_REMOTE, for example:
- RCLONE_REMOTE=gdrive:project-backups/app
5. Restart stack and verify backup logs show successful rclone copy.

### 11.7 Storage alternatives (drop-in with rclone)

Use the same backup container and script with different RCLONE_REMOTE values:

1. Google Drive: gdrive:project-backups/app
2. Amazon S3: s3:bucket-name/app
3. Backblaze B2: b2:bucket-name/app
4. Wasabi/MinIO: s3-compatible remote
5. SFTP to NAS: sftp:/volume1/backups/app

Minimum policy for alternatives:

1. Remote credentials are least-privilege and isolated per app.
2. Remote retention is enforced (mirror local retention baseline).
3. Restore test from remote artifact is run monthly.

### 11.8 App-level reliability requirements for this stack

1. One-time submit tokens + TTL + unique index for mutation idempotency.
2. Atomic compare-and-set updates for stock/counter writes.
3. Undo endpoint for immediate operator correction.
4. Duplicate cleanup admin tool for historical bad data repair.
5. Internal scheduler with DB lock documents + TTL for one-worker effect.
6. Token-protected fallback task endpoints for summary/replenish/manual triggers.

### 11.9 Verification checklist after each deployment

- [ ] /health returns HTTP 200
- [ ] Replica set has primary + secondary
- [ ] Backup artifact generated locally
- [ ] Off-site copy completed
- [ ] healthcheck.io ping recorded
- [ ] Token-protected task endpoints reject bad token
- [ ] One successful run each for summary/replenish paths

### 11.10 Restore drill runbook (must be tested)

1. Stop app writes (maintenance mode or stop app container).
2. Select latest valid .tar.gz backup artifact.
3. Extract archive in clean restore workspace.
4. Run mongorestore with oplog replay when available.
5. Start app and run smoke tests:
- health endpoint
- recent data presence
- key admin actions
6. Record RTO and issues in incident/reliability notes.

## 12) Team Policy

1. Any exception to this standard must be documented in the repo with reason, risk, and mitigation.
2. Every production incident updates this guideline or the project-specific runbook.
3. Reliability controls are treated as product features, not optional operations work.

---

Version: 1.1
Owner: project maintainer
Review cadence: every quarter or after any data-loss/duplication incident
