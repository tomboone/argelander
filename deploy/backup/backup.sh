#!/usr/bin/env bash
# The nightly backup (spec §10), run by supercronic from the crontab beside this file, or by
# hand with `task backup:now`. Dumps Postgres into restic, applies the retention policy and,
# on Sundays, reads back 5% of the repository's data. Every run ends with one line starting
# `argelander-backup: SUCCESS` or `argelander-backup: FAILURE`, and a failure exits non-zero,
# which supercronic logs as a failed job.
set -euo pipefail

# Each restic run names this host explicitly: the container's own hostname changes whenever
# Compose recreates it, and restic's retention policy applies per host, so snapshots taken
# under an old hostname would never be forgotten.
HOST=argelander
TAG=nightly

started=$SECONDS
step=start
trap 'echo "argelander-backup: FAILURE at ${step} (exit $?) after $((SECONDS - started))s"' ERR

step=unlock
# A run killed part-way (a deploy recreating the container, a power cut) leaves its lock in
# the repository, and restic never ignores one on its own: every later prune would fail on it.
# `unlock` removes only stale locks, those over 30 minutes old or held by a process that is
# gone, so it cannot break a run in progress.
restic unlock

step=dump
# restic runs pg_dump itself rather than reading a pipe: when the command exits non-zero,
# restic saves no snapshot, where `pg_dump | restic backup --stdin` would save a truncated or
# empty one, and the retention policy would then count it as a good night.
restic backup --host "$HOST" --tag "$TAG" --stdin-filename argelander.sql \
    --stdin-from-command -- pg_dump --dbname "$DATABASE_URL"

step=forget
restic forget --host "$HOST" --tag "$TAG" --keep-daily 14 --keep-weekly 8 --prune

if [ "$(date +%u)" = 7 ]; then
    step=check
    restic check --read-data-subset=5%
fi

echo "argelander-backup: SUCCESS in $((SECONDS - started))s"
