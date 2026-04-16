#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck disable=SC1091
. "$SCRIPT_DIR/backup-common.sh"

load_backup_env

COMPOSE_ARGS=${COMPOSE_ARGS:-}
RESTORE_INCLUDE_CADDY=${RESTORE_INCLUDE_CADDY:-auto}
RESTORE_PRECHECK_DIR=${RESTORE_PRECHECK_DIR:-${BACKUP_ROOT:-backups}/restore-preflight}
RESTORE_PRECHECK_DIR=$(resolve_path "$RESTORE_PRECHECK_DIR")
ARCHIVE_PATH=${1:-}
WORK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/flymanager-state-restore.XXXXXX")
TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)

cleanup() {
    rm -rf "$WORK_DIR"
}

trap cleanup INT TERM EXIT

case "$ARCHIVE_PATH" in
    -h|--help)
        cat <<'EOF'
Usage: ./scripts/state-restore.sh path/to/state-backup.tar.gz

Restore deployment-state data from a FlyManager state backup archive.

Environment:
  COMPOSE_ARGS           Extra docker compose arguments, for example:
                         -f compose.yaml -f compose.production.yaml
  RESTORE_INCLUDE_CADDY  auto, 1, or 0.
  RESTORE_PRECHECK_DIR   Directory used to store the current .env/data backup before overwrite.
  BACKUP_DRY_RUN         If truthy, print actions without restoring files.
EOF
        exit 0
        ;;
esac

if [ -z "$ARCHIVE_PATH" ]; then
    fail "Usage: ./scripts/state-restore.sh path/to/state-backup.tar.gz"
fi

ARCHIVE_PATH=$(resolve_path "$ARCHIVE_PATH")
[ -f "$ARCHIVE_PATH" ] || fail "State backup archive not found: $ARCHIVE_PATH"

if is_truthy "${BACKUP_DRY_RUN:-0}"; then
    log "Dry run: would restore deployment state from $ARCHIVE_PATH"
    exit 0
fi

tar -C "$WORK_DIR" -xzf "$ARCHIVE_PATH"

MANIFEST_PATH=$WORK_DIR/manifest.txt
[ -f "$MANIFEST_PATH" ] || fail "State backup archive is missing manifest.txt"

ENV_RESTORE_PATH=$(awk -F '=' '/^env_restore_path=/{print $2}' "$MANIFEST_PATH")
[ -n "$ENV_RESTORE_PATH" ] || ENV_RESTORE_PATH=.env
CURRENT_ENV_PATH=$(resolve_path "$ENV_RESTORE_PATH")
DATA_SOURCE_DIR=$WORK_DIR/runtime/data
ENV_SOURCE_PATH=$WORK_DIR/runtime/env-file
RESTORE_SNAPSHOT_DIR=$RESTORE_PRECHECK_DIR/$TIMESTAMP

ensure_directory "$RESTORE_SNAPSHOT_DIR"

if [ -f "$ENV_SOURCE_PATH" ]; then
    if [ -f "$CURRENT_ENV_PATH" ]; then
        ensure_parent_dir "$RESTORE_SNAPSHOT_DIR/$ENV_RESTORE_PATH"
        cp "$CURRENT_ENV_PATH" "$RESTORE_SNAPSHOT_DIR/$ENV_RESTORE_PATH"
    fi
    ensure_parent_dir "$CURRENT_ENV_PATH"
    cp "$ENV_SOURCE_PATH" "$CURRENT_ENV_PATH"
    log "Restored env file to $CURRENT_ENV_PATH"
fi

if [ -d "$DATA_SOURCE_DIR" ]; then
    if [ -d "$ROOT_DIR/data" ]; then
        mv "$ROOT_DIR/data" "$RESTORE_SNAPSHOT_DIR/data"
    fi
    cp -R "$DATA_SOURCE_DIR" "$ROOT_DIR/data"
    log "Restored data directory to $ROOT_DIR/data"
fi

include_caddy=0
proxy_available=0
case "$RESTORE_INCLUDE_CADDY" in
    1)
        include_caddy=1
        ;;
    auto)
        if [ -d "$WORK_DIR/runtime/caddy_data" ] || [ -d "$WORK_DIR/runtime/caddy_config" ]; then
            include_caddy=1
        fi
        ;;
    0)
        include_caddy=0
        ;;
    *)
        fail "RESTORE_INCLUDE_CADDY must be one of: auto, 1, 0"
        ;;
esac

if [ "$include_caddy" -eq 1 ]; then
    if ! docker_compose_available; then
        if [ "$RESTORE_INCLUDE_CADDY" = "1" ]; then
            fail "Caddy restore was requested, but Docker Compose is not available."
        fi
        warn "Skipping Caddy restore because Docker Compose is not available."
        include_caddy=0
    else
        ensure_docker_compose
    fi
fi

if [ "$include_caddy" -eq 1 ]; then
    if docker compose $COMPOSE_ARGS config --services >/dev/null 2>&1 && \
        docker compose $COMPOSE_ARGS config --services | grep -qx proxy; then
        proxy_available=1
    fi

    if [ "$proxy_available" -eq 0 ]; then
        if [ "$RESTORE_INCLUDE_CADDY" = "1" ]; then
            fail "Caddy restore was requested, but the selected Compose stack does not define a proxy service."
        fi
        warn "Skipping Caddy restore because the selected Compose stack does not define a proxy service."
        include_caddy=0
    fi
fi

restore_volume_dir() {
    source_dir=$1
    target_dir=$2
    [ -d "$source_dir" ] || return 0

    tar -C "$source_dir" -cf - . | docker compose $COMPOSE_ARGS run --rm -T proxy sh -c "mkdir -p $target_dir && rm -rf $target_dir/* && tar -C $target_dir -xf -"
}

if [ "$include_caddy" -eq 1 ]; then
    restore_volume_dir "$WORK_DIR/runtime/caddy_data" /data
    restore_volume_dir "$WORK_DIR/runtime/caddy_config" /config
    if compose_service_running proxy; then
        docker compose $COMPOSE_ARGS restart proxy >/dev/null
    fi
    log "Restored Caddy runtime volumes"
fi

if [ -d "$WORK_DIR/config" ]; then
    REFERENCE_DIR=$RESTORE_SNAPSHOT_DIR/reference-config
    mkdir -p "$REFERENCE_DIR"
    cp -R "$WORK_DIR/config/." "$REFERENCE_DIR/"
    log "Extracted backup config snapshot to $REFERENCE_DIR"
fi

log "Deployment-state restore completed from $ARCHIVE_PATH"
log "Previous local state was preserved under $RESTORE_SNAPSHOT_DIR"

trap - INT TERM EXIT
cleanup
