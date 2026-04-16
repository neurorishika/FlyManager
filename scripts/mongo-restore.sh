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

if docker compose $COMPOSE_ARGS ps --services --status running | grep -qx app; then
    APP_WAS_RUNNING=1
    docker compose $COMPOSE_ARGS stop app
fi

docker compose $COMPOSE_ARGS exec -T mongodb sh -c 'mongorestore --archive --gzip --drop' < "$ARCHIVE_PATH"

if [ "$APP_WAS_RUNNING" -eq 1 ]; then
    docker compose $COMPOSE_ARGS start app
fi

log "MongoDB restore completed from $ARCHIVE_PATH"