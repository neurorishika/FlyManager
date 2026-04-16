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

if [ ! -f "$ENV_FILE" ]; then
    cp "$EXAMPLE_ENV_FILE" "$ENV_FILE"
    if command -v python3 >/dev/null 2>&1; then
        GENERATED_SECRET=$(python3 - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
)
        python3 - <<PY
from pathlib import Path

env_file = Path(r"$ENV_FILE")
env_file.write_text(
    env_file.read_text().replace("replace-with-a-long-random-string", "$GENERATED_SECRET"),
    encoding="utf-8",
)
PY
    elif command -v openssl >/dev/null 2>&1; then
        GENERATED_SECRET=$(openssl rand -hex 32)
        sed -i.bak "s/replace-with-a-long-random-string/$GENERATED_SECRET/" "$ENV_FILE"
        rm -f "$ENV_FILE.bak"
    else
        echo "Unable to generate SECRET_KEY automatically because neither python3 nor openssl is available."
        exit 1
    fi
    echo "Created .env from .env.example with a generated SECRET_KEY."
fi

cd "$ROOT_DIR"
docker compose up -d --build

echo "FlyManager is starting. Open http://localhost:5234 once the app health check passes."