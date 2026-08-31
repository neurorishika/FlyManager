#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck disable=SC1091
. "$SCRIPT_DIR/backup-common.sh"

load_backup_env

COMPOSE_ARGS=${COMPOSE_ARGS:-}
ARCHIVE_PATH="${1:-}"
APP_WAS_RUNNING=0

case "$ARCHIVE_PATH" in
    -h|--help)
        cat <<'EOF'
Usage: ./scripts/mongo-restore.sh path/to/backup.archive.gz

Restore a MongoDB archive into the current Docker Compose stack.

Environment:
  COMPOSE_ARGS     Extra docker compose arguments, for example:
                   -f compose.yaml -f compose.production.yaml
  BACKUP_DRY_RUN   If truthy, print actions without restoring data.
EOF
        exit 0
        ;;
esac

if [ -z "$ARCHIVE_PATH" ]; then
    echo "Usage: ./scripts/mongo-restore.sh path/to/backup.archive.gz"
    exit 1
fi

ARCHIVE_PATH=$(resolve_path "$ARCHIVE_PATH")

if [ ! -f "$ARCHIVE_PATH" ]; then
    echo "Backup archive not found: $ARCHIVE_PATH"
    exit 1
fi

ensure_docker_compose

if is_truthy "${BACKUP_DRY_RUN:-0}"; then
    log "Dry run: would restore MongoDB from $ARCHIVE_PATH"
    exit 0
fi

cd "$ROOT_DIR"

if ! compose_service_running mongodb; then
    fail "The mongodb service is not running for the selected Compose stack."
fi

# The checksum the backup wrote is worth nothing if no restore ever reads it.
if [ -f "${ARCHIVE_PATH}.sha256" ]; then
    log "Verifying ${ARCHIVE_PATH}.sha256"
    ( cd "$(dirname "$ARCHIVE_PATH")" && sha256sum -c "$(basename "$ARCHIVE_PATH").sha256" ) \
        || fail "Checksum mismatch: $ARCHIVE_PATH is corrupt or truncated."
else
    log "WARNING: no .sha256 beside $ARCHIVE_PATH -- integrity unverified."
fi

# Stop the WORKER as well as the app. RQ jobs (cache backfills, FlyBase
# refreshes, exports) write to Mongo, and a job running through a
# `mongorestore --drop` interleaves its writes into half-restored collections.
WORKER_WAS_RUNNING=0
BACKUP_WAS_RUNNING=0
if docker compose $COMPOSE_ARGS ps --services --status running | grep -qx app; then
    APP_WAS_RUNNING=1
    docker compose $COMPOSE_ARGS stop app
fi
if docker compose $COMPOSE_ARGS ps --services --status running | grep -qx worker; then
    WORKER_WAS_RUNNING=1
    docker compose $COMPOSE_ARGS stop worker
fi
# And pause the backup loop, or it can capture the half-restored database into
# the retention window as a perfectly valid-looking archive.
if docker compose $COMPOSE_ARGS ps --services --status running | grep -qx mongo-backup; then
    BACKUP_WAS_RUNNING=1
    docker compose $COMPOSE_ARGS stop mongo-backup
fi

# --oplogReplay: the backup captures the oplog with --oplog precisely so the
# restore can replay it to a single consistent point. Skipping it silently
# discarded that, restoring each collection at its own scan time -- which for
# GridFS means fs.files can land without the fs.chunks it references.
docker compose $COMPOSE_ARGS exec -T mongodb sh -c 'mongorestore --archive --gzip --drop --oplogReplay' < "$ARCHIVE_PATH"

if [ "$BACKUP_WAS_RUNNING" -eq 1 ]; then
    docker compose $COMPOSE_ARGS start mongo-backup
fi
if [ "$WORKER_WAS_RUNNING" -eq 1 ]; then
    docker compose $COMPOSE_ARGS start worker
fi
if [ "$APP_WAS_RUNNING" -eq 1 ]; then
    docker compose $COMPOSE_ARGS start app
fi

log "MongoDB restore completed from $ARCHIVE_PATH"