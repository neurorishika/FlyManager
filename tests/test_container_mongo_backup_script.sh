#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
SCRIPT="$ROOT_DIR/scripts/container-mongo-backup.sh"

TEST_BACKUP_DIR=$(mktemp -d)
trap 'rm -rf "$TEST_BACKUP_DIR"' EXIT

failures=0

check() {
    description=$1
    shift
    if "$@"; then
        printf 'PASS: %s\n' "$description"
    else
        printf 'FAIL: %s\n' "$description"
        failures=$((failures + 1))
    fi
}

# 1. Dry run with no remote/heartbeat configured must succeed and print
#    what it would do, without requiring a real mongod, rclone remote,
#    or network access.
output=$(BACKUP_DRY_RUN=1 BACKUP_DIR="$TEST_BACKUP_DIR" MONGO_HOST=nonexistent-host MONGO_PORT=27017 BACKUP_KEEP_DAYS=14 sh "$SCRIPT" 2>&1)
check "dry run exits 0" test $? -eq 0
check "dry run mentions mongodump target" sh -c "printf '%s' \"$output\" | grep -q 'nonexistent-host:27017'"
check "dry run does not create an archive file" sh -c "[ -z \"\$(find '$TEST_BACKUP_DIR' -maxdepth 1 -type f)\" ]"

# 2. Dry run with rclone/heartbeat configured must mention both steps
#    without actually invoking rclone/curl.
output=$(BACKUP_DRY_RUN=1 BACKUP_DIR="$TEST_BACKUP_DIR" MONGO_HOST=nonexistent-host MONGO_PORT=27017 BACKUP_KEEP_DAYS=14 RCLONE_REMOTE=dummy:remote/path HEALTHCHECK_UUID=dummy-uuid sh "$SCRIPT" 2>&1)
check "dry run mentions rclone remote" sh -c "printf '%s' \"$output\" | grep -q 'dummy:remote/path'"
check "dry run mentions heartbeat UUID" sh -c "printf '%s' \"$output\" | grep -q 'dummy-uuid'"

if [ "$failures" -gt 0 ]; then
    printf '%d check(s) failed\n' "$failures"
    exit 1
fi

printf 'All checks passed\n'
