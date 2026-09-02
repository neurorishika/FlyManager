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
# Explicit, because DSM Task Scheduler runs this as root and root has no
# rclone config of its own -- the one created by `rclone config` lives under
# the interactive user's home. Without this the off-site push silently finds
# no remote.
RCLONE_CONFIG=${RCLONE_CONFIG:-$ROOT/scripts/rclone.conf}
export RCLONE_CONFIG
STAMP=$(date -u +%Y%m%dT%H%M%SZ)

if [ "${1:-}" = "--dry-run" ]; then
    echo "Dry run: would write $BACKUP_DIR/flymanager_state_${STAMP}.tar.gz"
    echo "Dry run: would write $BACKUP_DIR/flymanager_env_${STAMP}.env.enc"
    echo "Dry run: public key $PUBLIC_KEY $( [ -f "$PUBLIC_KEY" ] && echo present || echo MISSING )"
    exit 0
fi

# --- BEGIN EMBEDDED gfs-select.awk (canonical: scripts/lib/gfs-select.awk;
#     tests/test_gfs_retention.py fails if these drift apart) ---
gfs_select_awk() {
    cat <<'GFSAWK'
# Grandfather-father-son selection over timestamped backup names.
#
# Reads candidate filenames on stdin, writes the ones to DELETE on stdout.
# Keeps: every archive inside KEEP_ALL_DAYS, then one per day to KEEP_DAILY,
# one per 7-day bucket to KEEP_WEEKLY, one per month to KEEP_MONTHLY, and
# nothing older. Within each bucket the NEWEST survives.
#
# Flat "delete older than N days" retention cannot bound storage and can empty
# the directory outright when backups start failing silently; this bounds total
# archives forever (~110) regardless of how far back you keep them.
#
# Portable on purpose: busybox awk on the backup container has no mktime(), so
# ages come from Julian day numbers computed here.
#
# Required: -v TODAY=YYYYMMDD  -v MIN_KEEP=N
# Optional: KEEP_ALL_DAYS, KEEP_DAILY, KEEP_WEEKLY, KEEP_MONTHLY

function jdn(y, m, d,    a, yy, mm) {
    a = int((14 - m) / 12); yy = y + 4800 - a; mm = m + 12 * a - 3
    return d + int((153 * mm + 2) / 5) + 365 * yy + int(yy / 4) \
             - int(yy / 100) + int(yy / 400) - 32045
}

BEGIN {
    if (KEEP_ALL_DAYS  == "") KEEP_ALL_DAYS  = 7
    if (KEEP_DAILY     == "") KEEP_DAILY     = 30
    if (KEEP_WEEKLY    == "") KEEP_WEEKLY    = 180
    if (KEEP_MONTHLY   == "") KEEP_MONTHLY   = 730
    today = jdn(substr(TODAY,1,4) + 0, substr(TODAY,5,2) + 0, substr(TODAY,7,2) + 0)
    n = 0
}

{
    # Pull the first YYYYMMDD out of the name; anything without one is left
    # strictly alone rather than guessed at.
    if (match($0, /[0-9]{8}T[0-9]{6}Z/) == 0) next
    stamp = substr($0, RSTART, RLENGTH)
    names[++n] = $0
    stamps[n] = stamp
}

END {
    # Newest first, so the survivor of each bucket is the most recent.
    for (i = 1; i <= n; i++)
        for (j = i + 1; j <= n; j++)
            if (stamps[j] > stamps[i]) {
                t = stamps[i]; stamps[i] = stamps[j]; stamps[j] = t
                t = names[i];  names[i]  = names[j];  names[j]  = t
            }

    kept = 0
    for (i = 1; i <= n; i++) {
        s = stamps[i]
        age = today - jdn(substr(s,1,4) + 0, substr(s,5,2) + 0, substr(s,7,2) + 0)
        keep = 0
        if (age <= KEEP_ALL_DAYS) {
            keep = 1                                   # everything, recent
        } else if (age <= KEEP_DAILY) {
            bucket = "d" substr(s,1,8)
        } else if (age <= KEEP_WEEKLY) {
            bucket = "w" int(jdn(substr(s,1,4)+0, substr(s,5,2)+0, substr(s,7,2)+0) / 7)
        } else if (age <= KEEP_MONTHLY) {
            bucket = "m" substr(s,1,6)
        } else {
            bucket = ""                                # past the horizon
        }

        if (!keep && bucket != "" && !(bucket in seen)) { seen[bucket] = 1; keep = 1 }

        # The floor: never prune below MIN_KEEP, however old. Retention that
        # can empty the directory turns a silent failure into total loss.
        if (!keep && kept < MIN_KEEP) keep = 1
        if (keep) kept++; else print names[i]
    }
}
GFSAWK
}

# Delete the archives GFS did not select, taking each one's .sha256 with
# it. Never deletes a file whose name carries no timestamp.
gfs_prune_local() {
    _dir=$1; _glob=$2; _min_keep=$3
    _awk=$(mktemp); gfs_select_awk > "$_awk"
    find "$_dir" -maxdepth 1 -type f -name "$_glob" \
        | awk -f "$_awk" -v TODAY="$(date -u +%Y%m%d)" -v MIN_KEEP="$_min_keep" \
        | while read -r _victim; do rm -f "$_victim" "${_victim}.sha256"; done
    rm -f "$_awk"
}

# Same selection against an rclone remote.
gfs_prune_remote() {
    _remote=$1; _glob=$2; _min_keep=$3
    [ -n "$_remote" ] || return 0
    _awk=$(mktemp); gfs_select_awk > "$_awk"
    rclone lsf "$_remote" --include "$_glob" 2>/dev/null \
        | awk -f "$_awk" -v TODAY="$(date -u +%Y%m%d)" -v MIN_KEEP="$_min_keep" \
        | while read -r _victim; do
            rclone deletefile "${_remote}/${_victim}" 2>/dev/null || true
            rclone deletefile "${_remote}/${_victim}.sha256" 2>/dev/null || true
          done
    rm -f "$_awk"
}
# --- END EMBEDDED gfs-select.awk ---

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
# Checksummed by BARE FILENAME, not by path: sha256sum records whatever
# argument it was given, so an absolute path makes the .sha256 verifiable
# only on a machine where that exact path exists -- never after pulling
# the pair down from off-site, which is the one time it matters most.
( cd "$BACKUP_DIR" && sha256sum "$(basename "$STATE_ARCHIVE")" > "$(basename "$STATE_ARCHIVE").sha256" )
echo "State archive: $STATE_ARCHIVE"

# --- environment -----------------------------------------------------------
# Straight from the running container, so no Portainer API key has to live on
# the NAS. Piped directly into openssl: the plaintext never touches disk.
ENV_ARCHIVE="$BACKUP_DIR/flymanager_env_${STAMP}.env.enc"
"$DOCKER" inspect "$APP_CONTAINER" --format '{{range .Config.Env}}{{println .}}{{end}}' \
    | openssl smime -encrypt -binary -aes-256-cbc -outform DEM -out "$ENV_ARCHIVE" "$PUBLIC_KEY"
chmod 600 "$ENV_ARCHIVE"
( cd "$BACKUP_DIR" && sha256sum "$(basename "$ENV_ARCHIVE")" > "$(basename "$ENV_ARCHIVE").sha256" )
echo "Environment archive (encrypted): $ENV_ARCHIVE"

# --- retention -------------------------------------------------------------
gfs_prune_local "$BACKUP_DIR" 'flymanager_state_*.tar.gz' "$MIN_KEEP"
gfs_prune_local "$BACKUP_DIR" 'flymanager_env_*.env.enc' "$MIN_KEEP"

# --- off-site --------------------------------------------------------------
if [ -n "$RCLONE_REMOTE" ]; then
    for f in "$STATE_ARCHIVE" "${STATE_ARCHIVE}.sha256" "$ENV_ARCHIVE" "${ENV_ARCHIVE}.sha256"; do
        rclone copy "$f" "${RCLONE_REMOTE}/state/"
    done
    gfs_prune_remote "${RCLONE_REMOTE}/state" 'flymanager_state_*.tar.gz' "$MIN_KEEP"
    gfs_prune_remote "${RCLONE_REMOTE}/state" 'flymanager_env_*.env.enc' "$MIN_KEEP"
    echo "Copied off-site to ${RCLONE_REMOTE}/state/"
fi

echo "State backup complete."
