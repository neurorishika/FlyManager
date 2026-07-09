#!/bin/sh
set -eu

mkdir -p data/job_exports
mkdir -p temp

exec "$@"
