#!/bin/bash
# Driver for running and interacting with FlyManager locally via Docker Compose.
#
# The repo's tracked .env has a stale MongoDB Atlas URI left over from an
# earlier setup. `docker compose` auto-loads .env for variable substitution,
# so every command below explicitly overrides MONGO_URI/MONGO_DB_NAME to
# point at the local compose-managed replica set instead of trusting .env.
#
# Usage:
#   ./driver.sh up          Build and start the full stack (idempotent)
#   ./driver.sh bootstrap   Create a known-password test admin user
#   ./driver.sh verify      Log in via curl and check authenticated pages
#   ./driver.sh screenshot  Headless-Chrome screenshot of the login page
#   ./driver.sh status      docker compose ps
#   ./driver.sh logs <svc>  docker compose logs -f <service>
#   ./driver.sh down        Stop the stack (keeps data volumes)
#   ./driver.sh dev-fixtures  Idempotently apply DB state css_audit.py's
#                              PAGES list depends on (see cmd_dev_fixtures)

set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"

export MONGO_URI="${MONGO_URI:-mongodb://mongodb:27017,mongodb2:27017/?replicaSet=rs0}"
export MONGO_DB_NAME="${MONGO_DB_NAME:-flymanager}"

APP_URL="http://127.0.0.1:5234"
COOKIE_JAR="/tmp/flymanager-driver-cookies.txt"
TEST_ADMIN_USERNAME="${TEST_ADMIN_USERNAME:-devtest}"
TEST_ADMIN_PASSWORD="${TEST_ADMIN_PASSWORD:-DevTestPassw0rd}"
TEST_ADMIN_INITIALS="${TEST_ADMIN_INITIALS:-DT}"

cmd_up() {
    docker compose -f compose.yaml up -d --build
    echo "Waiting for app health..."
    for _ in $(seq 1 60); do
        if curl -sf "$APP_URL/health/ready" > /dev/null 2>&1; then
            echo "App is ready."
            docker compose -f compose.yaml ps --format "table {{.Name}}\t{{.Status}}"
            return 0
        fi
        sleep 2
    done
    echo "App did not become ready in time. Logs:" >&2
    docker compose -f compose.yaml logs app --tail 50 >&2
    exit 1
}

cmd_bootstrap() {
    # docker compose up's own entrypoint-triggered bootstrap can silently
    # no-op if FLYMANAGER_ADMIN_* wasn't in the app container's env at
    # creation time - invoking it directly here is the reliable path,
    # verified to actually create the user (unlike relying on `up`'s
    # implicit env-var passthrough, which was flaky in practice).
    docker compose -f compose.yaml exec \
        -e FLYMANAGER_ADMIN_USERNAME="$TEST_ADMIN_USERNAME" \
        -e FLYMANAGER_ADMIN_PASSWORD="$TEST_ADMIN_PASSWORD" \
        -e FLYMANAGER_ADMIN_INITIALS="$TEST_ADMIN_INITIALS" \
        app python -m flymanager.app.bootstrap
}

cmd_verify() {
    rm -f "$COOKIE_JAR"

    echo "--- GET /auth/login ---"
    curl -sf -c "$COOKIE_JAR" "$APP_URL/auth/login" -o /tmp/flymanager-login.html
    CSRF=$(grep -o 'name="csrf-token" content="[^"]*"' /tmp/flymanager-login.html | sed -E 's/.*content="([^"]*)"/\1/')
    if [ -z "$CSRF" ]; then
        echo "FAILED: could not extract CSRF token from login page" >&2
        exit 1
    fi

    echo "--- POST /auth/login ---"
    HTTP_CODE=$(curl -s -b "$COOKIE_JAR" -c "$COOKIE_JAR" -L \
        --data-urlencode "username=$TEST_ADMIN_USERNAME" \
        --data-urlencode "password=$TEST_ADMIN_PASSWORD" \
        --data-urlencode "csrf_token=$CSRF" \
        "$APP_URL/auth/login" -o /tmp/flymanager-post-login.html -w "%{http_code}")
    if [ "$HTTP_CODE" != "200" ] || ! grep -qi "$TEST_ADMIN_USERNAME" /tmp/flymanager-post-login.html; then
        echo "FAILED: login did not succeed (http_code=$HTTP_CODE)" >&2
        exit 1
    fi
    echo "Login OK (http_code=$HTTP_CODE)"

    echo "--- GET /home (authenticated) ---"
    HTTP_CODE=$(curl -s -b "$COOKIE_JAR" "$APP_URL/home" -o /tmp/flymanager-home.html -w "%{http_code}")
    [ "$HTTP_CODE" = "200" ] || { echo "FAILED: /home returned $HTTP_CODE" >&2; exit 1; }
    echo "/home OK"

    echo "--- GET /stock/explorer (real seeded data) ---"
    HTTP_CODE=$(curl -s -b "$COOKIE_JAR" "$APP_URL/stock/explorer" -o /tmp/flymanager-stocks.html -w "%{http_code}")
    [ "$HTTP_CODE" = "200" ] || { echo "FAILED: /stock/explorer returned $HTTP_CODE" >&2; exit 1; }
    echo "/stock/explorer OK"

    echo "--- GET /jobs/status.json (job-queue API) ---"
    HTTP_CODE=$(curl -s -b "$COOKIE_JAR" "$APP_URL/jobs/status.json" -o /tmp/flymanager-jobs.json -w "%{http_code}")
    [ "$HTTP_CODE" = "200" ] || { echo "FAILED: /jobs/status.json returned $HTTP_CODE" >&2; exit 1; }
    echo "/jobs/status.json OK: $(cat /tmp/flymanager-jobs.json)"

    echo "All checks passed."
}

cmd_screenshot() {
    mkdir -p "$SCRIPT_DIR/shots"
    CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if [ ! -x "$CHROME" ]; then
        # Linux fallback (untested in this environment - this repo's
        # driver was built and verified on macOS with Docker Desktop).
        CHROME="$(command -v google-chrome-stable || command -v chromium || true)"
    fi
    if [ -z "$CHROME" ] || [ ! -x "$CHROME" ]; then
        echo "No Chrome/Chromium binary found - skipping screenshot." >&2
        exit 1
    fi
    OUT="${1:-$SCRIPT_DIR/shots/login.png}"
    "$CHROME" --headless=new --disable-gpu --no-sandbox \
        --screenshot="$OUT" --window-size=1400,1000 "$APP_URL/auth/login"
    echo "Screenshot written to $OUT"
}

cmd_dev_fixtures() {
    # css_audit.py's PAGES list includes routes that only render real content
    # (rather than a redirect) if specific dev-seed records are reachable by
    # the `devtest` account. There is no tracked seed/fixture script for the
    # dev dataset (mongo-init in compose.yaml only initializes the replica
    # set) - the dataset itself lives purely in the persistent Mongo volume.
    # This command is the one place that DB-state dependency is recorded and
    # kept idempotent (safe to re-run, always converges to the same state):
    #
    #   - view-cross (/cross/view_cross/18d73b2cc8): get_accessible_cross()
    #     only returns a document when User == devtest or
    #     AssignedTo == devtest. No seed cross is owned by devtest, so this
    #     sets AssignedTo on that one cross. Without it, the route 404s
    #     internally and get_accessible_cross's "not found" branch silently
    #     redirects to /cross/cross_explorer - a real page, so status-200 +
    #     ".header" checks both pass and css_audit.py "succeeds" while
    #     screenshotting the wrong page.
    #
    # Run this after `up`/`bootstrap` and before css_audit.py capture/tapcheck
    # if the Mongo volume is ever reseeded/recreated from scratch.
    #
    # (view-stock and phenotype-preview were checked for the same latent
    # dependency and don't have one: stock 0034051b64 already has
    # User: "devtest" in the seed data, and phenotype_preview's GET route
    # renders an empty form with no record lookup at all.)
    docker exec flymanager-mongodb mongosh --quiet flymanager --eval '
        const res = db.crosses.updateOne(
            { UniqueID: "18d73b2cc8" },
            { $set: { AssignedTo: "devtest" } }
        );
        if (res.matchedCount !== 1) {
            print("FAILED: expected to match 1 cross with UniqueID 18d73b2cc8, matched " + res.matchedCount);
            quit(1);
        }
        print("dev-fixtures OK: cross 18d73b2cc8 AssignedTo=devtest (matched=" + res.matchedCount + ", modified=" + res.modifiedCount + ")");
    '
}

cmd_status() {
    docker compose -f compose.yaml ps --format "table {{.Name}}\t{{.Status}}"
}

cmd_logs() {
    docker compose -f compose.yaml logs -f "${1:?service name required}"
}

cmd_down() {
    docker compose -f compose.yaml down
}

case "${1:-}" in
    up) cmd_up ;;
    bootstrap) cmd_bootstrap ;;
    verify) cmd_verify ;;
    screenshot) shift; cmd_screenshot "$@" ;;
    status) cmd_status ;;
    logs) shift; cmd_logs "$@" ;;
    down) cmd_down ;;
    dev-fixtures) cmd_dev_fixtures ;;
    *)
        echo "Usage: $0 {up|bootstrap|verify|screenshot|status|logs <service>|down|dev-fixtures}" >&2
        exit 1
        ;;
esac
