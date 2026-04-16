#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck disable=SC1091
. "$SCRIPT_DIR/backup-common.sh"

load_backup_env

COMPOSE_ARGS=${COMPOSE_ARGS:-}
BACKUP_PREFIX=${BACKUP_PREFIX:-flymanager_state}
BACKUP_DIR=${STATE_BACKUP_DIR:-${BACKUP_ROOT:-backups/state}}
BACKUP_DIR=$(resolve_path "$BACKUP_DIR")
BACKUP_KEEP_RECENT=${STATE_BACKUP_KEEP_RECENT:-${BACKUP_KEEP_RECENT:-14}}
BACKUP_KEEP_DAILY=${STATE_BACKUP_KEEP_DAILY:-${BACKUP_KEEP_DAILY:-30}}
BACKUP_INCLUDE_CADDY=${BACKUP_INCLUDE_CADDY:-auto}
ACTIVE_ENV_FILE=$(resolve_path "${ENV_FILE:-$ROOT_DIR/.env}")
TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
DEFAULT_ARCHIVE="$BACKUP_DIR/${BACKUP_PREFIX}_${TIMESTAMP}.tar.gz"

case "${1:-}" in
    -h|--help)
        cat <<'EOF'
Usage: ./scripts/state-backup.sh [archive-path]

Create a deployment-state backup containing runtime data outside MongoDB.

Environment:
  COMPOSE_ARGS                Extra docker compose arguments, for example:
                              -f compose.yaml -f compose.production.yaml
  STATE_BACKUP_DIR            Output directory for state archives.
  STATE_BACKUP_KEEP_RECENT    Number of newest archives to always retain.
  STATE_BACKUP_KEEP_DAILY     Number of day-level archives to retain after recent backups.
  BACKUP_INCLUDE_CADDY        auto, 1, or 0.
  BACKUP_OFFSITE_DIR          Optional mounted directory for a second copy.
  BACKUP_DRY_RUN              If truthy, print actions without creating an archive.
EOF
        exit 0
        ;;
esac

ARCHIVE_PATH=${1:-$DEFAULT_ARCHIVE}
ARCHIVE_PATH=$(resolve_path "$ARCHIVE_PATH")
TMP_ARCHIVE_PATH=${ARCHIVE_PATH}.tmp
STAGING_DIR=$(mktemp -d "${TMPDIR:-/tmp}/flymanager-state-backup.XXXXXX")

cleanup() {
    rm -rf "$STAGING_DIR"
    rm -f "$TMP_ARCHIVE_PATH"
}

trap cleanup INT TERM EXIT

ensure_parent_dir "$ARCHIVE_PATH"

if is_truthy "${BACKUP_DRY_RUN:-0}"; then
    log "Dry run: would create deployment-state backup at $ARCHIVE_PATH"
    log "Dry run: would include .env, data/, compose files, and optional Caddy state"
    if [ -n "${BACKUP_OFFSITE_DIR:-}" ]; then
        log "Dry run: would copy archive to $(resolve_path "$BACKUP_OFFSITE_DIR")"
    fi
    exit 0
fi

ensure_directory "$STAGING_DIR/runtime"
ensure_directory "$STAGING_DIR/config"

ENV_RESTORE_PATH=.env
case "$ACTIVE_ENV_FILE" in
    "$ROOT_DIR"/*)
        ENV_RESTORE_PATH=${ACTIVE_ENV_FILE#"$ROOT_DIR"/}
        ;;
    *)
        warn "ENV_FILE points outside the repository root. The backup will restore it as .env by default."
        ;;
esac

if [ -f "$ACTIVE_ENV_FILE" ]; then
    cp "$ACTIVE_ENV_FILE" "$STAGING_DIR/runtime/env-file"
else
    warn "No env file found at $ACTIVE_ENV_FILE; continuing without it."
fi

if [ -d "$ROOT_DIR/data" ]; then
    cp -R "$ROOT_DIR/data" "$STAGING_DIR/runtime/data"
else
    warn "No data directory found at $ROOT_DIR/data; continuing without it."
fi

for config_file in compose.yaml compose.production.yaml deploy/Caddyfile; do
    if [ -f "$ROOT_DIR/$config_file" ]; then
        ensure_parent_dir "$STAGING_DIR/config/$config_file"
        cp "$ROOT_DIR/$config_file" "$STAGING_DIR/config/$config_file"
    fi
done

include_caddy=0
case "$BACKUP_INCLUDE_CADDY" in
    1)
        ensure_docker_compose
        include_caddy=1
        ;;
    auto)
        if docker_compose_available && docker compose $COMPOSE_ARGS config --services >/dev/null 2>&1 && \
            docker compose $COMPOSE_ARGS config --services | grep -qx proxy; then
            include_caddy=1
        fi
        ;;
    0)
        include_caddy=0
        ;;
    *)
        fail "BACKUP_INCLUDE_CADDY must be one of: auto, 1, 0"
        ;;
esac

if [ "$include_caddy" -eq 1 ]; then
    ensure_directory "$STAGING_DIR/runtime/caddy_data"
    ensure_directory "$STAGING_DIR/runtime/caddy_config"

    if ! docker compose $COMPOSE_ARGS run --rm -T proxy sh -c 'tar -C /data -cf - .' | tar -xf - -C "$STAGING_DIR/runtime/caddy_data"; then
        if [ "$BACKUP_INCLUDE_CADDY" = "1" ]; then
            fail "Failed to back up Caddy /data volume."
        fi
        warn "Failed to back up Caddy /data volume; continuing because BACKUP_INCLUDE_CADDY=auto."
        rm -rf "$STAGING_DIR/runtime/caddy_data"
    fi

    if ! docker compose $COMPOSE_ARGS run --rm -T proxy sh -c 'tar -C /config -cf - .' | tar -xf - -C "$STAGING_DIR/runtime/caddy_config"; then
        if [ "$BACKUP_INCLUDE_CADDY" = "1" ]; then
            fail "Failed to back up Caddy /config volume."
        fi
        warn "Failed to back up Caddy /config volume; continuing because BACKUP_INCLUDE_CADDY=auto."
        rm -rf "$STAGING_DIR/runtime/caddy_config"
    fi
fi

cat > "$STAGING_DIR/manifest.txt" <<EOF
generated_at_utc=$TIMESTAMP
root_dir=$ROOT_DIR
compose_args=${COMPOSE_ARGS:-<none>}
includes_env=$( [ -f "$ACTIVE_ENV_FILE" ] && printf yes || printf no )
env_restore_path=$ENV_RESTORE_PATH
includes_data=$( [ -d "$ROOT_DIR/data" ] && printf yes || printf no )
includes_caddy=$( [ "$include_caddy" -eq 1 ] && printf yes || printf no )
EOF

tar -C "$STAGING_DIR" -czf "$TMP_ARCHIVE_PATH" .
mv "$TMP_ARCHIVE_PATH" "$ARCHIVE_PATH"

write_checksum_file "$ARCHIVE_PATH"
prune_backup_dir "$BACKUP_DIR" "$BACKUP_PREFIX" "$BACKUP_KEEP_RECENT" "$BACKUP_KEEP_DAILY"
copy_archive_offsite "$ARCHIVE_PATH"
print_backup_summary "$ARCHIVE_PATH"

trap - INT TERM EXIT
cleanup