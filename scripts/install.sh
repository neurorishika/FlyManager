#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ENV_FILE="$ROOT_DIR/.env"
EXAMPLE_ENV_FILE="$ROOT_DIR/.env.example"

if ! command -v docker >/dev/null 2>&1; then
    echo "Docker is required but was not found in PATH."
    exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
    echo "Docker Compose v2 is required but is not available."
    exit 1
fi

mkdir -p "$ROOT_DIR/data/uploads"
mkdir -p "$ROOT_DIR/data/backup"
mkdir -p "$ROOT_DIR/flask_session"
mkdir -p "$ROOT_DIR/temp"
mkdir -p "$ROOT_DIR/flymanager/app/static/generated_labels"
mkdir -p "$ROOT_DIR/backups/mongodb"
mkdir -p "$ROOT_DIR/backups/state"
mkdir -p "$ROOT_DIR/backups/logs"

# shellcheck source=lib/generate-secret-key.sh
. "$(dirname "$0")/lib/generate-secret-key.sh"

if [ ! -f "$ENV_FILE" ]; then
    cp "$EXAMPLE_ENV_FILE" "$ENV_FILE"
    echo "Created .env from .env.example."
fi

ensure_secret_key "$ENV_FILE" || exit 1

cd "$ROOT_DIR"
docker compose up -d --build

echo "FlyManager is starting. Open http://localhost:5234 once the app health check passes."