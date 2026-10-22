# Runbook: a pipeline run on the dev stack

How to take a Slice from VSX to queued Proposals on the dev stack, which commands to run, and
what to look at when something goes wrong. Every command runs in the `app` container
(`docker compose exec app …`), as the Taskfile does. The example output is from the first
end-to-end run, Draco 13–16, on 2026-10-21 (NEU-1722).

The prod stack on the Mac mini runs the same commands against its own services; section 10
says how it differs.

## 1. Bring the stack up

```sh
task up        # postgres, app and jupyter; installs the git hooks
task migrate   # argelander db-upgrade: Alembic to head
```

The workers are not started by `task up`: they sit behind the `workers` Compose profile so
nothing fetches until a run is meant.

## 2. Select a Slice

```sh
docker compose exec app argelander select-slice Dra 13 16
```

```
Slice Dra-13-16 (id 3): 430 Targets, 0 new
  with a Gaia DR3 record: 430; with a Gaia vari_* class: 0; with a Gaia period: 430
  13–14 mag: 106
  14–15 mag: 119
  15–16 mag: 205
  fetch Jobs queued: 0
```

It asks VizieR's copy of VSX for the VAR-typed entries with a Gaia DR3 ID in the
constellation and magnitude range, joins Gaia DR3 for all of them in one upload query, writes
a `slices` row and the Targets, and queues a fetch Job for every Target still `selected`. It
takes a couple of minutes, nearly all of it Gaia's archive.

Running it again is safe: it writes another `slices` row of the same name, refreshes the
Targets' Gaia columns, and queues nothing for a Target already past `selected` (the output
above is a rerun, hence `0 new` and `0` queued; the first run queued all 430).
Each Target keeps the Slice that first selected it.

What the Draco Slice turned out to be: every one of its 430 Targets is a Gaia DR3
short-timescale candidate that VSX imported as `VAR`, named `Gaia DR3 <source_id>`, with
Gaia's short-timescale frequency as its period and no `vari_classifier_result` class. That is
why the vari_* class count is 0 and the period count is 430; a live query confirmed it.

## 3. Start the workers

```sh
task workers        # starts (or restarts) fetcher and analyzer
task workers:logs   # follows both JSON logs
```

`fetcher` claims ten fetch Jobs at a time (`FETCH_BATCH_SIZE`), asks ZTF, TESS and ASAS-SN
once per Target and Gaia's epoch photometry once for the batch, writes the Cache under
`data/cache/{survey}/{gaia_source_id}/`, indexes it in `light_curve_files`, and queues an
analyze Job per Target. `analyzer` claims one analyze Job at a time, runs the analysis chain,
writes the `analyses` row, and in the same transaction writes the Proposal and draws its plots
under `data/plots/{gaia_source_id}/{pipeline_version}/`.

Both poll every 60 s when idle. They do not reload on edits: run `task workers` again to pick
up a code change. A batch the fetcher has claimed is recorded only when the whole batch is
done, so stopping it mid-batch leaves ten claimed Jobs that `release_stale` hands back after
30 minutes (spec §3).

Pace on 2026-10-21, on the dev laptop: about five minutes per batch of ten Targets for the
fetcher, TESS setting the pace, and about 20 seconds per Target for the analyzer. The fetcher
is the bottleneck; the full 430-Target Slice is about 3.5 hours.

## 4. Watch progress

```sh
docker compose exec app argelander status
docker compose exec app argelander status --slice Dra-13-16
```

```
Targets (all Slices): 430
  selected      360
  fetching       10
  fetched         0
  analyzed        0
  proposed       60
  reviewed        0
  submitted       0
  accepted        0
  rejected        0
  failed          0
Jobs         pending  claimed     done   failed
  fetch          360       10       60        0
  analyze          0        0       60        0
```

Targets count by their stored status; Jobs count by kind and status, every Job ever queued,
so a re-queued Target counts once in `done` or `failed` and again in `pending`. `--slice`
narrows both to the Targets the Slice selected first.

What to look at in the logs:

- `"level": "WARNING"` lines from `argelander.fetch.base` are one archive call being retried;
  from `argelander.fetch.worker`, one survey that failed for one Target after its retries.
  Neither fails the Job: a fetch Job fails only when every survey failed.
- `"level": "ERROR"` lines are a Job failing (with its traceback) or a whole pass failing,
  Postgres restarting, say. The worker carries on either way.
- The plain-text `0% (0/3668) of the cadences will be ignored…` lines are lightkurve's own
  stdout; ignore them.

## 5. Look at the results

The Cache and plots are files under `data/`; the rest is Postgres, on `localhost:5433`
(`docker compose exec postgres psql -U argelander`).

Light Curve outcomes by survey, and why surveys failed:

```sql
SELECT survey, status, count(*) FROM light_curve_files GROUP BY 1, 2 ORDER BY 1, 2;
SELECT survey, error, count(*) FROM light_curve_files
WHERE status = 'failed' GROUP BY 1, 2 ORDER BY 3 DESC;
```

Failed Jobs and their errors:

```sql
SELECT j.kind, t.vsx_name, j.attempts, j.last_error
FROM jobs j JOIN targets t ON t.id = j.target_id WHERE j.status = 'failed';
```

Queued Proposals by proposed type, with Agreement and Confidence:

```sql
SELECT p.proposed_type, p.uncertain, count(*), round(avg(p.confidence), 3) AS mean_confidence,
       count(*) FILTER (WHERE a.agreement) AS with_agreement
FROM proposals p JOIN analyses a ON a.id = p.analysis_id
WHERE p.status = 'queued' GROUP BY 1, 2 ORDER BY 3 DESC;
```

One Target's evidence: `analyses.plots` holds each plot's path under `data/plots/`, and
`submission.png` is the one VSX would get.

```sql
SELECT t.vsx_name, t.vsx_period, p.proposed_type, p.period, p.confidence, a.plots->>'submission'
FROM proposals p JOIN targets t ON t.id = p.target_id JOIN analyses a ON a.id = p.analysis_id
WHERE p.status = 'queued' ORDER BY p.confidence DESC LIMIT 10;
```

## 6. Re-queue work

```sh
docker compose exec app argelander enqueue fetch --failed
docker compose exec app argelander enqueue analyze --slice Dra-13-16
docker compose exec app argelander enqueue fetch --target "Gaia DR3 1144599167953902464"
```

```
$ argelander enqueue fetch --failed
queued 0 fetch Jobs for 0 Targets (0 already had a Job pending or claimed)
$ argelander enqueue fetch --target "Gaia DR3 1599735890237763456"
queued 0 fetch Jobs for 1 Targets (1 already had a Job pending or claimed)
```

(The first run had no failed Job, and that Target's fetch Job was still waiting.)

`enqueue <fetch|analyze>` queues a Job of that kind for each Target named: one by VSX name
(`--target`), a Slice's (`--slice`), or with `--failed` only those whose newest Job of that
kind failed for good, across every Slice when given alone. A Target that already has a Job
of either kind pending or claimed is skipped, so the command is safe to repeat and never
queues a fetch beside a waiting analysis. Each Target named has its status recomputed, so a
re-queued `failed` Target shows `selected` (fetch) or `fetched` (analyze) again.

When to use which:

- an archive was down (ASAS-SN's maintenance, say): `enqueue fetch --slice …` once it is
  back; a re-fetch replaces each survey's `light_curve_files` row and Cache files, and queues
  a new analysis;
- the analysis or the rules changed: bump `PIPELINE_VERSION`, then `enqueue analyze --slice
  …`; each Target gets a new Analysis and Proposal and its older queued Proposal is
  `superseded` (spec §4);
- only the plots changed: `classify --replot` (section 8), no Job needed.

## 7. Repair the status column

```sh
docker compose exec app argelander recompute-status
```

```
changed 0 Targets
```

`targets.status` is a cache the workers keep (spec §4). `recompute-status` rederives every
Target's status from its Jobs, Proposals, Reviews and Submissions and prints each one it
changes. After a clean run it changes nothing; a change prints as `Gaia DR3 … : fetching -> selected` above the count.
The one change to expect is a fetch claim the
fetcher left behind when it was stopped or crashed mid-batch, once `release_stale` has handed
it back: `fetching -> selected`.

## 8. Redraw the plots

```sh
docker compose exec app argelander classify --replot
docker compose exec app argelander classify --replot --target "Gaia DR3 1144599167953902464"
```

Redraws every Analysis at the current Pipeline Version from its newest Proposal, without
analysing or classifying again, and rewrites `analyses.plots`.

## 9. Stop

```sh
task workers:stop   # the workers only
task down           # everything; the postgres volume is kept
```

## 10. The prod stack

`compose.yaml` alone is the prod stack (spec §10): `postgres`, `fetcher` and `analyzer`
from the prod image, each with a memory limit (1 GB, 1 GB, 3 GB) and `restart:
unless-stopped`, plus `api` (512 MB) behind the `api` profile until the review app's `web`
command exists. Postgres keeps its data on a named volume on the internal disk; the
Cache, the plots and the astropy download cache live on the USB SSD. The mini's `.env`
needs two variables, plus a third for the first start of the review app:

```sh
USB_ROOT=/mnt/argelander      # the USB SSD's mount point (deploy/README.md)
POSTGRES_PASSWORD=<a real one>
TS_AUTHKEY=tskey-auth-...     # once, then blanked (deploy/tailscale.md)
```

Neither is required by Compose itself, so the dev stack keeps working without them; left
unset in prod, Postgres refuses to initialise and the workers' bind fails with
`bind source path does not exist: /USB_ROOT-is-not-set`.

Once, on a freshly formatted SSD, create the directories, owned by the image's user (uid
1000), and the Cache marker. Check first that `findmnt "$USB_ROOT"` shows the SSD: run
against the empty mount point on the boot disk, these put the marker there and the guard
then passes with the SSD missing. On the mini, `USB_ROOT` is a bind mount of
`/mnt/music/argelander`, and `deploy/README.md` step 4 creates them there instead:

```sh
sudo mkdir -p "$USB_ROOT"/{cache,plots,astropy}
sudo touch "$USB_ROOT"/cache/.argelander-cache
sudo chown -R 1000:1000 "$USB_ROOT"/{cache,plots,astropy}
```

The marker is the mount guard. The workers bind the SSD's mount point itself, so with the
SSD unplugged they still start, on the empty directory left on the boot disk, find no
marker, and exit with this line in their logs, restarting until the SSD is back:

```
CACHE_DIR /usb/cache has no .argelander-cache marker, so the USB SSD is not mounted; mount it, or set ALLOW_UNMOUNTED_CACHE=1 in dev
```

A mount-point test cannot do this from inside a container, where every bind-mount target is
a mount point. The workers' healthcheck reads the same marker, so `docker compose ps` shows
them unhealthy if the SSD drops out under them. Docker does not restart an unhealthy
container: restart them by hand once the SSD is back.

On the mini, `task deploy` does all of what follows, and `deploy/README.md` has the
mini's own setup: the bind mount the T7 gets for this, the fstab lines and the systemd
drop-in that makes Docker wait for them. By hand, bring it up, migrate, then start the
workers:

```sh
docker compose build
docker compose up -d postgres
docker compose run --rm fetcher argelander db-upgrade
docker compose up -d fetcher analyzer
docker stats --no-stream      # each service's limit shows in MEM USAGE / LIMIT
```

The review app (`api`) and its `tailscale` sidecar share the `api` profile and come up
together with `docker compose --profile api up -d tailscale api`; the app is reachable only
on the personal tailnet, at `http://argelander:8000`. `deploy/tailscale.md` has the auth
key, the ACLs, the checks, and what to do when the sidecar restarts on its own or loses its
state volume.

The `backup` service dumps Postgres to Backblaze B2 with restic every night at 03:30;
`task backup:check` lists the snapshots, and `deploy/restore.md` has the setup, the restore
drill and a real restore. It needs four more variables in the mini's `.env` and refuses to
start without them.

The CLI runs in the fetcher's container (`docker compose exec fetcher argelander status`),
since prod has no `app` service. Logs are Docker's json-file driver, rotated at 5 × 10 MB per
service.

## The first run, 2026-10-21

Draco 13–16, 430 Targets, Pipeline Version 0.1.0. The workers ran for about 30 minutes,
until 60 Targets had Proposals, then the fetcher was stopped mid-batch and the analyzer
left to drain: the `status` output in section 4 is the end state, ten fetch Jobs still
claimed. `recompute-status` changed nothing.

| | ok | empty | failed |
| --- | --- | --- | --- |
| ZTF | 58 | 2 | 0 |
| TESS | 60 | 0 | 0 |
| Gaia epoch photometry | 60 | 0 | 0 |
| ASAS-SN | 0 | 1 | 59 |

Every ASAS-SN failure is the same: Sky Patrol's `get_block_servers` answers `[]`, the
maintenance outage `fetch/asassn.py` has recorded since 2026-10-13, so each call gives up after
its retries (`FetchError: asassn: gave up after 3 attempts; last failure: connection error`).
A fetch Job fails only when every survey fails, so no Job failed. Once Sky Patrol is back,
`enqueue fetch --slice Dra-13-16` fetches it for every Target.

| Proposed type | Proposals | Mean Confidence | With Agreement |
| --- | --- | --- | --- |
| CST | 46 | 1.000 | 0 |
| VAR (uncertain) | 6 | 0.000 | 0 |
| EB | 3 | 0.911 | 3 |
| EB (uncertain) | 1 | 0.600 | 1 |
| EA | 2 | 0.857 | 2 |
| ROT | 1 | 1.000 | 1 |
| L (uncertain) | 1 | 0.200 | 0 |

Seven Analyses reached Agreement, none at 2:1, none blend-suspect. The Cache took 79 MB and
the plots 25 MB for the 60 Targets.

Most of the 60 are Gaia short-timescale candidates that the other surveys do not see vary.
40 of the 46 CST Proposals have the variability rule's support too: their magnitudes spread
no more than 2.0 times what the errors give. The other six rest on the absence of Agreement
alone, the variability rule being neutral (an excess of 2.1–2.9 for four, every survey
contaminated for two), and that single 0.5-weight item still makes their Confidence 1.000:
worth a look before the review queue sorts by Confidence.
