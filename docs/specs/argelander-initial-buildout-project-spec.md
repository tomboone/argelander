# Argelander: Initial Buildout — project spec

**Linear project:** Argelander: Initial Buildout (P-NEU-98), initiative Argelander (I-10)
**Repo:** `tomboone/argelander` (single repo)
**Design source:** `docs/handover.md` (settled design), `CONTEXT.md` (glossary), `docs/adr/` (decisions)
**Written:** 2026-10-19, from the `/projectit` Phase 1 grilling session

This spec is what `/implementit` falls back to when a ticket has no per-ticket plan. It
has to be concrete enough that a ticket can be built from it alone. Where the handover
already settles something this spec points at it rather than repeating it; where the
grilling session added a decision, the decision and its reason are here.

---

## 1. Purpose and end state

Build v1 of a pipeline that turns VAR-typed VSX entries with Gaia DR3 IDs into accepted
VSX revisions, each a verified claim with traceable evidence. The pipeline is a private
instrument, not a product.

**Done when:** the full Compose stack runs on the `kumadrome` Mac mini and at least one
Proposal from the first Slice (Draco, 13–16 mag) has gone Review → Submission → VSX →
accepted Verdict. Ongoing submissions and scale-up (handover build-order steps 8–9) are
the next project in the initiative.

**Not in scope:** cross-catalog audits, the RNAAS write-up, batch list submissions, a
scheduler service, any auth beyond the tailnet, an error tracker, machine-learned
classification, Kubernetes, Coolify.

## 2. Vocabulary

Use the terms in `CONTEXT.md` everywhere: Target, Slice, Light Curve, Cache, Analysis,
Pipeline Version, Agreement, Proposal, Confidence, Superseded, Review, Submission,
Verdict, Stage, Job. Table names, Python classes and template labels use these words.

## 3. Architecture

Six Stages share state through Postgres and the Parquet Cache (handover: Architecture).
Four are automated (target selector, fetcher, analyzer, classifier+plots); two are Tom's
(review, submission tracker). In v1:

- **No scheduler.** Tom runs `argelander select-slice` by hand. The `fetcher` and
  `analyzer` are long-running Compose services that poll the `jobs` table and sleep when
  idle. Reason: v1 processes one Slice; a scheduler is a solution to a problem that
  arrives with the next project.
- **One Python package, one image.** Every service is `python -m argelander <command>`
  with a different command. Reason: all services share models, config and the science
  code; separate images would only duplicate dependency installs.
- **Postgres is the job queue** (ADR-0002). `SELECT … FOR UPDATE SKIP LOCKED`; stale
  claims older than 30 minutes are released. One fetcher and one analyzer process; the
  claim logic exists for idempotency, not scale-out.
- **Submission is manual** (ADR-0001). The tracker produces paste-ready values and a plot;
  no code ever touches the VSX web forms.

### 3.1 Package layout

```
src/argelander/
  __init__.py          PIPELINE_VERSION = "0.1.0"  (bumped whenever rules or analysis change)
  config.py            pydantic-settings: DATABASE_URL, CACHE_DIR, PLOTS_DIR, ASTRO_CACHE_DIR, archive limits
  cli.py               typer app: select-slice, fetch-worker, analyze-worker, classify, enqueue, web, db-upgrade
  db/
    models.py          SQLAlchemy 2 declarative models (section 4)
    session.py         sync engine/session for workers, async engine/session for FastAPI (psycopg 3)
    queue.py           claim(), complete(), fail(), release_stale()
  alembic/             migrations (env.py reads DATABASE_URL from config)
  catalog/
    vsx.py             VizieR B/vsx slice query via astroquery
    gaia.py            one ADQL table-upload join per Slice (gaia_source + vari_* + neighbours)
    constellation.py   astropy get_constellation + Slice bounds
  lightcurve.py        LightCurve dataclass + Parquet read/write (section 5)
  fetch/
    base.py            Fetcher protocol, per-archive semaphore, retry with backoff
    ztf.py  tess.py  gaia_epoch.py  asassn.py
    worker.py          fetch-worker loop
  analysis/
    time.py            every survey → BJD_TDB
    clean.py           survey quality flags → cleaned LightCurve
    periodogram.py     Lomb-Scargle per band + BoxLeastSquares
    aliases.py         P, 2P, P/2, 1 d, 0.5 d, sidereal-day, TESS 13.7 d tests
    agreement.py       the Agreement rule
    blend.py           Gaia neighbours within a few arcsec
    cmd.py             colour-magnitude position from Gaia colour + parallax
    worker.py          analyze-worker loop (writes Analysis, then runs classify + plots)
  classify/
    types.py           VSX type codes and definitions used by rules
    rules/             one module per rule; each a Rule subclass
    registry.py        rules per VSX type
    confidence.py      normalised weighted sum
    propose.py         Analysis → Proposal (and supersede logic)
  plots/
    phase.py  periodogram.py  cmd.py   matplotlib, MPLBACKEND=Agg, PNG to PLOTS_DIR
  web/
    app.py             FastAPI, Jinja2 templates, htmx; routes in section 9
    templates/  static/
  submission/
    form.py            Proposal + Review → VSX form field values and reference text
    tracker.py         Submission lifecycle and Verdict recording
tests/                 pytest; tests/fixtures/*.parquet (section 11)
notebooks/             prototype notebooks; import from argelander, never define science here
compose.yaml           prod stack (section 10); compose.dev.yaml adds jupyter and dev mounts
Dockerfile             python:3.12-slim, uv, non-root
Taskfile.yml           go-task: up, down, test, lint, format, migrate, deploy, backup:check
deploy/                fstab line, systemd override, tailscale sidecar notes, restore drill
```

### 3.2 Stack

Python 3.12. uv + `pyproject.toml`, ruff (lint + format). Type hints `X | None`, never
`Optional`. No `from __future__ import annotations`. Typer for the CLI. SQLAlchemy 2 +
Alembic on psycopg 3 (sync in workers, async in FastAPI). astropy, astroquery,
lightkurve, pyarrow, numpy, matplotlib, FastAPI, Jinja2, htmx (vendored static file).
pydantic-settings for config. structlog or stdlib logging to stdout as JSON lines. No
pandas or polars in the core package (notebooks may use pandas for display).

Pin exact versions in `uv.lock`; the Mac mini's 2011 CPU means a wheel that crashes with
"illegal instruction" is pinned to the last working version with a comment (handover:
Host constraints).

## 4. Data model (Postgres)

All tables carry `created_at timestamptz default now()`. IDs are bigint identity unless
noted. JSONB is used for per-survey detail that varies in shape; scalar columns are used
for anything queried or sorted.

| Table | Purpose | Key columns |
| --- | --- | --- |
| `slices` | one Slice per selection run | `name` (e.g. `Dra-13-16`), `constellation`, `mag_min`, `mag_max`, `pipeline_version`, `n_targets` |
| `targets` | one Target per VSX entry | `slice_id`, `vsx_oid` (unique), `vsx_name`, `gaia_source_id` (unique), `ra`, `dec`, `vsx_type`, `vsx_period`, `vsx_mag_max`, `vsx_mag_min`, `vsx_band`, `gaia_g`, `gaia_bp_rp`, `parallax`, `parallax_error`, `gaia_vari_class`, `gaia_vari_class_score`, `gaia_vari_period`, `gaia_vari_detail` jsonb, `neighbours` jsonb (sources within 5″: id, sep, G), `status` |
| `light_curve_files` | Cache index | `target_id`, `survey` (`ztf`/`tess`/`gaia`/`asassn`), `source_key` (survey-native id(s)), `path`, `n_points`, `t_min_bjd`, `t_max_bjd`, `bands` text[], `fetched_at`, `fetcher_version`, `status` (`ok`/`empty`/`failed`), `error` |
| `jobs` | the queue | `kind` (`fetch`/`analyze`), `target_id`, `status` (`pending`/`claimed`/`done`/`failed`), `claimed_at`, `attempts`, `max_attempts` default 3, `last_error`, `payload` jsonb, `finished_at`; index on `(kind, status, created_at)` |
| `analyses` | one Analysis per Target per Pipeline Version | `target_id`, `pipeline_version`, `inputs` jsonb (per survey: n points, date range, bands, light_curve_file ids), `periods` jsonb (per band: best period, power, FAP, top-5 peaks), `alias_tests` jsonb, `agreement` bool, `agreed_period`, `agreed_sources` text[], `blend` jsonb, `cmd` jsonb (abs G, BP−RP, distance), `plots` jsonb (kind → path), `started_at`, `finished_at`; unique `(target_id, pipeline_version)` |
| `proposals` | one Proposal per Analysis | `analysis_id`, `target_id`, `pipeline_version`, `proposed_type`, `period`, `epoch_bjd`, `mag_max`, `mag_min`, `band`, `confidence` numeric(4,3), `evidence` jsonb (list of rule name, verdict, weight, explanation), `status` (`queued`/`superseded`/`approved`/`rejected`), `superseded_by` nullable FK |
| `reviews` | Tom's Review of a Proposal | `proposal_id`, `decision` (`approve`/`edit`/`reject`), `edited` jsonb (field → new value, when decision = edit), `notes` text not null, `reviewed_at` |
| `submissions` | a Submission and its Verdict | `proposal_id`, `review_id`, `form_fields` jsonb, `reference_text`, `plot_path`, `status` (`prepared`/`submitted`/`accepted`/`rejected`), `submitted_at`, `verdict_at`, `verdict_reason` text, `verdict_tags` text[] |

**Target status** is derived for display, not stored as truth: it follows the newest Job
and Proposal. A stored `status` column on `targets` is a cache updated by the workers
(`selected` → `fetching` → `fetched` → `analyzed` → `proposed` → `reviewed` →
`submitted` → `accepted`/`rejected`, plus `failed`), and may be recomputed from the other
tables by a CLI command.

**Rerun semantics** (grilling decision): a rerun under a new Pipeline Version inserts a new
`analyses` row and a new `proposals` row. Any older Proposal for the same Target with
status `queued` is set to `superseded` with `superseded_by` pointing at the new row. A
Proposal with status `approved` or `rejected`, or one referenced by a Submission, is never
modified; the review app shows the newer Proposal beside it with a field-level diff.
Reruns under the *same* Pipeline Version overwrite the existing Analysis row (idempotent
stages, handover: Design principles).

## 5. Light curves and the Cache

```python
@dataclass(frozen=True)
class LightCurve:
    survey: str              # "ztf" | "tess" | "gaia" | "asassn"
    source_key: str          # survey-native id(s), e.g. ZTF oids joined by "+"
    band: str                # "g" | "r" | "i" | "T" | "G" | "BP" | "RP" | "V"
    time: np.ndarray         # BJD_TDB, float64
    value: np.ndarray        # magnitude (AB for ZTF, Vega G for Gaia, V/g for ASAS-SN) or normalised flux for TESS
    error: np.ndarray
    quality: np.ndarray      # survey-native flag, int32; 0 means good after clean()
    value_kind: str          # "mag" | "flux"
    meta: dict               # zero-point offsets applied, original time system, fetch date
```

One `LightCurve` per (Target, survey, band). Cache path:
`$CACHE_DIR/{survey}/{gaia_source_id}/{band}.parquet`, written with pyarrow; `meta` goes in
the Parquet file metadata. The Cache is re-downloadable and never the system of record;
`light_curve_files` indexes it.

Fetchers convert archive output to `LightCurve` at the boundary. Raw TESS cutouts are
deleted in the same job that extracted the light curve.

### 5.1 Per-survey fetch contracts

- **ZTF** (IRSA light-curve service, position query, radius ≈ 1.5″): returns several
  `oid`s per star (per field, CCD, filter). Group by filter, compute the median magnitude
  offset of each oid relative to the oid with the most points, apply the offset and record
  it in `meta`; flag a Target `blend_suspect` if any offset exceeds 0.05 mag. Keep
  `catflags == 0`. ZTF saturates near 12.5–13 mag; the 13–16 Slice avoids this.
- **TESS** (lightkurve via MAST): prefer prebuilt QLP or TGLC light curves, else SPOC;
  fall back to a TESScut cutout with a simple aperture only when nothing prebuilt covers
  the star. Which source wins per magnitude is resolved in the prototype (handover open
  item). Normalise flux per sector and stitch; keep `quality == 0`.
- **Gaia DR3 epoch photometry** (DataLink, `Gaia.load_data(..., retrieval_type=
  "EPOCH_PHOTOMETRY")`): G, BP, RP per transit; drop `rejected_by_photometry` and
  `rejected_by_variability`. Fetched per Slice in batches of IDs, not per star.
- **ASAS-SN** (Sky Patrol v2 client): V and g; keep the quality flag the client marks good.
  Long baseline is its value; its cadence rarely settles short periods alone.

Per-archive concurrency is a local semaphore in `fetch/base.py` (defaults: IRSA 2, MAST 2,
Gaia 1, ASAS-SN 2) with exponential backoff on HTTP 429/5xx and a per-Target
`max_attempts` of 3. Gaia catalog data (colour, parallax, vari_*, neighbours) is **not** a
fetch Job: `select-slice` fetches it for the whole Slice in one ADQL upload join.

## 6. Analysis

Order of operations in `analysis/worker.py` for one Target:

1. Load all `LightCurve`s for the Target from the Cache; skip surveys with `status != ok`.
2. **Time:** everything is already BJD_TDB because fetchers convert at the boundary, using
   `analysis/time.py`: ZTF MJD (UTC) → BJD_TDB with the Target's coordinates; TESS BTJD
   → BJD_TDB by adding 2457000; Gaia TCB → JD by adding 2455197.5, then TCB→TDB via
   astropy; ASAS-SN HJD (UTC) → BJD_TDB via astropy. `time.py` has a test against a
   published epoch of a known eclipsing binary to within 1 s.
3. **Clean** (`clean.py`): drop flagged points, sigma-clip at 5σ around a running median,
   require ≥ 30 points per band to proceed.
4. **Periodogram** (`periodogram.py`): astropy `LombScargle` per band over 0.05–100 d
   (grid oversampled ×10), plus astropy `BoxLeastSquares` over 0.1–30 d for eclipsers.
   Record the top five peaks with power and false-alarm probability per band. Gaia's
   published `vari_*` period is the first hypothesis: it is always scored and plotted.
5. **Aliases** (`aliases.py`): for each candidate test P, 2P, P/2 and the 1 d, 0.5 d and
   sidereal-day (0.99727 d) aliases; for TESS also 13.7 d orbital systematics. Prefer the
   candidate whose phase-folded scatter is lowest and record the ranking.
6. **Agreement** (`agreement.py`): accept a period when at least two of {ZTF g, ZTF r,
   ASAS-SN, TESS, Gaia published period} agree within 0.1 % (or at a 2P/P/2 relation,
   recorded as such). Gaia epoch photometry alone never settles a period.
7. **Blend** (`blend.py`): from `targets.neighbours`, flag `blend_suspect` when a neighbour
   within 3″ is brighter than G + 2, or when ZTF oid offsets exceeded 0.05 mag.
8. **CMD** (`cmd.py`): absolute G from parallax when `parallax / parallax_error > 5`,
   BP−RP; record the position and the nearest classical instability-strip or
   main-sequence region label.
9. Write the `analyses` row; hand off to `classify.propose` and `plots`.

## 7. Classifier

Rules are Python objects (grilling decision):

```python
class Rule(Protocol):
    name: str
    applies_to: frozenset[str]            # VSX type codes this rule bears on
    def evaluate(self, a: Analysis, t: Target) -> Evidence  # verdict: supports|against|neutral, weight 0..1, explanation: str
```

`registry.py` lists rules per VSX type (initial set: EW, EA, EB, RRAB, RRC, DSCT, HADS,
DCEP, SR, L, M, ROT, ELL, and "not variable"). `confidence.py` combines evidence per type
as (sum of supporting weights − sum of opposing weights) / (sum of all weights), clipped
to 0–1; the Proposal takes the best-scoring type and keeps the full evidence list for
every type that scored. A Target with no Agreement produces a Proposal of type `VAR`
(unchanged) with low Confidence so Tom can see why it failed. Explanations are one
sentence each and are shown verbatim in the review app and copied into Submission
reference text when relevant.

Thresholds and ranges live in `classify/types.py` and are part of what
`PIPELINE_VERSION` stamps. Verdict feedback (section 9.3) changes rules by hand; this
project does not automate that.

## 8. Plots

PNG via matplotlib, written to `$PLOTS_DIR/{gaia_source_id}/{pipeline_version}/`:
`phase-{survey}-{band}.png` (folded at the Proposal period, two cycles, binned median
overlaid), `periodogram.png` (all bands, candidate and alias markers),
`cmd.png` (Target on a Gaia background drawn from a committed subsample), and
`submission.png` (the single phase plot chosen for the VSX form: the band with the most
points unless Tom's Review picks another). Paths are recorded in `analyses.plots`. Plots
are disposable and regenerated by `argelander classify --replot`.

## 9. Review app

FastAPI + Jinja2 + htmx, no auth beyond the tailnet (grilling decision). Routes:

| Route | Purpose |
| --- | --- |
| `GET /` | queue of `queued` Proposals sorted by Confidence desc; filters: type, min confidence, blend flag |
| `GET /proposals/{id}` | evidence (all plots, rule explanations, per-survey periods, alias ranking, CMD) beside the Proposal fields and VSX's current values; shows a diff to any earlier Proposal for the Target |
| `POST /proposals/{id}/review` | body: decision, edited fields, notes (required); writes `reviews`, sets Proposal status; on approve creates a `prepared` Submission |
| `GET /targets/{id}` | history: Proposals, Reviews, Submissions |
| `GET /submissions` | tracker: `prepared` → `submitted` → verdict, with one-per-day pacing shown |
| `GET /submissions/{id}` | paste-ready form fields, reference text, plot download |
| `POST /submissions/{id}/submitted` | mark submitted (date) |
| `POST /submissions/{id}/verdict` | accepted / rejected + reason + tags |
| `GET /healthz` | DB ping; watched by Uptime Kuma |

Templates are server-rendered; htmx swaps the review form and queue rows. Static assets
(htmx, a small CSS file) are vendored, no build step.

### 9.1 Submission tracker

`submission/form.py` renders the VSX revision-form fields from the Proposal and Review:
name, position, type, period, epoch (converted to the form's expected HJD/JD convention),
max and min magnitude with band stated (ZTF g/r AB, ASAS-SN V or g, Gaia G), and a
reference block listing the surveys, date ranges and the Pipeline Version. **The exact
field list is confirmed against the live form after VSX registration** (a human ticket
in milestone 1) and the renderer is adjusted then.

### 9.2 Pacing

Moderators review one submission per person per day. The tracker shows the count of
`submitted` items awaiting a Verdict and recommends the next one by Confidence.

### 9.3 Verdict feedback

Rejection reasons are stored verbatim with tags (`period`, `type`, `blend`, `evidence`,
`format`, `other`). Rule changes in response are manual code changes that bump
`PIPELINE_VERSION`.

## 10. Infrastructure

See handover: Infrastructure and deployment, Storage budget, Container notes. Additions
from the grilling session:

- `compose.yaml` services: `postgres` (16, data on internal SSD volume, `mem_limit`),
  `api`, `fetcher`, `analyzer`, `backup`, `tailscale`. The `api` service uses
  `network_mode: service:tailscale` so it is reachable only on the personal tailnet.
  `fetcher` and `analyzer` mount `$CACHE_DIR`, `$PLOTS_DIR` and `$ASTRO_CACHE_DIR` from the
  USB SSD. Memory limits: postgres 1 GB, analyzer 3 GB, fetcher 1 GB, api 512 MB.
- `compose.dev.yaml` adds `jupyter` (same image plus jupyterlab, mounts `notebooks/` and
  the cache) and exposes postgres on localhost; dev runs through `task up`.
- `backup`: an image with `postgresql-client` and `restic`, run by supercronic at 03:30
  local: `pg_dump | restic backup --stdin --stdin-filename argelander.sql` to a B2 bucket,
  then `restic forget --keep-daily 14 --keep-weekly 8 --prune`. A documented restore drill
  (`deploy/restore.md`) is executed once as a ticket.
- USB SSD mounted by UUID with `nofail` and a `RequiresMountsFor=` systemd override on
  docker.service, documented in `deploy/`; the fetcher refuses to start if `$CACHE_DIR`
  is not a mount point (guards against writing to the boot disk).
- Deploy: `git pull && docker compose pull/build && docker compose up -d` wrapped as
  `task deploy`, run over SSH by Tom. Not Coolify.
- Monitoring: JSON logs to stdout; Uptime Kuma (personal instance on the mini) checks
  `/healthz` over the tailnet; the backup job logs success/failure. No error tracker.

## 11. Testing and CI

- pytest; tests run fully offline. `tests/fixtures/` holds trimmed real Light Curves as
  Parquet for the prototype set (≈ 20 well-characterised variables of the initial rule
  types plus negatives: known blends, 1-day-alias traps, constant stars), capped at a few
  MB total. A fixture manifest records each star's published period, type and source.
- Tests marked `live` hit archives and are skipped in CI (`-m "not live"`).
- The prototype pass criterion is a test: derived periods match published values within
  0.1 % (or a recorded 2P relation) and negatives produce no Agreement.
- GitHub Actions on push and PR: `uv sync`, `ruff check`, `ruff format --check`, `pytest
  -m "not live"`, `alembic` single-head check, `docker build`. Node is not involved.
- Pre-commit runs ruff format + check; commit normally.

## 12. Conventions

Conventional commits with the Linear ID parenthetical; one ticket = one PR to `main`.
Stage files by name. Secrets only in `.env` (gitignored) and the mini's Compose env. No
third-party API call in a request path of the review app: it reads Postgres and the plot
directory only.

## 13. Milestones (confirmed in Phase 2)

1. **Groundwork** — VSX registration and reading (human); package skeleton, Dockerfile,
   dev Compose with postgres + jupyter, CI, Taskfile.
2. **Prototype science** — LightCurve + Cache, time conversion, four fetchers, periodogram,
   aliases, Agreement, blend, CMD, plots, fixtures, the notebook proving ~20 known stars;
   per-star storage measured.
3. **First manual submissions** — two or three Submissions straight from the notebook
   (human); the evidence format moderators expect is captured into this spec.
4. **CLI pipeline** — schema and Alembic, jobs queue, select-slice (VizieR + Gaia join),
   fetch and analyze workers, classifier rules and Confidence, Proposal writing and
   supersession, CLI.
5. **Mac mini deployment** — prod Compose, memory limits, USB mount guard, tailscale
   sidecar, backup service and restore drill, Uptime Kuma check, deploy task.
6. **Review app** — queue, evidence page, review form, target history, healthz.
7. **Submission tracker and first acceptance** — form renderer, tracker pages, Verdict
   recording; Tom submits from the Draco Slice until one is accepted (human).

## 14. Resolved in the prototype, not here

- TESS source per star (QLP vs TGLC vs cutout) by magnitude.
- Real per-star Cache size (handover estimates 0.5–1.5 MB).
- Whether ASAS-SN g or V is more useful at 13–16 mag.
- The Draco VAR Target count at 13–16 mag (one VizieR query before milestone 4).
