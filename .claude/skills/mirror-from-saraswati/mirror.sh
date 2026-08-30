#!/usr/bin/env bash
# Mirror production FlyManager state (MongoDB + runtime files) from the
# Synology NAS ("saraswati") into the local Docker Compose stack.
#
# Read-only on the NAS: it streams a fresh `mongodump` straight out of the
# production mongo container and rsyncs runtime directories. It never
# triggers the production backup script, never writes to the NAS, and never
# touches the Portainer stack.
#
# DESTRUCTIVE LOCALLY: `restore` drops the local `flymanager` database.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/../../.." && pwd)"
cd "$ROOT_DIR"

NAS_SSH_TARGET="${NAS_SSH_TARGET:-saraswati@saraswati.taild08eb9.ts.net}"
NAS_DOCKER="${NAS_DOCKER:-/usr/local/bin/docker}"
NAS_ROOT="${NAS_ROOT:-/volume1/docker/flymanager}"
MONGODB_CONTAINER="${MONGODB_CONTAINER:-flymanager-mongodb}"
DB_NAME="${MONGO_DB_NAME:-flymanager}"
COMPOSE_FILE="${COMPOSE_FILE:-compose.yaml}"
DUMP_DIR="${DUMP_DIR:-backups/mongodb}"
KEEP_DUMPS="${KEEP_DUMPS:-5}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-180}"

ASSUME_YES="${MIRROR_YES:-0}"
WITH_REFERENCE=0
ARCHIVE_OVERRIDE=""

log() { printf '==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

ssh_nas() { ssh -o BatchMode=yes -o ConnectTimeout=15 "$NAS_SSH_TARGET" "$@"; }

# ---------------------------------------------------------------- dump ----
# Streams a gzipped archive of ONLY the app database. Scoping to --db is
# deliberate: an unscoped dump carries `admin`/`config`, and restoring those
# over the local stack would clobber its replica-set configuration.
dump() {
  mkdir -p "$DUMP_DIR"
  local ts archive
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  archive="$DUMP_DIR/saraswati_${DB_NAME}_${ts}.archive.gz"

  log "Dumping '${DB_NAME}' from ${MONGODB_CONTAINER} on ${NAS_SSH_TARGET}"
  if ! ssh_nas "${NAS_DOCKER} exec ${MONGODB_CONTAINER} mongodump --db ${DB_NAME} --archive --gzip" > "${archive}.tmp"; then
    rm -f "${archive}.tmp"
    die "mongodump failed on the NAS."
  fi
  [[ -s "${archive}.tmp" ]] || { rm -f "${archive}.tmp"; die "mongodump produced an empty archive."; }
  mv "${archive}.tmp" "$archive"
  log "Wrote ${archive} ($(du -h "$archive" | cut -f1))"

  # Keep only the newest $KEEP_DUMPS archives.
  local old
  old="$(ls -1t "$DUMP_DIR"/saraswati_"${DB_NAME}"_*.archive.gz 2>/dev/null | tail -n +$((KEEP_DUMPS + 1)) || true)"
  if [[ -n "$old" ]]; then
    log "Pruning $(echo "$old" | wc -l | tr -d ' ') old archive(s)"
    echo "$old" | xargs rm -f
  fi
  echo "$archive"
}

latest_archive() {
  if [[ -n "$ARCHIVE_OVERRIDE" ]]; then
    [[ -f "$ARCHIVE_OVERRIDE" ]] || die "Archive not found: $ARCHIVE_OVERRIDE"
    echo "$ARCHIVE_OVERRIDE"
    return
  fi
  local a
  a="$(ls -1t "$DUMP_DIR"/saraswati_"${DB_NAME}"_*.archive.gz 2>/dev/null | head -1 || true)"
  [[ -n "$a" ]] || die "No archive in ${DUMP_DIR}. Run '$0 dump' first."
  echo "$a"
}

# --------------------------------------------------------------- files ----
# rsync WITHOUT --delete on purpose: local data/ holds directories that do not
# exist on the NAS (phenotype_images/, markers/, client.key). Deleting to match
# the NAS would destroy them.
files() {
  local pairs=(
    "$NAS_ROOT/data/uploads/|data/uploads/"
    "$NAS_ROOT/data/backup/|data/backup/"
    "$NAS_ROOT/data/job_exports/|data/job_exports/"
    "$NAS_ROOT/generated_labels/|flymanager/app/static/generated_labels/"
  )
  if [[ "$WITH_REFERENCE" -eq 1 ]]; then
    # ~311 MB of static reference data; skipped unless asked for.
    pairs+=(
      "$NAS_ROOT/data/flybase/|data/flybase/"
      "$NAS_ROOT/data/bloomington.csv|data/bloomington.csv"
    )
  fi

  local pair src dest
  for pair in "${pairs[@]}"; do
    src="${pair%%|*}"; dest="${pair##*|}"
    mkdir -p "$(dirname "${dest%/}")"
    [[ "$dest" == */ ]] && mkdir -p "$dest"
    log "rsync ${src} -> ${dest}"
    rsync -az --info=stats1 "${NAS_SSH_TARGET}:${src}" "$dest"
  done
}

# ------------------------------------------------------------------ up ----
# Preflight: compose.yaml hardcodes `container_name: flymanager-*`, so only one
# checkout (repo root or any .claude/worktrees/* worktree) can run the stack at
# a time. Bringing ours up while another owns those names fails with an opaque
# "container name is already in use" conflict, so detect it and say what to do.
check_stack_owner() {
  local owner
  owner="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$MONGODB_CONTAINER" 2>/dev/null || true)"
  [[ -z "$owner" || "$owner" == "$ROOT_DIR" ]] && return 0

  local project
  project="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$MONGODB_CONTAINER" 2>/dev/null || true)"
  cat >&2 <<EOF
ERROR: the flymanager-* containers are owned by a different checkout.

  running project: ${project:-<unknown>}
  its directory:   ${owner}
  this directory:  ${ROOT_DIR}

compose.yaml uses fixed container names, so both cannot run at once. Either:

  * mirror into that stack instead:
      cd "${owner}" && "${SCRIPT_DIR}/mirror.sh" restore
  * or take it down first (this stops whatever work it is running):
      docker compose --project-directory "${owner}" -f "${owner}/compose.yaml" down
EOF
  exit 1
}

up() {
  check_stack_owner
  log "Bringing up the local stack (${COMPOSE_FILE}, with rebuild)"
  docker compose -f "$COMPOSE_FILE" up -d --build

  log "Waiting for ${MONGODB_CONTAINER} to become healthy (timeout ${WAIT_TIMEOUT}s)"
  local waited=0 status
  while :; do
    status="$(docker inspect -f '{{.State.Health.Status}}' "$MONGODB_CONTAINER" 2>/dev/null || echo missing)"
    [[ "$status" == "healthy" ]] && break
    (( waited >= WAIT_TIMEOUT )) && die "${MONGODB_CONTAINER} not healthy after ${WAIT_TIMEOUT}s (status: ${status})."
    sleep 3; waited=$((waited + 3))
  done

  log "Waiting for the replica set to elect a primary"
  waited=0
  while :; do
    if docker exec "$MONGODB_CONTAINER" mongosh --quiet --eval 'db.hello().isWritablePrimary' 2>/dev/null | grep -q true; then
      break
    fi
    (( waited >= WAIT_TIMEOUT )) && die "No primary elected after ${WAIT_TIMEOUT}s."
    sleep 3; waited=$((waited + 3))
  done
  log "Local stack is up and writable"
}

# ------------------------------------------------------------- restore ----
restore() {
  local archive; archive="$(latest_archive)"

  if [[ "$ASSUME_YES" != "1" ]]; then
    cat <<EOF

  This DROPS the local '${DB_NAME}' database and replaces it with
  production data from ${NAS_SSH_TARGET}.

    archive: ${archive}
    target:  ${MONGODB_CONTAINER} (local ${COMPOSE_FILE} stack)

EOF
    if [[ -t 0 ]]; then
      read -r -p "  Type 'yes' to continue: " reply
      [[ "$reply" == "yes" ]] || die "Aborted."
    else
      die "Refusing to drop the local database non-interactively. Re-run with --yes."
    fi
  fi

  log "Dropping local database '${DB_NAME}'"
  docker exec "$MONGODB_CONTAINER" mongosh --quiet --eval "db.getSiblingDB('${DB_NAME}').dropDatabase()" >/dev/null

  log "Restoring from ${archive}"
  # w:1, not majority: the local set has 2 members and one may lag on startup.
  docker exec -i "$MONGODB_CONTAINER" mongorestore \
    --archive --gzip --drop \
    --nsInclude "${DB_NAME}.*" \
    --writeConcern '{"w":1}' < "$archive"

  log "Collection counts after restore"
  docker exec "$MONGODB_CONTAINER" mongosh --quiet --eval "
    const db2 = db.getSiblingDB('${DB_NAME}');
    db2.getCollectionNames().sort().forEach(c => print('    ' + c + ': ' + db2[c].countDocuments()));
  "

  log "Restarting app and worker so they pick up the new data"
  docker compose -f "$COMPOSE_FILE" restart app worker >/dev/null
}

usage() {
  cat >&2 <<EOF
usage: $0 [options] [dump|files|up|restore|all]

  dump      Stream a fresh mongodump of '${DB_NAME}' from the NAS into ${DUMP_DIR}/
  files     rsync runtime files (uploads, backup, job_exports, generated_labels)
  up        docker compose up -d --build, wait for mongo healthy + primary
  restore   Drop the local '${DB_NAME}' DB and restore the newest archive
  all       dump -> files -> up -> restore  (default)

options:
  --yes                 Skip the destructive-restore confirmation
  --with-reference      Also rsync data/flybase + bloomington.csv (~311 MB)
  --archive PATH        Restore this archive instead of the newest one
EOF
  exit 2
}

CMD=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes|-y) ASSUME_YES=1 ;;
    --with-reference) WITH_REFERENCE=1 ;;
    --archive) ARCHIVE_OVERRIDE="${2:?--archive needs a path}"; shift ;;
    -h|--help) usage ;;
    -*) die "Unknown option: $1" ;;
    *) [[ -n "$CMD" ]] && die "Only one command at a time."; CMD="$1" ;;
  esac
  shift
done

case "${CMD:-all}" in
  dump)    dump >/dev/null ;;
  files)   files ;;
  up)      up ;;
  restore) restore ;;
  all)     dump >/dev/null; files; up; restore; log "Mirror complete." ;;
  *)       usage ;;
esac
