#!/bin/sh

set -eu

DEFAULT_ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ROOT_DIR=${FLYMANAGER_ROOT_DIR:-$DEFAULT_ROOT_DIR}
case "$ROOT_DIR" in
    /*)
        ;;
    *)
        ROOT_DIR=$DEFAULT_ROOT_DIR/$ROOT_DIR
        ;;
esac
ENV_FILE=${ENV_FILE:-$ROOT_DIR/.env}

load_backup_env() {
    [ -f "$ENV_FILE" ] || return 0

    while IFS= read -r raw_line || [ -n "$raw_line" ]; do
        case "$raw_line" in
            ''|\#*)
                continue
                ;;
        esac

        key=${raw_line%%=*}
        value=${raw_line#*=}
        key=$(printf '%s' "$key" | tr -d '[:space:]')
        value=$(printf '%s' "$value" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')

        case "$key" in
            ''|*[!A-Za-z0-9_]*)
                warn "Skipping non-standard dotenv entry in $ENV_FILE: $raw_line"
                continue
                ;;
        esac

        case "$value" in
            \"*\")
                value=${value#\"}
                value=${value%\"}
                ;;
            \'*\')
                value=${value#\'}
                value=${value%\'}
                ;;
        esac

        export "$key=$value"
    done < "$ENV_FILE"
}

log() {
    printf '%s\n' "$*"
}

warn() {
    printf 'WARN: %s\n' "$*" >&2
}

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        fail "$2"
    fi
}

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

resolve_path() {
    case "$1" in
        /*)
            printf '%s\n' "$1"
            ;;
        *)
            printf '%s/%s\n' "$ROOT_DIR" "$1"
            ;;
    esac
}

ensure_parent_dir() {
    mkdir -p "$(dirname "$1")"
}

ensure_directory() {
    mkdir -p "$1"
}

ensure_docker_compose() {
    require_command docker "Docker is required but was not found in PATH."
    if ! docker compose version >/dev/null 2>&1; then
        fail "Docker Compose v2 is required but is not available."
    fi
}

docker_compose_available() {
    if ! command -v docker >/dev/null 2>&1; then
        return 1
    fi

    docker compose version >/dev/null 2>&1
}

compose_service_running() {
    service_name=$1
    docker compose $COMPOSE_ARGS ps --services --status running | grep -qx "$service_name"
}

checksum_command() {
    if command -v shasum >/dev/null 2>&1; then
        printf 'shasum -a 256'
        return 0
    fi

    if command -v sha256sum >/dev/null 2>&1; then
        printf 'sha256sum'
        return 0
    fi

    return 1
}

write_checksum_file() {
    archive_path=$1
    checksum_cmd=$(checksum_command || true)
    checksum_path=${archive_path}.sha256

    if [ -z "$checksum_cmd" ]; then
        warn "No SHA-256 tool found. Skipping checksum generation for $archive_path."
        return 0
    fi

    ensure_parent_dir "$checksum_path"
    if [ "$checksum_cmd" = "shasum -a 256" ]; then
        shasum -a 256 "$archive_path" > "$checksum_path"
    else
        sha256sum "$archive_path" > "$checksum_path"
    fi
}

archive_day_key() {
    archive_name=$1
    prefix=$2
    stamp=${archive_name#${prefix}_}
    day_key=$(printf '%s' "$stamp" | sed -E 's/^([0-9]{8}).*/\1/')

    if [ -z "$day_key" ] || [ "$day_key" = "$stamp" ]; then
        printf 'unknown\n'
        return 0
    fi

    printf '%s\n' "$day_key"
}

prune_backup_dir() {
    backup_dir=$1
    prefix=$2
    keep_recent=$3
    keep_daily=$4

    [ -d "$backup_dir" ] || return 0

    archive_list=$(
        find "$backup_dir" -maxdepth 1 -type f \
            \( -name "${prefix}_*.archive.gz" -o -name "${prefix}_*.tar.gz" \) \
            -print | LC_ALL=C sort -r
    )

    [ -n "$archive_list" ] || return 0

    recent_kept=0
    daily_kept=0
    daily_keys='|'

    printf '%s\n' "$archive_list" | while IFS= read -r archive_path; do
        [ -n "$archive_path" ] || continue
        archive_name=$(basename "$archive_path")

        if [ "$recent_kept" -lt "$keep_recent" ]; then
            recent_kept=$((recent_kept + 1))
            continue
        fi

        day_key=$(archive_day_key "$archive_name" "$prefix")
        case "$daily_keys" in
            *"|$day_key|"*)
                ;;
            *)
                if [ "$daily_kept" -lt "$keep_daily" ]; then
                    daily_keys=${daily_keys}${day_key}'|'
                    daily_kept=$((daily_kept + 1))
                    continue
                fi
                ;;
        esac

        rm -f "$archive_path" "${archive_path}.sha256"
        log "Pruned old backup $archive_path"
    done
}

copy_archive_offsite() {
    archive_path=$1
    offsite_dir=${BACKUP_OFFSITE_DIR:-}

    [ -n "$offsite_dir" ] || return 0

    offsite_dir=$(resolve_path "$offsite_dir")
    ensure_directory "$offsite_dir"

    archive_target="$offsite_dir/$(basename "$archive_path")"
    cp "$archive_path" "$archive_target"

    if [ -f "${archive_path}.sha256" ]; then
        cp "${archive_path}.sha256" "${archive_target}.sha256"
    fi

    prune_backup_dir "$offsite_dir" "$BACKUP_PREFIX" "$BACKUP_KEEP_RECENT" "$BACKUP_KEEP_DAILY"
}

print_backup_summary() {
    archive_path=$1
    log "Backup written to $archive_path"
    if [ -n "${BACKUP_OFFSITE_DIR:-}" ]; then
        log "Offsite copy written to $(resolve_path "$BACKUP_OFFSITE_DIR")/$(basename "$archive_path")"
    fi
}