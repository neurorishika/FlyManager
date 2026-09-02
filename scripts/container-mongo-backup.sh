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

# Checksummed by BARE FILENAME, not by path: sha256sum records whatever
# argument it was given, so an absolute path makes the .sha256 verifiable
# only on a machine where that exact path exists -- never after pulling
# the pair down from off-site, which is the one time it matters most.
( cd "$BACKUP_DIR" && sha256sum "$(basename "$ARCHIVE")" > "$(basename "$ARCHIVE").sha256" )

# Retention only ever runs after a dump that just succeeded, and never deletes
# the last archives standing. Backups failing silently -- disk full, auth
# change, mongodump missing -- used to be followed by retention quietly
# deleting everything that ever existed once it aged past the window.
gfs_prune_local "$BACKUP_DIR" 'flymanager_mongodb_*.archive.gz' "$BACKUP_MIN_KEEP"

if [ -n "$RCLONE_REMOTE" ]; then
    SLIM="$BACKUP_DIR/flymanager_labdata_${TIMESTAMP}.archive.gz"
    TMP_SLIM="${SLIM}.tmp"
    trap 'rm -f "$TMP_SLIM"' INT TERM EXIT
    # --db is mandatory alongside --excludeCollection, and --oplog is not
    # allowed with --db, so the slim archive is a single-database dump without
    # an oplog. That is acceptable here and only here: the full archive on the
    # NAS keeps the oplog for consistent local restores, and the off-site copy
    # is the disaster case, where a few seconds of skew matters far less than
    # having any copy at all.
    mongodump \
        --host "${MONGO_HOST}:${MONGO_PORT}" \
        --db="${MONGO_DB_NAME:-flymanager}" \
        --archive="$TMP_SLIM" \
        --gzip \
        --excludeCollection=flybase_phenotypes \
        --excludeCollection=flybase_allele_genes \
        --excludeCollection=genes2nd \
        --excludeCollection=genes3rd \
        --excludeCollection=genesX \
        --excludeCollection=genes4th
    mv "$TMP_SLIM" "$SLIM"
    trap - INT TERM EXIT
    ( cd "$BACKUP_DIR" && sha256sum "$(basename "$SLIM")" > "$(basename "$SLIM").sha256" )

    rclone copy "$SLIM" "${RCLONE_REMOTE}/mongo/"
    rclone copy "${SLIM}.sha256" "${RCLONE_REMOTE}/mongo/"
    gfs_prune_remote "${RCLONE_REMOTE}/mongo" 'flymanager_labdata_*.archive.gz' "$BACKUP_MIN_KEEP"

    # The slim copy exists to be uploaded; keeping it would just be a second
    # local retention problem, so it goes once it is off the machine.
    rm -f "$SLIM" "${SLIM}.sha256"
    echo "Off-site (lab data only): ${RCLONE_REMOTE}/mongo/flymanager_labdata_${TIMESTAMP}.archive.gz"
fi

# The app writes its daily state backup into $BACKUP_DIR/state on its own
# scheduler; this loop is what carries it off-site and applies the same GFS
# retention, so the app never needs rclone and the deletion rule stays in one
# place.
STATE_DIR="$BACKUP_DIR/state"
if [ -d "$STATE_DIR" ]; then
    gfs_prune_local "$STATE_DIR" 'flymanager_state_*.tar.gz' "$BACKUP_MIN_KEEP"
    gfs_prune_local "$STATE_DIR" 'flymanager_env_*.env.enc' "$BACKUP_MIN_KEEP"
    if [ -n "$RCLONE_REMOTE" ]; then
        rclone copy "$STATE_DIR" "${RCLONE_REMOTE}/state/" --include '*.tar.gz*' --include '*.env.enc*'
        gfs_prune_remote "${RCLONE_REMOTE}/state" 'flymanager_state_*.tar.gz' "$BACKUP_MIN_KEEP"
        gfs_prune_remote "${RCLONE_REMOTE}/state" 'flymanager_env_*.env.enc' "$BACKUP_MIN_KEEP"
        echo "Off-site (state): ${RCLONE_REMOTE}/state/"
    fi
fi

if [ -n "$HEALTHCHECK_UUID" ]; then
    curl -fsS -m 10 --retry 3 "https://hc-ping.com/${HEALTHCHECK_UUID}" > /dev/null || true
fi

echo "Backup complete: $ARCHIVE"
