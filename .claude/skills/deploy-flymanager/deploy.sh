#!/usr/bin/env bash
# Build FlyManager's Docker image, push it to Docker Hub, and redeploy the
# Synology/Portainer production stack — with a pre-deploy Mongo backup,
# post-deploy verification, and automatic rollback on failure.
# See SKILL.md in this directory.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$REPO_ROOT"

# Auto-load PORTAINER_URL / PORTAINER_API_KEY / PORTAINER_STACK_NAME from the
# repo's .env if they aren't already set in the environment. Tolerates the
# "KEY = value" / "KEY=\"value\"" formats this repo's .env actually uses.
load_env_defaults() {
  local env_file="${REPO_ROOT}/.env"
  [[ -f "$env_file" ]] || return 0
  local key value
  while IFS='=' read -r key value; do
    key="$(echo "$key" | tr -d '[:space:]')"
    case "$key" in
      PORTAINER_URL|PORTAINER_API_KEY|PORTAINER_STACK_NAME) ;;
      *) continue ;;
    esac
    value="$(echo "$value" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/")"
    if [[ -z "${!key:-}" ]]; then
      export "$key=$value"
    fi
  done < <(grep -E '^\s*(PORTAINER_URL|PORTAINER_API_KEY|PORTAINER_STACK_NAME)\s*=' "$env_file" || true)
}
load_env_defaults

IMAGE="${DOCKERHUB_IMAGE:-neurorishika/flymanager}"
PLATFORM="${DOCKER_PLATFORM:-linux/amd64}"
SHA="$(git rev-parse --short HEAD)"
TAG="${DEPLOY_TAG:-$SHA}"
NEW_IMAGE="${IMAGE}:${TAG}"

NAS_SSH_TARGET="${NAS_SSH_TARGET:-saraswati@saraswati.taild08eb9.ts.net}"
MONGODB_CONTAINER="${MONGODB_CONTAINER:-flymanager-mongodb}"
MONGO_BACKUP_CONTAINER="${MONGO_BACKUP_CONTAINER:-flymanager-mongo-backup}"
APP_CONTAINER="${APP_CONTAINER:-flymanager-app}"
WORKER_CONTAINER="${WORKER_CONTAINER:-flymanager-worker}"
MONGO_DB_NAME="${MONGO_DB_NAME:-flymanager}"

PORTAINER_STACK_NAME="${PORTAINER_STACK_NAME:-flymanager}"

STATE_DIR="${REPO_ROOT}/.claude/skills/deploy-flymanager/.state"
STATE_FILE="${STATE_DIR}/last-deploy.json"

ssh_nas() {
  ssh -o BatchMode=yes -o ConnectTimeout=10 "$NAS_SSH_TARGET" "$@"
}

cmd="${1:-all}"

# ---------------------------------------------------------------------------
# build / push
# ---------------------------------------------------------------------------

build() {
  echo "==> Building ${NEW_IMAGE} (+ :latest) for ${PLATFORM}"
  docker buildx build \
    --platform "$PLATFORM" \
    --build-arg "APP_VERSION=${TAG}" \
    --load \
    -t "$NEW_IMAGE" \
    -t "${IMAGE}:latest" \
    .
}

push() {
  echo "==> Pushing ${NEW_IMAGE}"
  docker push "$NEW_IMAGE"
  echo "==> Pushing ${IMAGE}:latest"
  docker push "${IMAGE}:latest"
}

# ---------------------------------------------------------------------------
# Mongo backup (on-demand, via the stack's own backup container/script)
# ---------------------------------------------------------------------------

backup() {
  mkdir -p "$STATE_DIR"

  echo "==> Triggering on-demand Mongo backup on the NAS"
  local backup_output archive_path
  backup_output="$(ssh_nas "/usr/local/bin/docker exec ${MONGO_BACKUP_CONTAINER} /usr/local/bin/container-mongo-backup.sh")"
  echo "$backup_output" | tail -5
  archive_path="$(echo "$backup_output" | sed -n 's/^Backup complete: //p' | tail -1)"
  if [[ -z "$archive_path" ]]; then
    echo "ERROR: could not determine backup archive path from backup script output." >&2
    exit 1
  fi
  echo "==> Backup archive: ${archive_path}"

  echo "==> Recording pre-deploy collection counts"
  local tmpdir counts_file
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' RETURN
  counts_file="${tmpdir}/pre_counts.json"
  ssh_nas "/usr/local/bin/docker exec ${MONGODB_CONTAINER} mongosh --quiet --eval '
    const out = {};
    db.getSiblingDB(\"${MONGO_DB_NAME}\").getCollectionNames().forEach(function(c) {
      out[c] = db.getSiblingDB(\"${MONGO_DB_NAME}\").getCollection(c).countDocuments();
    });
    print(JSON.stringify(out));
  '" > "$counts_file"

  ARCHIVE_PATH="$archive_path" COUNTS_FILE="$counts_file" STATE_FILE="$STATE_FILE" python3 -c "
import json, os
state = {
    'backup_archive': os.environ['ARCHIVE_PATH'],
    'pre_counts': json.load(open(os.environ['COUNTS_FILE'])),
}
json.dump(state, open(os.environ['STATE_FILE'], 'w'), indent=2)
"
  echo "==> Pre-deploy state recorded at ${STATE_FILE}"
}

# ---------------------------------------------------------------------------
# Portainer API helpers
# ---------------------------------------------------------------------------

require_portainer_creds() {
  if [[ -z "${PORTAINER_URL:-}" || -z "${PORTAINER_API_KEY:-}" ]]; then
    echo "ERROR: PORTAINER_URL and PORTAINER_API_KEY must both be set." >&2
    exit 1
  fi
}

# Sets STACK_ID / ENDPOINT_ID globals.
resolve_stack() {
  require_portainer_creds
  local base="${PORTAINER_URL%/}"
  local tmpdir="$1"
  curl -fsS -H "X-API-Key: ${PORTAINER_API_KEY}" "${base}/api/stacks" > "${tmpdir}/stacks.json"
  STACK_ID="$(PORTAINER_STACK_NAME="$PORTAINER_STACK_NAME" python3 -c "
import json, os
stacks = json.load(open('${tmpdir}/stacks.json'))
match = [s for s in stacks if s['Name'] == os.environ['PORTAINER_STACK_NAME']]
print(match[0]['Id'] if match else '')
")"
  ENDPOINT_ID="$(PORTAINER_STACK_NAME="$PORTAINER_STACK_NAME" python3 -c "
import json, os
stacks = json.load(open('${tmpdir}/stacks.json'))
match = [s for s in stacks if s['Name'] == os.environ['PORTAINER_STACK_NAME']]
print(match[0]['EndpointId'] if match else '')
")"
  if [[ -z "$STACK_ID" ]]; then
    echo "ERROR: no stack named '${PORTAINER_STACK_NAME}' found via the Portainer API. Set PORTAINER_STACK_NAME if it's named differently." >&2
    exit 1
  fi
}

# Sets APP_IMAGE_PREVIOUS. Writes ${tmpdir}/redeploy_payload.json.
# $1 = tmpdir, $2 = new image ref (e.g. neurorishika/flymanager:abc1234)
build_redeploy_payload() {
  local tmpdir="$1"
  local new_image="$2"
  local base="${PORTAINER_URL%/}"

  curl -fsS -H "X-API-Key: ${PORTAINER_API_KEY}" "${base}/api/stacks/${STACK_ID}" > "${tmpdir}/stack_detail.json"
  curl -fsS -H "X-API-Key: ${PORTAINER_API_KEY}" "${base}/api/stacks/${STACK_ID}/file" > "${tmpdir}/stack_file.json"

  APP_IMAGE_PREVIOUS="$(NEW_IMAGE="$new_image" python3 -c "
import json, os
detail = json.load(open('${tmpdir}/stack_detail.json'))
file_content = json.load(open('${tmpdir}/stack_file.json'))['StackFileContent']
env = detail.get('Env', [])
previous = ''
found = False
for entry in env:
    if entry.get('name') == 'APP_IMAGE':
        previous = entry.get('value', '')
        entry['value'] = os.environ['NEW_IMAGE']
        found = True
if not found:
    env.append({'name': 'APP_IMAGE', 'value': os.environ['NEW_IMAGE']})
payload = {
    'stackFileContent': file_content,
    'env': env,
    'prune': False,
    'pullImage': True,
}
json.dump(payload, open('${tmpdir}/redeploy_payload.json', 'w'))
print(previous)
")"
}

# Pre-pulls an image directly on the NAS over SSH, outside of Portainer's
# request/response cycle. This is the fix for a real incident: Portainer's
# PUT /api/stacks/{id} with pullImage:true pulls synchronously as part of
# handling the HTTP request, and the DSM reverse proxy in front of
# Portainer (PORTAINER_URL) times out (504) well before a slow Docker Hub
# pull finishes — even though Portainer keeps working server-side and the
# deploy eventually succeeds anyway. Pre-pulling here means Portainer's own
# pull is a fast no-op ("Image is up to date"), so the PUT call finishes
# well inside the proxy's timeout instead of racing it.
pre_pull_image() {
  local image="$1"
  echo "==> Pre-pulling ${image} on the NAS (avoids racing the Portainer reverse-proxy timeout)"
  ssh_nas "/usr/local/bin/docker pull '${image}'"
}

put_stack() {
  local tmpdir="$1"
  local base="${PORTAINER_URL%/}"
  # --max-time is a safety margin, not the fix — pre_pull_image is the fix.
  # A 504 here is treated as "uncertain", not "failed": the caller confirms
  # via confirm_image_applied instead of trusting this exit code alone,
  # because Portainer has been observed to complete the redeploy server-side
  # even when the client-facing request times out.
  curl -sS --max-time 240 -X PUT \
    -H "X-API-Key: ${PORTAINER_API_KEY}" \
    -H "Content-Type: application/json" \
    --data "@${tmpdir}/redeploy_payload.json" \
    -o /dev/null -w '%{http_code}' \
    "${base}/api/stacks/${STACK_ID}?endpointId=${ENDPOINT_ID}"
}

# Polls the NAS directly for up to $2 seconds to confirm $APP_CONTAINER is
# actually running $1 — the ground truth, independent of what the Portainer
# API call returned. Echoes "confirmed" or "timeout".
confirm_image_applied() {
  local expected_image="$1"
  local timeout_seconds="${2:-120}"
  local waited=0
  while [[ $waited -lt $timeout_seconds ]]; do
    local actual
    actual="$(ssh_nas "/usr/local/bin/docker inspect ${APP_CONTAINER} --format '{{.Config.Image}}'" 2>/dev/null || true)"
    if [[ "$actual" == "$expected_image" ]]; then
      echo "confirmed"
      return 0
    fi
    sleep 10
    waited=$((waited + 10))
  done
  echo "timeout"
  return 1
}

redeploy() {
  if [[ -z "${PORTAINER_URL:-}" || -z "${PORTAINER_API_KEY:-}" ]]; then
    echo "ERROR: PORTAINER_URL and/or PORTAINER_API_KEY not set — cannot redeploy." >&2
    echo "    (checked the environment and ${REPO_ROOT}/.env — neither had both set)" >&2
    echo "    export PORTAINER_URL=\"https://your-nas:9443\"" >&2
    echo "    export PORTAINER_API_KEY=\"<personal access token>\"  # Portainer -> your user -> Access tokens" >&2
    echo "    then re-run: ./.claude/skills/deploy-flymanager/deploy.sh redeploy" >&2
    echo "    Or redeploy manually: Portainer -> Stacks -> ${PORTAINER_STACK_NAME} -> Pull and redeploy." >&2
    exit 1
  fi

  local tmpdir
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' RETURN

  echo "==> Looking up stack '${PORTAINER_STACK_NAME}'"
  resolve_stack "$tmpdir"

  echo "==> Fetching current stack file + env (stack id ${STACK_ID}, endpoint ${ENDPOINT_ID})"
  build_redeploy_payload "$tmpdir" "$NEW_IMAGE"

  if [[ -z "$APP_IMAGE_PREVIOUS" ]]; then
    echo "WARNING: stack had no existing APP_IMAGE env var — nothing to roll back to if this deploy fails."
  else
    echo "==> Previous APP_IMAGE: ${APP_IMAGE_PREVIOUS}"
    mkdir -p "$STATE_DIR"
    python3 -c "
import json
path = '${STATE_FILE}'
try:
    state = json.load(open(path))
except (FileNotFoundError, json.JSONDecodeError):
    state = {}
state['previous_app_image'] = '${APP_IMAGE_PREVIOUS}'
state['new_app_image'] = '${NEW_IMAGE}'
json.dump(state, open(path, 'w'), indent=2)
"
  fi

  pre_pull_image "$NEW_IMAGE"

  echo "==> Setting APP_IMAGE=${NEW_IMAGE} and redeploying (pullImage=true)"
  local http_code
  http_code="$(put_stack "$tmpdir")"

  if [[ "$http_code" == 2* ]]; then
    echo "==> Redeploy request accepted (HTTP ${http_code})."
  else
    echo "WARNING: Portainer API returned HTTP ${http_code} (may be a reverse-proxy timeout, not a real failure)."
    echo "==> Confirming actual state on the NAS directly..."
    if [[ "$(confirm_image_applied "$NEW_IMAGE" 180)" != "confirmed" ]]; then
      echo "ERROR: ${APP_CONTAINER} is still not running ${NEW_IMAGE} after waiting — redeploy genuinely failed." >&2
      exit 1
    fi
    echo "==> Confirmed: ${APP_CONTAINER} is running ${NEW_IMAGE} despite the non-2xx response — redeploy succeeded."
  fi
  echo "==> Redeploy triggered."
}

# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

# Sets VERIFY_FAILURE_KIND to one of: "" (success), "deploy" (health/image —
# no data was touched, an image-only rollback suffices), "data" (collection
# counts regressed — needs a full data + image rollback).
verify() {
  VERIFY_FAILURE_KIND=""

  echo "==> Waiting for /health/ready"
  local attempt=0 healthy=0
  while [[ $attempt -lt 24 ]]; do
    if ssh_nas "curl -fsS http://127.0.0.1:12754/health/ready" >/dev/null 2>&1; then
      healthy=1
      break
    fi
    attempt=$((attempt + 1))
    sleep 5
  done
  if [[ $healthy -ne 1 ]]; then
    echo "FAIL: app did not report healthy at /health/ready within 120s."
    VERIFY_FAILURE_KIND="deploy"
    return 1
  fi
  echo "==> App is healthy."

  local deployed_image
  deployed_image="$(ssh_nas "/usr/local/bin/docker inspect ${APP_CONTAINER} --format '{{.Config.Image}}'")"
  if [[ "$deployed_image" != "$NEW_IMAGE" ]]; then
    echo "FAIL: ${APP_CONTAINER} is running '${deployed_image}', expected '${NEW_IMAGE}'."
    VERIFY_FAILURE_KIND="deploy"
    return 1
  fi
  echo "==> ${APP_CONTAINER} is running the expected image (${NEW_IMAGE})."

  if [[ ! -f "$STATE_FILE" ]]; then
    echo "WARNING: no pre-deploy state file found — skipping data-equivalence check."
    return 0
  fi

  echo "==> Comparing collection counts against pre-deploy backup"
  local tmpdir post_counts_file
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' RETURN
  post_counts_file="${tmpdir}/post_counts.json"
  ssh_nas "/usr/local/bin/docker exec ${MONGODB_CONTAINER} mongosh --quiet --eval '
    const out = {};
    db.getSiblingDB(\"${MONGO_DB_NAME}\").getCollectionNames().forEach(function(c) {
      out[c] = db.getSiblingDB(\"${MONGO_DB_NAME}\").getCollection(c).countDocuments();
    });
    print(JSON.stringify(out));
  '" > "$post_counts_file"

  if ! STATE_FILE="$STATE_FILE" POST_COUNTS_FILE="$post_counts_file" python3 -c "
import json, os, sys
state = json.load(open(os.environ['STATE_FILE']))
pre = state.get('pre_counts', {})
post = json.load(open(os.environ['POST_COUNTS_FILE']))
regressions = {k: (v, post.get(k)) for k, v in pre.items() if post.get(k, 0) < v}
if regressions:
    print('FAIL: collection count(s) decreased since the pre-deploy backup:')
    for name, (before, after) in regressions.items():
        print(f'  {name}: {before} -> {after}')
    sys.exit(1)
print('OK: no collection lost documents relative to the pre-deploy backup.')
"; then
    VERIFY_FAILURE_KIND="data"
    return 1
  fi
}

# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------

# Reverts Portainer's APP_IMAGE to the pre-deploy value. Cheap (no data
# touched) — safe to run for any verify failure, including ones where the
# new image just failed its health check and no data was ever at risk.
rollback_image() {
  if [[ ! -f "$STATE_FILE" ]]; then
    echo "ERROR: no state file at ${STATE_FILE} — nothing to roll back to. Manual intervention required." >&2
    exit 1
  fi
  local previous_app_image
  previous_app_image="$(python3 -c "import json; print(json.load(open('${STATE_FILE}')).get('previous_app_image',''))")"

  if [[ -z "$previous_app_image" ]]; then
    echo "WARNING: no previous_app_image recorded — skipping image rollback."
    return 0
  fi

  echo "==> Reverting APP_IMAGE to ${previous_app_image}"
  local tmpdir
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' RETURN
  resolve_stack "$tmpdir"
  build_redeploy_payload "$tmpdir" "$previous_app_image"
  pre_pull_image "$previous_app_image"
  local http_code
  http_code="$(put_stack "$tmpdir")"
  if [[ "$http_code" == 2* ]]; then
    echo "==> Revert request accepted (HTTP ${http_code})."
  else
    echo "WARNING: Portainer API returned HTTP ${http_code} (may be a reverse-proxy timeout, not a real failure)."
    echo "==> Confirming actual state on the NAS directly..."
    if [[ "$(confirm_image_applied "$previous_app_image" 180)" != "confirmed" ]]; then
      echo "ERROR: ${APP_CONTAINER} is still not running ${previous_app_image} after waiting — image rollback genuinely failed." >&2
      exit 1
    fi
    echo "==> Confirmed: ${APP_CONTAINER} is running ${previous_app_image} despite the non-2xx response."
  fi
  echo "==> Stack reverted to ${previous_app_image}."
}

# Restores MongoDB from the pre-deploy backup archive. Expensive (stops the
# app, replays the whole dump) — only run this when the data-equivalence
# check in verify() actually found a regression, not for a plain
# health-check/image-mismatch failure where no data was touched.
rollback_data() {
  if [[ ! -f "$STATE_FILE" ]]; then
    echo "ERROR: no state file at ${STATE_FILE} — nothing to roll back to. Manual intervention required." >&2
    exit 1
  fi
  local backup_archive
  backup_archive="$(python3 -c "import json; print(json.load(open('${STATE_FILE}')).get('backup_archive',''))")"

  if [[ -z "$backup_archive" ]]; then
    echo "WARNING: no backup_archive recorded — skipping Mongo restore."
    return 0
  fi

  echo "==> Restoring MongoDB from ${backup_archive}"
  ssh_nas "/usr/local/bin/docker stop ${APP_CONTAINER} ${WORKER_CONTAINER}"
  # w:1 instead of the driver default (majority) — safe here because the app
  # is stopped (no live readers to protect) and this is a full --drop
  # restore, not incremental writes; majority ack per batch was the main
  # cost observed in practice (~1600 docs/sec at majority vs much faster
  # at w:1) and buys nothing while the app can't see the data anyway.
  ssh_nas "/usr/local/bin/docker exec ${MONGO_BACKUP_CONTAINER} cat '${backup_archive}' | /usr/local/bin/docker exec -i ${MONGODB_CONTAINER} mongorestore --archive --gzip --drop --writeConcern '{\"w\":1}'"
  ssh_nas "/usr/local/bin/docker start ${APP_CONTAINER} ${WORKER_CONTAINER}"
  echo "==> MongoDB restored."
}

# Full rollback: data + image. Used by the standalone `rollback` command
# (a human asking to roll back wants everything reverted) and by `all` when
# verify's data-equivalence check specifically failed.
rollback() {
  echo "==> ROLLING BACK (data + image)"
  rollback_data
  rollback_image
  echo "==> Rollback complete. Verify manually before trusting the stack again."
}

# ---------------------------------------------------------------------------

case "$cmd" in
  build) build ;;
  push) push ;;
  backup) backup ;;
  redeploy) redeploy ;;
  verify) verify ;;
  rollback) rollback ;;
  rollback-image) rollback_image ;;
  rollback-data) rollback_data ;;
  all)
    backup
    build
    push
    redeploy
    echo "==> Waiting a moment for containers to recreate before verifying..."
    sleep 10
    if verify; then
      echo
      echo "==> Deploy succeeded: ${NEW_IMAGE}"
    else
      echo
      case "${VERIFY_FAILURE_KIND:-}" in
        data)
          echo "==> Data-equivalence check failed — rolling back data AND image."
          rollback_data
          rollback_image
          ;;
        deploy)
          echo "==> Health/image check failed — no data was touched, rolling back image only."
          rollback_image
          ;;
        *)
          echo "==> Verification failed (unknown reason) — rolling back data AND image to be safe."
          rollback_data
          rollback_image
          ;;
      esac
      exit 1
    fi
    ;;
  *)
    echo "usage: $0 [build|push|backup|redeploy|verify|rollback|rollback-image|rollback-data|all]" >&2
    exit 1
    ;;
esac
