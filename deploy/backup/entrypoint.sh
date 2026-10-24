#!/usr/bin/env bash
# Refuses to start without the settings every run needs, so a missing one shows at deploy time
# in `docker compose ps` and the service's logs, not at 03:30 as a failed backup. Then runs
# the command: supercronic by default, or whatever `docker compose run backup ...` names.
set -euo pipefail

missing=()
for name in DATABASE_URL RESTIC_REPOSITORY RESTIC_PASSWORD AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY; do
    if [ -z "${!name:-}" ]; then
        missing+=("$name")
    fi
done
if [ ${#missing[@]} -gt 0 ]; then
    echo "argelander-backup: not starting, ${missing[*]} unset; set them in .env (deploy/restore.md)" >&2
    exit 1
fi

exec "$@"
