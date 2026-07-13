#!/bin/sh
set -eu

if [ $# -lt 1 ] || [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
    cat <<'EOF'
Usage: ./scripts/mongo-restore-drill.sh path/to/backup.archive.gz

Restore a MongoDB archive into a fully disposable, isolated scratch
container - never the live stack - and print post-restore sanity counts.
Safe to run against a copy of a real production archive.

The scratch container is always removed on exit, including on failure.
EOF
    exit 0
fi

ARCHIVE_INPUT=$1
ARCHIVE_DIR=$(cd "$(dirname "$ARCHIVE_INPUT")" && pwd)
ARCHIVE_PATH="$ARCHIVE_DIR/$(basename "$ARCHIVE_INPUT")"

if [ ! -f "$ARCHIVE_PATH" ]; then
    echo "Backup archive not found: $ARCHIVE_PATH" >&2
    exit 1
fi

DRILL_CONTAINER="flymanager-restore-drill-$$"

cleanup() {
    docker rm -f "$DRILL_CONTAINER" > /dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

echo "Starting scratch Mongo container: $DRILL_CONTAINER"
docker run -d --name "$DRILL_CONTAINER" mongo:7.0 > /dev/null

echo "Waiting for scratch mongod to accept connections..."
attempt=0
until docker exec "$DRILL_CONTAINER" mongosh --quiet --eval 'db.adminCommand("ping").ok' > /dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 30 ]; then
        echo "Scratch mongod never became ready" >&2
        exit 1
    fi
    sleep 1
done

echo "Copying archive into scratch container..."
docker cp "$ARCHIVE_PATH" "$DRILL_CONTAINER:/tmp/drill.archive.gz"

echo "Restoring archive..."
docker exec "$DRILL_CONTAINER" sh -c 'mongorestore --archive=/tmp/drill.archive.gz --gzip'

echo "--- Post-restore sanity counts ---"
docker exec "$DRILL_CONTAINER" mongosh --quiet --eval '
  const names = db.adminCommand({listDatabases: 1}).databases.map(d => d.name).filter(n => !["admin", "local", "config"].includes(n));
  names.forEach(name => {
    const target = db.getSiblingDB(name);
    print(name + ":");
    target.getCollectionNames().forEach(coll => {
      print("  " + coll + ": " + target.getCollection(coll).countDocuments());
    });
  });
'

echo "Restore drill completed successfully for $ARCHIVE_PATH"
