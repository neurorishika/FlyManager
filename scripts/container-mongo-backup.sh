#!/bin/sh
set -eu

MONGO_HOST=${MONGO_HOST:-mongodb}
MONGO_PORT=${MONGO_PORT:-27017}
BACKUP_DIR=${BACKUP_DIR:-/backups}
BACKUP_KEEP_DAYS=${BACKUP_KEEP_DAYS:-14}
# Never prune below this many archives, however old they are.
BACKUP_MIN_KEEP=${BACKUP_MIN_KEEP:-3}
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

# Dump to .tmp and rename: a container killed mid-dump otherwise leaves a
# truncated archive under a valid name, which retention then treats as a
# keeper and no restore path ever checks.
TMP_ARCHIVE="${ARCHIVE}.tmp"
trap 'rm -f "$TMP_ARCHIVE"' INT TERM EXIT
mongodump \
    --host "${MONGO_HOST}:${MONGO_PORT}" \
    --oplog \
    --archive="$TMP_ARCHIVE" \
    --gzip
mv "$TMP_ARCHIVE" "$ARCHIVE"
trap - INT TERM EXIT

sha256sum "$ARCHIVE" > "${ARCHIVE}.sha256"

# Retention only ever runs after a dump that just succeeded, and never deletes
# the last archives standing. Backups failing silently -- disk full, auth
# change, mongodump missing -- used to be followed by retention quietly
# deleting everything that ever existed once it aged past the window.
SURVIVING=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'flymanager_mongodb_*.archive.gz' | wc -l)
if [ "$SURVIVING" -gt "$BACKUP_MIN_KEEP" ]; then
    find "$BACKUP_DIR" -maxdepth 1 -type f -name 'flymanager_mongodb_*.archive.gz*' \
        -mtime +"$BACKUP_KEEP_DAYS" -delete
else
    echo "Retention skipped: only $SURVIVING archive(s) present (minimum $BACKUP_MIN_KEEP)."
fi

if [ -n "$RCLONE_REMOTE" ]; then
    rclone copy "$ARCHIVE" "${RCLONE_REMOTE}/"
    rclone copy "${ARCHIVE}.sha256" "${RCLONE_REMOTE}/"
fi

if [ -n "$HEALTHCHECK_UUID" ]; then
    curl -fsS -m 10 --retry 3 "https://hc-ping.com/${HEALTHCHECK_UUID}" > /dev/null || true
fi

echo "Backup complete: $ARCHIVE"
