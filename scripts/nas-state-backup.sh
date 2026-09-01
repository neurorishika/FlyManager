#!/bin/sh
# Back up the FlyManager state that mongodump does NOT cover.
#
# The Mongo backup captures every collection and GridFS blob. It does not
# capture what lives on the filesystem, nor the stack's environment -- and the
# Portainer deployment has no git checkout, so scripts/state-backup.sh in the
# repo cannot run there. This script is self-contained for exactly that reason:
# copy it to the NAS and schedule it.
#
# What it captures:
#   - data/uploads and generated_labels (user files; nothing else holds them)
#   - the app container's environment, which is where SECRET_KEY, the admin
#     credentials and the SMTP credentials exist and NOWHERE else. Losing
#     SECRET_KEY invalidates every session; losing the rest means locking
#     yourself out of your own instance.
#
# The environment is encrypted to a PUBLIC key. The NAS can encrypt and cannot
# decrypt, so neither a compromised NAS nor a leaked off-site copy exposes the
# secrets. Decrypt with the private key you kept off the NAS:
#
#   openssl smime -decrypt -binary -inform DEM \
#     -in flymanager_env_<stamp>.env.enc \
#     -inkey state-backup-private.pem
#
# Usage: nas-state-backup.sh [--dry-run]
set -eu

DOCKER=${DOCKER:-/usr/local/bin/docker}
ROOT=${FLYMANAGER_ROOT:-/volume1/docker/flymanager}
BACKUP_DIR=${STATE_BACKUP_DIR:-$ROOT/backups/state}
PUBLIC_KEY=${STATE_BACKUP_PUBLIC_KEY:-$ROOT/scripts/state-backup-public.pem}
APP_CONTAINER=${APP_CONTAINER:-flymanager-app}
KEEP_DAYS=${STATE_BACKUP_KEEP_DAYS:-30}
# Never prune below this many, however old. Retention that can empty the
# directory is how a silent failure becomes total loss -- see the Mongo
# backup script for the same guard and the same reason.
MIN_KEEP=${STATE_BACKUP_MIN_KEEP:-3}
RCLONE_REMOTE=${RCLONE_REMOTE:-}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)

if [ "${1:-}" = "--dry-run" ]; then
    echo "Dry run: would write $BACKUP_DIR/flymanager_state_${STAMP}.tar.gz"
    echo "Dry run: would write $BACKUP_DIR/flymanager_env_${STAMP}.env.enc"
    echo "Dry run: public key $PUBLIC_KEY $( [ -f "$PUBLIC_KEY" ] && echo present || echo MISSING )"
    exit 0
fi

[ -f "$PUBLIC_KEY" ] || { echo "ERROR: no public key at $PUBLIC_KEY" >&2; exit 1; }
mkdir -p "$BACKUP_DIR"

# --- files -----------------------------------------------------------------
STATE_ARCHIVE="$BACKUP_DIR/flymanager_state_${STAMP}.tar.gz"
TMP_STATE="${STATE_ARCHIVE}.tmp"
trap 'rm -f "$TMP_STATE"' INT TERM EXIT
tar czf "$TMP_STATE" -C "$ROOT" \
    $( [ -d "$ROOT/data/uploads" ] && echo data/uploads ) \
    $( [ -d "$ROOT/generated_labels" ] && echo generated_labels ) \
    $( [ -f "$ROOT/data/bloomington.csv" ] && echo data/bloomington.csv )
mv "$TMP_STATE" "$STATE_ARCHIVE"
trap - INT TERM EXIT
sha256sum "$STATE_ARCHIVE" > "${STATE_ARCHIVE}.sha256"
echo "State archive: $STATE_ARCHIVE"

# --- environment -----------------------------------------------------------
# Straight from the running container, so no Portainer API key has to live on
# the NAS. Piped directly into openssl: the plaintext never touches disk.
ENV_ARCHIVE="$BACKUP_DIR/flymanager_env_${STAMP}.env.enc"
"$DOCKER" inspect "$APP_CONTAINER" --format '{{range .Config.Env}}{{println .}}{{end}}' \
    | openssl smime -encrypt -binary -aes-256-cbc -outform DEM -out "$ENV_ARCHIVE" "$PUBLIC_KEY"
chmod 600 "$ENV_ARCHIVE"
sha256sum "$ENV_ARCHIVE" > "${ENV_ARCHIVE}.sha256"
echo "Environment archive (encrypted): $ENV_ARCHIVE"

# --- retention -------------------------------------------------------------
SURVIVING=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'flymanager_state_*.tar.gz' | wc -l)
if [ "$SURVIVING" -gt "$MIN_KEEP" ]; then
    find "$BACKUP_DIR" -maxdepth 1 -type f \
        \( -name 'flymanager_state_*' -o -name 'flymanager_env_*' \) \
        -mtime +"$KEEP_DAYS" -delete
else
    echo "Retention skipped: only $SURVIVING state archive(s) present (minimum $MIN_KEEP)."
fi

# --- off-site --------------------------------------------------------------
if [ -n "$RCLONE_REMOTE" ]; then
    for f in "$STATE_ARCHIVE" "${STATE_ARCHIVE}.sha256" "$ENV_ARCHIVE" "${ENV_ARCHIVE}.sha256"; do
        rclone copy "$f" "${RCLONE_REMOTE}/state/"
    done
    echo "Copied off-site to ${RCLONE_REMOTE}/state/"
fi

echo "State backup complete."
