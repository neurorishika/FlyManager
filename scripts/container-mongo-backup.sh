#!/bin/sh
set -eu

MONGO_HOST=${MONGO_HOST:-mongodb}
MONGO_PORT=${MONGO_PORT:-27017}
BACKUP_DIR=${BACKUP_DIR:-/backups}
BACKUP_KEEP_DAYS=${BACKUP_KEEP_DAYS:-14}
RCLONE_REMOTE=${RCLONE_REMOTE:-}
HEALTHCHECK_UUID=${HEALTHCHECK_UUID:-}
BACKUP_DRY_RUN=${BACKUP_DRY_RUN:-0}

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

TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
ARCHIVE="$BACKUP_DIR/flymanager_mongodb_${TIMESTAMP}.archive.gz"

if is_truthy "$BACKUP_DRY_RUN"; then
    echo "Dry run: would run mongodump --host ${MONGO_HOST}:${MONGO_PORT} --oplog --archive=$ARCHIVE --gzip"
    echo "Dry run: would write checksum to ${ARCHIVE}.sha256"
    echo "Dry run: would prune archives older than $BACKUP_KEEP_DAYS days in $BACKUP_DIR"
    if [ -n "$RCLONE_REMOTE" ]; then
        echo "Dry run: would run rclone copy $ARCHIVE $RCLONE_REMOTE/"
    fi
    if [ -n "$HEALTHCHECK_UUID" ]; then
        echo "Dry run: would ping https://hc-ping.com/$HEALTHCHECK_UUID"
    fi
    exit 0
fi

mkdir -p "$BACKUP_DIR"

mongodump \
    --host "${MONGO_HOST}:${MONGO_PORT}" \
    --oplog \
    --archive="$ARCHIVE" \
    --gzip

sha256sum "$ARCHIVE" > "${ARCHIVE}.sha256"

find "$BACKUP_DIR" -maxdepth 1 -type f -name 'flymanager_mongodb_*.archive.gz*' \
    -mtime +"$BACKUP_KEEP_DAYS" -delete

if [ -n "$RCLONE_REMOTE" ]; then
    rclone copy "$ARCHIVE" "${RCLONE_REMOTE}/"
    rclone copy "${ARCHIVE}.sha256" "${RCLONE_REMOTE}/"
fi

if [ -n "$HEALTHCHECK_UUID" ]; then
    curl -fsS -m 10 --retry 3 "https://hc-ping.com/${HEALTHCHECK_UUID}" > /dev/null || true
fi

echo "Backup complete: $ARCHIVE"
