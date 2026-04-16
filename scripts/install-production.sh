#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ENV_FILE="$ROOT_DIR/.env"
EXAMPLE_ENV_FILE="$ROOT_DIR/.env.example"
DOMAIN_ARG="${1:-}"

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "$2"
        exit 1
    fi
}

upsert_env_var() {
    key="$1"
    value="$2"

    require_command python3 "python3 is required to update the .env file automatically."

    python3 - <<PY
from pathlib import Path
import re

env_file = Path(r"$ENV_FILE")
key = "$1"
value = "$2"
content = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
pattern = re.compile(rf"^(\s*{re.escape(key)}\s*=\s*).*$", re.MULTILINE)

if pattern.search(content):
    content = pattern.sub(rf"\1{value}", content)
else:
    if content and not content.endswith("\n"):
        content += "\n"
    content += f"{key}={value}\n"

env_file.write_text(content, encoding="utf-8")
PY
}

require_command docker "Docker is required but was not found in PATH."

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
fi

if [ -n "$DOMAIN_ARG" ]; then
    upsert_env_var "FLYMANAGER_DOMAIN" "$DOMAIN_ARG"
fi

CURRENT_DOMAIN=$(awk -F '=' '/^[[:space:]]*FLYMANAGER_DOMAIN[[:space:]]*=/{value=$2} END{print value}' "$ENV_FILE" | tr -d ' "')

if [ -z "$CURRENT_DOMAIN" ] || [ "$CURRENT_DOMAIN" = "flymanager.example.com" ]; then
    echo "Set FLYMANAGER_DOMAIN in .env or pass the domain as the first argument to this script."
    exit 1
fi

cd "$ROOT_DIR"
docker compose -f compose.yaml -f compose.production.yaml up -d --build

echo "Production stack started for https://$CURRENT_DOMAIN"
echo "Make sure DNS points to this server and ports 80/443 are reachable from the internet."