#!/bin/sh
set -eu

mkdir -p "${UPLOAD_FOLDER:-data/uploads}"
mkdir -p "${SESSION_FILE_DIR:-/tmp/flymanager/flask_session}"
mkdir -p data/backup
mkdir -p data/job_exports
mkdir -p temp
mkdir -p flymanager/app/static/generated_labels

python -m flymanager.app.bootstrap

exec "$@"