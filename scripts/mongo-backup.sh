#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck disable=SC1091
. "$SCRIPT_DIR/backup-common.sh"

load_backup_env

COMPOSE_ARGS=${COMPOSE_ARGS:-}
BACKUP_PREFIX=${BACKUP_PREFIX:-flymanager_mongodb}
BACKUP_DIR=${MONGO_BACKUP_DIR:-${BACKUP_ROOT:-backups/mongodb}}
BACKUP_DIR=$(resolve_path "$BACKUP_DIR")
BACKUP_KEEP_RECENT=${MONGO_BACKUP_KEEP_RECENT:-${BACKUP_KEEP_RECENT:-48}}
BACKUP_KEEP_DAILY=${MONGO_BACKUP_KEEP_DAILY:-${BACKUP_KEEP_DAILY:-14}}
TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
DEFAULT_ARCHIVE="$BACKUP_DIR/${BACKUP_PREFIX}_${TIMESTAMP}.archive.gz"

case "${1:-}" in
	-h|--help)
		cat <<'EOF'
Usage: ./scripts/mongo-backup.sh [archive-path]

Create a MongoDB archive backup for the current Docker Compose stack.

Environment:
  COMPOSE_ARGS                Extra docker compose arguments, for example:
							  -f compose.yaml -f compose.production.yaml
  MONGO_BACKUP_DIR            Output directory for Mongo archives.
  MONGO_BACKUP_KEEP_RECENT    Number of newest archives to always retain.
  MONGO_BACKUP_KEEP_DAILY     Number of day-level archives to retain after recent backups.
  BACKUP_OFFSITE_DIR          Optional mounted directory for a second copy.
  BACKUP_DRY_RUN              If truthy, print actions without creating a backup.
EOF
		exit 0
		;;
esac

ARCHIVE_PATH=${1:-$DEFAULT_ARCHIVE}
ARCHIVE_PATH=$(resolve_path "$ARCHIVE_PATH")
TMP_ARCHIVE_PATH=${ARCHIVE_PATH}.tmp

ensure_docker_compose
ensure_parent_dir "$ARCHIVE_PATH"

if is_truthy "${BACKUP_DRY_RUN:-0}"; then
	log "Dry run: would create MongoDB backup at $ARCHIVE_PATH"
	log "Dry run: would retain $BACKUP_KEEP_RECENT recent backups and $BACKUP_KEEP_DAILY daily checkpoints"
	if [ -n "${BACKUP_OFFSITE_DIR:-}" ]; then
		log "Dry run: would copy archive to $(resolve_path "$BACKUP_OFFSITE_DIR")"
	fi
	exit 0
fi

cd "$ROOT_DIR"

if ! compose_service_running mongodb; then
	fail "The mongodb service is not running for the selected Compose stack."
fi

trap 'rm -f "$TMP_ARCHIVE_PATH"' INT TERM EXIT
docker compose $COMPOSE_ARGS exec -T mongodb sh -c 'mongodump --archive --gzip' > "$TMP_ARCHIVE_PATH"
mv "$TMP_ARCHIVE_PATH" "$ARCHIVE_PATH"
trap - INT TERM EXIT

write_checksum_file "$ARCHIVE_PATH"
prune_backup_dir "$BACKUP_DIR" "$BACKUP_PREFIX" "$BACKUP_KEEP_RECENT" "$BACKUP_KEEP_DAILY"
copy_archive_offsite "$ARCHIVE_PATH"
print_backup_summary "$ARCHIVE_PATH"