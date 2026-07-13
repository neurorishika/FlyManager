# Synology Production Migration: Replica Set + Backup Container + Job Queue

**One-time runbook.** Follow top to bottom, in order. Do not skip
verification steps between stages — each stage assumes the previous one
is confirmed working before you proceed.

## What this migration changes

Your Portainer stack (built from `compose.synology.yaml`, pasted-YAML
deployment, no git checkout on the NAS) currently runs a single-instance
MongoDB with no replica set, no in-stack backup automation, and no
background job queue. This migration brings it to:

1. **A 2-member Mongo replica set** (`mongodb` + new `mongodb2`, with a
   one-shot `mongo-init` container that safely initializes it).
2. **An in-stack backup container** (`mongo-backup`) that runs
   `mongodump --oplog` every 6 hours, with local retention, and optional
   off-site push (rclone) / heartbeat (healthcheck.io) once you configure
   them.
3. **Redis + a background worker container** (`redis`, `worker`) — a
   feature that was sitting uncommitted in the working tree and has never
   been deployed before. It moves heavy admin actions (Excel export,
   FlyBase/Bloomington refreshes, phenotype cache backfills) off the
   request path.

Your existing MongoDB data (in the `mongo_data` volume) is **never**
recreated, copied, or reformatted by this migration — the existing
`mongodb` container just gains a `--replSet` flag and keeps using the
exact same volume. Data risk is limited to normal service-restart risk,
not data-loss risk, provided you follow the steps in order and don't skip
the pre-migration backup.

**Expected downtime:** a few minutes, while the stack restarts. Plan for a
maintenance window; don't run this during active lab use.

---

## Stage 0: Pre-flight checklist

Do not proceed past this stage until every box is true.

- [ ] You have SSH access to the Synology NAS (or another way to run
      `docker exec` against the running containers — Portainer's web
      console also works for the verification commands below).
- [ ] You have access to the Portainer UI for this stack.
- [ ] You have a Docker Hub (or other registry) account you can push an
      updated image to, and know the current `APP_IMAGE` value configured
      in the Portainer stack's environment variables (open the stack in
      Portainer → **Environment variables** tab and note it down now,
      along with every other value currently set there — `SECRET_KEY`,
      `FLYMANAGER_ADMIN_USERNAME`, `FLYMANAGER_ADMIN_PASSWORD`,
      `FLYMANAGER_ADMIN_INITIALS`, `ORG_ABV`, `SMTP_*`,
      `MAIL_SUPPRESS_SEND`, `CORS_ALLOWED_ORIGINS`, `APP_IMAGE`. Write
      these down somewhere safe before continuing — updating the stack
      file does not clear them, but you should have them recorded in case
      anything looks different after the update).
- [ ] The current stack is healthy right now (`docker ps` on the NAS shows
      `flymanager-mongodb`, `flymanager-app` running and healthy, no
      restart loops).
- [ ] You've picked a maintenance window (evening/weekend, low lab
      activity).

---

## Stage 1: Build and push the updated image

From your development machine, in this repo, on the
`better-genetics-and-cleaner-UI-` branch at its current tip:

```bash
git log --oneline -1
# confirm this shows commit 36af407 or later
```

Build and push, substituting your actual registry/username and picking a
**specific version tag**, not just `:latest` (so you can always redeploy
the exact previous image if you need to roll back):

```bash
docker build -t YOUR_DOCKERHUB_USER/flymanager:phase2-migration .
docker push YOUR_DOCKERHUB_USER/flymanager:phase2-migration
```

Keep a note of whatever image tag was running **before** this migration
(check the current `APP_IMAGE` value from Stage 0) — that's your rollback
image if needed.

---

## Stage 2: Prepare NAS directories and upload script files

SSH into the NAS:

```bash
ssh your-user@your-synology-ip
mkdir -p /volume1/docker/flymanager/scripts
```

From your development machine, copy the two required files onto the NAS
(via `scp`, or Synology File Station if you prefer a GUI):

```bash
scp scripts/container-mongo-backup.sh your-user@your-synology-ip:/volume1/docker/flymanager/scripts/container-mongo-backup.sh
scp scripts/rclone.conf.example your-user@your-synology-ip:/volume1/docker/flymanager/scripts/rclone.conf
```

(Uploading `rclone.conf.example` renamed to `rclone.conf` gives you a
harmless placeholder — the backup container only invokes `rclone` when
`RCLONE_REMOTE` is set, which it won't be yet. You'll replace this file's
content for real once you configure off-site backup in Stage 7.)

Back on the NAS via SSH, confirm both files exist and check the backup
script is executable (it should already be, since it's tracked in git as
mode `100755` — this just confirms the copy preserved that):

```bash
ls -l /volume1/docker/flymanager/scripts/
```

Expected: both files present, `container-mongo-backup.sh` shows `-rwxr-xr-x` or similar with the executable bit set. If it doesn't:

```bash
chmod +x /volume1/docker/flymanager/scripts/container-mongo-backup.sh
```

---

## Stage 3: Take a pre-migration safety backup

Since this Portainer deployment has no git checkout, you can't run
`scripts/mongo-backup.sh` directly on the NAS. Take a manual backup
straight from the running container instead — still via SSH on the NAS:

```bash
mkdir -p /volume1/docker/flymanager/backups
docker exec flymanager-mongodb sh -c 'mongodump --archive --gzip' > /volume1/docker/flymanager/backups/pre-migration-$(date -u +%Y%m%dT%H%M%SZ).archive.gz
```

Verify the archive isn't empty:

```bash
ls -lh /volume1/docker/flymanager/backups/pre-migration-*.archive.gz
```

Expected: a file with a non-trivial size (not 0 bytes). This is your
safety net — if anything goes wrong later in this runbook, you can
restore from this exact archive into a fresh single-instance `mongodb`
container.

---

## Stage 4: Update the Portainer stack

1. In Portainer, open the stack → **Editor** tab.
2. **Before changing anything**, copy the currently-displayed YAML
   somewhere safe (a text file on your workstation) — this is your
   fallback stack definition if you need to roll back the *compose file
   itself* (separate from rolling back the image, Stage 1).
3. Select and delete all the existing YAML in the editor.
4. Paste in the full contents of this repo's current
   `compose.synology.yaml` (from commit `36af407` or later — verify with
   `git log --oneline -1` on your dev machine before copying).
5. Go to the **Environment variables** tab. Confirm all the values you
   recorded in Stage 0 are still present (they persist across a stack-file
   update automatically — you're just double-checking, not re-entering
   them).
6. Update `APP_IMAGE` to the new tag you pushed in Stage 1:
   `YOUR_DOCKERHUB_USER/flymanager:phase2-migration`.
7. Leave the new optional variables (`RCLONE_REMOTE`, `RCLONE_CONFIG_PATH`,
   `HEALTHCHECK_UUID`, `MONGO_BACKUP_INTERVAL_SECONDS`, `BACKUP_KEEP_DAYS`)
   unset for now — they all have safe defaults (empty/14 days/21600
   seconds), and you'll configure the real ones in Stage 7 once this
   migration is confirmed working.
8. Click **Update the stack**. If Portainer offers a "Re-pull image"
   / "Pull latest image" toggle, enable it (needed since your tag is new).

---

## Stage 5: Watch the deployment come up

In Portainer's stack view (or via SSH with `docker ps` / `docker compose
logs`), watch service startup. Expected order:

1. `mongodb`, `mongodb2`, `redis` start and become healthy.
2. `mongo-init` runs once, exits with status `0` (shows as "Exited (0)"
   in Portainer, not a crash-loop).
3. `mongo-backup`, `app`, `worker` start after `mongo-init` completes.
4. `app` and `worker` become healthy (worker has no healthcheck by
   design — just confirm it's `Up` and not restarting).

If `mongo-init` exits non-zero or keeps restarting, **stop here** and go
to the Rollback section below — do not proceed to verification.

---

## Stage 6: Verification (do all of these before declaring success)

Via SSH on the NAS:

**Replica set health:**

```bash
docker exec flymanager-mongodb mongosh --quiet --eval 'rs.status().members.map(m => ({name: m.name, stateStr: m.stateStr}))'
```

Expected: one member `PRIMARY` (should be `mongodb:27017`), one
`SECONDARY` (`mongodb2:27017`). If `mongodb2` shows `STARTUP2`, wait
another minute and re-run — it's still performing initial sync.

**Your existing data is intact:**

```bash
docker exec flymanager-mongodb mongosh --quiet --eval 'db.getSiblingDB("flymanager").stocks.countDocuments()'
docker exec flymanager-mongodb mongosh --quiet --eval 'db.getSiblingDB("flymanager").crosses.countDocuments()'
```

Expected: counts matching what you'd expect from before the migration
(compare against your own knowledge of the current dataset size, or
against the pre-migration backup archive from Stage 3 if you want an
exact comparison — you can restore that archive into a scratch container
with `scripts/mongo-restore-drill.sh` on your dev machine and compare
counts).

**Backup container is running and producing archives:**

```bash
docker logs flymanager-mongo-backup --tail 50
```

Expected: no repeated `chmod: Read-only file system` crash-looping (a
single harmless line mentioning it is fine — the script tolerates it),
and eventually a line like `Backup complete: /backups/flymanager_mongodb_....archive.gz`.

```bash
docker exec flymanager-mongo-backup ls -lh /backups
```

Expected: at least one `.archive.gz` and matching `.sha256` file.

**App and worker health:**

```bash
curl -s http://127.0.0.1:12754/health
curl -s http://127.0.0.1:12754/health/ready
```

Expected: both return `200`/`{"status": "ok"}`-style responses.

**Application spot-check (in a browser, via your normal domain):**

- [ ] Log in successfully.
- [ ] View a stock record and a cross record you know existed before the
      migration — confirm the data is correct.
- [ ] Open the `/jobs` page (new — part of the bundled job-queue feature)
      and confirm it loads without error.
- [ ] Trigger one lightweight background action you're comfortable
      testing in production (e.g. the Excel export, from wherever that's
      exposed in the UI) and confirm it completes and the file/result
      shows up — this exercises the new `worker` container end-to-end.

Only once **every** box above is checked do you consider this migration
complete.

---

## Stage 7 (optional, do this once Stage 6 is fully green): Enable real off-site backup and heartbeat

1. Generate a real rclone remote config on your workstation:
   `rclone config` (see [BACKUP_AND_RECOVERY.md](BACKUP_AND_RECOVERY.md)'s
   "Backup container" section for details).
2. Upload the resulting config to the NAS, replacing the placeholder:
   ```bash
   scp path/to/your/real/rclone.conf your-user@your-synology-ip:/volume1/docker/flymanager/scripts/rclone.conf
   ```
3. In Portainer's stack **Environment variables** tab, set:
   - `RCLONE_REMOTE` — e.g. `gdrive:flymanager-backups`
   - `HEALTHCHECK_UUID` — from a new check you create at healthcheck.io
4. **Update the stack** again (no YAML change needed, just the env vars —
   Portainer will recreate `mongo-backup` with the new environment).
5. Wait for the next backup cycle (or up to `MONGO_BACKUP_INTERVAL_SECONDS`,
   default 6 hours) and confirm:
   - `docker logs flymanager-mongo-backup` shows a successful `rclone
     copy` line.
   - The healthcheck.io dashboard shows a recent successful ping.

---

## Rollback plan

If anything in Stage 5 or 6 doesn't check out:

1. **Do not panic about data loss** — the `mongodb` container's data
   volume (`mongo_data`) has not been touched by anything in this
   migration except mongod's own `--replSet` flag, and you have the
   Stage 3 backup as an additional safety net regardless.
2. In Portainer's stack **Editor**, paste back the YAML you saved in
   Stage 4 step 2 (the pre-migration version) and **Update the stack**.
   This removes `mongodb2`, `mongo-init`, `mongo-backup`, `redis`,
   `worker` and restarts `mongodb` back to standalone mode and `app` back
   to its previous image/config.
3. Also revert `APP_IMAGE` in the Environment variables tab back to the
   pre-migration tag you recorded in Stage 0.
4. Verify `/health/ready` and a normal login again.
5. If `mongodb` itself won't come back up standalone after having run
   with `--replSet` briefly (unlikely, but possible if it got far enough
   into replica-set-specific local state), restore from the Stage 3
   backup into a fresh standalone `mongodb` container:
   ```bash
   docker exec flymanager-mongodb sh -c 'mongorestore --archive --gzip --drop' < /volume1/docker/flymanager/backups/pre-migration-<timestamp>.archive.gz
   ```
6. Note what went wrong and share it before attempting the migration
   again — don't just retry blind.

---

## After a successful migration

1. Record the date and outcome. Append a line to
   [BACKUP_AND_RECOVERY.md](BACKUP_AND_RECOVERY.md)'s drill log (or a new
   "Migration Log" subsection near it) noting: date, that this migration
   ran, and the result of your Stage 6 verification.
2. Watch the `mongo-backup` container for its first two or three
   scheduled cycles (every 6 hours) to build confidence before
   considering the backup pipeline fully trustworthy.
3. Once you have a real production backup archive from the new pipeline,
   run `scripts/mongo-restore-drill.sh` against a **copy** of it from your
   workstation, to rehearse the restore path against real (not
   synthetic) data at least once.
4. This file (`SYNOLOGY_MIGRATION_PHASE2.md`) has served its purpose once
   the migration is complete and verified — feel free to delete it, or
   keep it as a historical record of how this migration was performed.
