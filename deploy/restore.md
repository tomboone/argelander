# Backups and restores

Postgres is the one thing on the mini that cannot be rebuilt: reviews and submission history
exist nowhere else, and the host is 15-year-old hardware (spec §10). The `backup` service
dumps it into a [restic](https://restic.net) repository in a Backblaze B2 bucket every night
at 03:30 local. The Cache is not backed up (it can be downloaded again), and neither are the
plots (they can be redrawn).

| Piece | Where |
| --- | --- |
| `backup` service | `compose.yaml`: its own image from `deploy/backup/`, 512 MB, running supercronic |
| Schedule | `deploy/backup/crontab`, 03:30 `America/New_York` |
| What a run does | `deploy/backup/backup.sh`, installed as `argelander-backup` |
| Repository and credentials | `RESTIC_REPOSITORY`, `RESTIC_PASSWORD`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` in the mini's `.env` |
| restic's local copy of the repository index | the `argelander_restic-cache` named volume; disposable |
| The repository password | the mini's `.env` **and** the password manager; without it the backups cannot be read |

Each run:

1. `restic unlock`: removes locks left by a run that was killed part-way (a deploy that
   recreated the container mid-run, a power cut). It removes only stale locks, over 30
   minutes old or held by a dead process. Without it, one such lock would fail every later
   prune.
2. `restic backup --stdin-from-command -- pg_dump`, saved as `/argelander.sql`, tagged
   `nightly`, host `argelander`. restic runs `pg_dump` itself rather than reading
   `pg_dump | restic backup --stdin`, as the handover sketched: if the dump fails, restic
   saves no snapshot at all, where the pipe would save a truncated one that the retention
   policy then counts as a good night. The host is fixed because the container's hostname
   changes on every recreate, and retention applies per host.
3. `restic forget --keep-daily 14 --keep-weekly 8 --prune`: a snapshot a day for two weeks, then one a week back to
   about eight weeks. restic applies the two rules together, so the weeklies overlap the
   dailies rather than following them.
4. On Sundays, `restic check --read-data-subset=5%`: downloads a random 5% of the data
   and verifies it, besides checking the repository's structure.

The last line of every run is `argelander-backup: SUCCESS in <n>s` or
`argelander-backup: FAILURE at <step> (exit <code>) after <n>s`, and a failure exits
non-zero, which supercronic logs as `error running command: exit status <code>`.

The service refuses to start without the four restic and B2 variables, naming the missing
ones in its logs and restarting until they are set, so a deploy without them shows up in
`docker compose ps` straight away rather than at 03:30.

## One-time setup

1. **The bucket.** In the Backblaze console, *Buckets → Create a Bucket*: private, any
   name (`argelander-backup` below). Then on the bucket, *Lifecycle Settings → Keep only the
   last version of the file*. restic talks to B2 through its S3-compatible API, as restic's
   own documentation recommends over its native B2 backend, and through that API a delete
   only hides the file; without the lifecycle rule, pruned data would be kept and billed
   forever. Note the bucket's *Endpoint*, e.g. `s3.us-east-005.backblazeb2.com`.

2. **An application key.** *Application Keys → Add a New Application Key*: access to
   `argelander-backup` only, *Read and Write*. The key ID and the key are shown once.

3. **The `.env` lines.** On the mini, appended to `~/argelander/.env` (mode 600, from
   `deploy/README.md` step 7). Generate the repository password once and put it in the
   password manager too: a lost password means unreadable backups.

   ```sh
   cd ~/argelander
   cat >> .env <<EOF
   RESTIC_REPOSITORY=s3:https://s3.us-east-005.backblazeb2.com/argelander-backup/postgres
   RESTIC_PASSWORD=$(openssl rand -hex 32)
   AWS_ACCESS_KEY_ID=<the key ID>
   AWS_SECRET_ACCESS_KEY=<the application key>
   EOF
   grep RESTIC_PASSWORD .env      # copy this into the password manager
   ```

4. **`restic init`.** Once, ever, for the repository. A one-off container, since the
   service may not be running yet:

   ```sh
   docker compose build backup
   docker compose run --rm backup restic init
   # created restic repository 1a2b3c4d5e at s3:https://s3.us-east-005.backblazeb2.com/argelander-backup/postgres
   ```

   Run against a repository that already exists, it refuses (`config file already
   exists`), so it cannot wipe one.

5. **Deploy, then take the first backup now** rather than waiting for 03:30:

   ```sh
   task deploy
   task backup:now        # ends with: argelander-backup: SUCCESS in <n>s
   task backup:check      # one snapshot, host argelander, tag nightly, /argelander.sql
   ```

## Checking it

```sh
task backup:check                                   # restic snapshots
docker compose logs backup | grep 'argelander-backup:'
```

`task backup:check` and `task backup:now` run on the mini, against the running `backup`
container; from the laptop, `ssh kumadrome 'cd ~/argelander && task backup:check'`. After
the first night there should be a snapshot dated around 03:30. Snapshot times are shown in
`America/New_York`.

To see a failure end to end, point a run at a database that does not exist:

```sh
docker compose exec -T -e DATABASE_URL=postgresql://argelander@nowhere/argelander backup argelander-backup; echo "exit $?"
# subprocess pg_dump: pg_dump: error: could not translate host name "nowhere" to address: ...
# Fatal: unable to save snapshot: failed to save /argelander.sql: Fatal: command failed: exit status 1
# argelander-backup: FAILURE at dump (exit 1) after 1s
# exit 1
task backup:check        # no new snapshot
```

## The restore drill: the latest snapshot into a scratch database

Restores `/argelander.sql` from the latest nightly snapshot into `argelander_restore`
beside the live database, compares the two, then drops it. Nothing touches `argelander`.
The backup image has `psql` 16 to match the server.

```sh
cd ~/argelander
docker compose exec -T postgres createdb -U argelander argelander_restore
docker compose exec -T backup bash -o pipefail -c \
  'restic dump --tag nightly latest /argelander.sql \
   | psql --quiet --output /dev/null --set ON_ERROR_STOP=1 --single-transaction \
       "${DATABASE_URL%/*}/argelander_restore"'
echo "exit $?"           # 0, or the drill failed
```

`pipefail` matters: without it a failed `restic dump` (wrong password, no snapshot, B2
unreachable) hands `psql` an empty or cut-off stream, `psql` restores what it got and exits
0, and the drill passes on nothing. An SQL error stops `psql` and rolls the whole restore
back.

Then compare the two. Reviews and submissions change only when Tom acts, so they should
match exactly; the workers keep writing Targets, Jobs and Analyses, so those can be ahead in
the live database by whatever ran since 03:30.

```sh
q="select (select version_num from alembic_version) as migration,
          (select count(*) from reviews) as reviews,
          (select count(*) from submissions) as submissions,
          (select count(*) from proposals) as proposals,
          (select count(*) from targets) as targets"
for db in argelander argelander_restore; do
  docker compose exec -T postgres psql -U argelander -d "$db" -c "$q"
done
docker compose exec -T postgres dropdb -U argelander argelander_restore
```

To restore an older snapshot, list them with `task backup:check` and pass its ID to
`restic dump` in place of `--tag nightly latest`.

## Restoring for real

When the database is lost or damaged. On a fresh host, first do steps 1–7 of
`deploy/README.md`'s one-time setup, and add the **same** four restic and B2 values to
`.env` (the password from the password manager). Skip `restic init`: the repository already
exists.

1. Stop everything that writes, and bring up Postgres alone:

   ```sh
   cd ~/argelander
   docker compose stop fetcher analyzer api
   docker compose up -d postgres
   ```

2. Replace the database with an empty one. On a fresh host, Postgres has just created an
   empty `argelander`, so skip this:

   ```sh
   docker compose exec -T postgres dropdb -U argelander argelander
   docker compose exec -T postgres createdb -U argelander argelander
   ```

3. Restore into it. `run`, not `exec`, since the `backup` service may not be running; the
   dump brings its own `alembic_version`, so do not run `db-upgrade` first.

   ```sh
   docker compose run --rm -T backup bash -o pipefail -c \
     'restic dump --tag nightly latest /argelander.sql \
      | psql --quiet --output /dev/null --set ON_ERROR_STOP=1 --single-transaction "$DATABASE_URL"'
   echo "exit $?"
   ```

   Anything but exit 0 means the database may hold part of the dump: go back to step 2
   before trying again, and do not deploy onto it.

4. `task deploy`. It migrates the restored database forward if the code is newer than the
   dump, and starts the rest of the stack, `backup` included.
