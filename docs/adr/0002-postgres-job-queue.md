# Postgres is the job queue

The pipeline runs one fetcher and one analyzer on a two-core 2011 Mac mini shared with
other services. We decided to queue work in a Postgres `jobs` table claimed with
`SELECT … FOR UPDATE SKIP LOCKED`, releasing claims older than 30 minutes, rather than
adding Redis with Celery, arq or RQ. The queue exists for idempotent reruns and crash
recovery, not for scaling out; a second datastore would cost memory on a 16 GB host and a
second thing to back up for no throughput gain.

## Consequences

- Workers poll with a short sleep when idle; there is no push notification of new jobs.
- If the project ever needs more than a handful of workers, revisit this before reaching
  for Redis: `LISTEN/NOTIFY` is the next step within Postgres.
