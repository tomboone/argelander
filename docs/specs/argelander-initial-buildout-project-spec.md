# Argelander: Initial Buildout — project spec

**Linear project:** Argelander: Initial Buildout (P-NEU-98), initiative Argelander (I-10)
**Repo:** `tomboone/argelander` (single repo)
**Design source:** `docs/handover.md` (settled design), `CONTEXT.md` (glossary), `docs/adr/` (decisions),
and VSX's own rules captured 2026-10-19 in `docs/reference/`: `vsx-submission-rules.md`
(notice, FAQ, Manual), `vsx-revision-form.md` (form fields and vocabularies),
`vsx-type-ranges.md` (type definitions). Where this spec and the reference disagree, the
reference wins and the spec is wrong.
**Written:** 2026-10-19, from the `/projectit` Phase 1 grilling session; amended the same day
after Tom registered with VSX and read the submission notice

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
  cli.py               typer app: select-slice, fetch-worker, analyze-worker, classify, enqueue, status,
                       recompute-status, web, db-upgrade
  status.py            Target status derived from Jobs and Proposals; recompute, counts, re-queue (NEU-1722)
  db/
    models.py          SQLAlchemy 2 declarative models (section 4)
    session.py         sync engine/session for workers, async engine/session for FastAPI (psycopg 3)
    queue.py           enqueue(), claim(), complete(), fail(), release_stale()
  alembic/             migrations (env.py reads DATABASE_URL from config)
  catalog/
    vsx.py             VizieR B/vsx slice query via astroquery
    gaia.py            table-upload joins per Slice chunk (gaia_source + vari_*, 2MASS, neighbours, TESS field)
    constellation.py   astropy get_constellation + Slice bounds
  selector.py          select-slice: VSX + Gaia join → slices, targets, fetch Jobs
  lightcurve.py        LightCurve dataclass + Parquet read/write (section 5)
  fetch/
    base.py            Fetcher protocol, per-archive semaphore, retry with backoff
    ztf.py  tess.py  gaia_epoch.py  asassn.py
    worker.py          fetch-worker loop
  analysis/
    time.py            every survey → BJD_TDB
    clean.py           survey quality flags → cleaned LightCurve
    periodogram.py     Lomb-Scargle per band + BoxLeastSquares
    model.py           Analysis, PeriodogramResult, Peak: plain dataclasses M4 maps to the ORM
    aliases.py         P, 2P, P/2, 1 d, 0.5 d, sidereal-day, TESS 13.7 d tests
    agreement.py       the Agreement rule
    blend.py           Gaia neighbours within a few arcsec
    cmd.py             colour-magnitude position from Gaia colour + parallax
    pipeline.py        analyze(): steps 3–8 for one Target, shared by the worker and the prototype
    worker.py          analyze-worker loop (writes Analysis, then runs classify + plots)
  prototype.py         the prototype gate's pass criterion, shared by its test and the notebook
  classify/
    types.py           VSX type codes and definitions used by rules
    rules/             one module per rule; each a Rule subclass
    registry.py        rules per VSX type
    confidence.py      normalised weighted sum
    propose.py         Analysis → Proposal (and supersede logic)
  plots/
    phase.py  periodogram.py  cmd.py  combined.py   matplotlib Figures, no pyplot; PNG to PLOTS_DIR
    render.py          render_all(): every plot of one Analysis, submission.png under 1 MB
    data/              committed Gaia DR3 background for cmd.png
  web/
    app.py             FastAPI, Jinja2 templates, htmx; routes in section 9
    templates/  static/
  submission/
    form.py            Proposal + Review → VSX form field values and reference text
                       (passbands and period precision first, NEU-1709: the plots print them)
    tracker.py         Submission lifecycle and Verdict recording
tests/                 pytest; tests/fixtures/*.parquet (section 11)
notebooks/             prototype notebooks; import from argelander, never define science here
compose.yaml           prod stack (section 10); compose.dev.yaml adds jupyter and dev mounts
Dockerfile             python:3.12-slim, uv, non-root
Taskfile.yml           go-task: up, down, shell, hooks, test, lint, format, typecheck, migrate, deploy, backup:check
deploy/                fstab line, systemd override, tailscale sidecar notes, restore drill
```

### 3.2 Stack

Python 3.12. uv + `pyproject.toml`, ruff (lint + format), ty (type checking). Type hints
`X | None`, never `Optional`. No `from __future__ import annotations`. Typer for the CLI.

ty was chosen on 2026-10-19 over mypy and pyright: it is Astral's checker, so it fits the
uv + ruff stack and runs fast enough for a hook, and pyright would pull a Node runtime into
a Node-free repo. ty is pre-1.0, so `unresolved-import` is silenced per module for the
thinly typed astronomy libraries (astropy, astroquery, lightkurve), never globally. If the
beta blocks real code with false positives, the fallback is mypy, recorded here as an
amendment rather than swapped silently. SQLAlchemy 2 +
Alembic on psycopg 3 (sync in workers, async in FastAPI). astropy, astroquery,
lightkurve, pyarrow, numpy, matplotlib, FastAPI, Jinja2, htmx (vendored static file).
pydantic-settings for config. structlog or stdlib logging to stdout as JSON lines. No
pandas or polars in the core package (notebooks may use pandas for display).

*Amended 2026-10-21 (NEU-1717):* stdlib logging it is: `argelander/logs.py` holds a JSON
formatter (time, level, logger, message, the `extra=` fields, the traceback) and
`configure_logging()`, which each long-running service calls once at startup; `fetch-worker`
is the first.

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
| `targets` | one Target per VSX entry | `slice_id`, `vsx_oid` (unique), `vsx_name`, `gaia_source_id` (unique), `ra`, `dec`, `vsx_type`, `vsx_period`, `vsx_mag_max`, `vsx_mag_min`, `vsx_band`, `gaia_g`, `gaia_bp_rp`, `parallax`, `parallax_error`, `gaia_vari_class`, `gaia_vari_class_score`, `gaia_vari_period`, `gaia_vari_detail` jsonb, `neighbours` jsonb (sources within 20″: id, sep, G), `status` |
| `light_curve_files` | Cache index | `target_id`, `survey` (`ztf`/`tess`/`gaia`/`asassn`), `source_key` (survey-native id(s)), `path`, `n_points`, `t_min_bjd`, `t_max_bjd`, `bands` text[], `fetched_at`, `fetcher_version`, `status` (`ok`/`empty`/`failed`), `error` |
| `jobs` | the queue | `kind` (`fetch`/`analyze`), `target_id`, `status` (`pending`/`claimed`/`done`/`failed`), `claimed_at`, `attempts`, `max_attempts` default 3, `last_error`, `payload` jsonb, `finished_at`; index on `(kind, status, created_at)` |
| `analyses` | one Analysis per Target per Pipeline Version | `target_id`, `pipeline_version`, `inputs` jsonb (per survey: n points, date range, bands, light_curve_file ids), `periods` jsonb (per band: best period, power, FAP, top-5 peaks), `alias_tests` jsonb, `agreement` bool, `agreed_period`, `agreed_sources` text[], `agreement_relation` (`1:1`/`2:1`, null without Agreement), `blend` jsonb, `cmd` jsonb (abs G, BP−RP, distance), `plots` jsonb (kind → path), `started_at`, `finished_at`; unique `(target_id, pipeline_version)` |
| `proposals` | one Proposal per Analysis | `analysis_id`, `target_id`, `pipeline_version`, `proposed_type` (a code from the VSX dropdown vocabulary), `uncertain` bool, `period`, `epoch_bjd`, `eclipse_duration_pct` nullable, `mag_max`, `mag_min`, `min_is_amplitude` bool, `band` (VSX passband code), `confidence` numeric(4,3), `evidence` jsonb (list of rule name, verdict, weight, explanation), `status` (`queued`/`superseded`/`approved`/`rejected`), `superseded_by` nullable FK |
| `reviews` | Tom's Review of a Proposal | `proposal_id`, `decision` (`approve`/`edit`/`reject`), `edited` jsonb (field → new value, when decision = edit), `notes` text not null, `reviewed_at` |
| `submissions` | a Submission and its Verdict | `proposal_id`, `review_id`, `form_fields` jsonb, `reference_text`, `plot_path`, `status` (`prepared`/`submitted`/`accepted`/`rejected`), `submitted_at`, `verdict_at`, `verdict_reason` text, `verdict_tags` text[] |

*Amended 2026-10-21 (NEU-1713):* `targets` also has `tess_field` jsonb (the 120″ count,
brightest G and combined G, §5.1), and `submissions.status` includes `needs_changes` (§9.2).
Each fixed vocabulary is varchar with a CHECK constraint, not a Postgres enum type, so a new
value is a constraint swap rather than `ALTER TYPE`; in Python each is a `StrEnum` in
`db/models.py`. Every `target_id`, `slice_id`, `analysis_id` and `proposal_id` foreign key is
indexed. `light_curve_files.source_key` and `path` are null on an `empty` or `failed` row.
Migrations live in `src/argelander/alembic/`; `argelander db-upgrade` (`task migrate`) applies
them and `tests/test_db.py` runs the equivalent of `alembic check` against a throwaway
database.

*Amended 2026-10-21 (NEU-1714):* `jobs` also has a partial unique index,
`uq_jobs_kind_target_id_active`, on `(kind, target_id)` where `status` is `pending` or
`claimed`. `queue.enqueue` inserts with `ON CONFLICT DO NOTHING` against it, so enqueueing a
Target that already has an active Job of that kind is a no-op, race-free; once that Job is
`done` or `failed` the Target can be enqueued again. The queue functions never commit: a
worker commits straight after `claim`, so the claim survives a crash for `release_stale` to
recover.

*Amended 2026-10-21 (NEU-1716):* `targets` also has `vsx_mag_min_is_amplitude` bool, VizieR's
`f_min` flag: without it an amplitude would be stored as a magnitude at minimum. The Gaia
JSONB columns hold a `GaiaRecord`'s fields by their dataclass names, so a later Stage can
rebuild the record without asking Gaia again: `neighbours` is a list of `{source_id,
separation, g}`, `tess_field` is `{n_sources, brightest_g, combined_g}`, and
`gaia_vari_detail` is `{in_vari_summary, vari_g_trimmed_range, vari_subclass, vari_periods,
ruwe, cross_ids}`, which also carries RUWE and the 2MASS designation, since this table has no
column for either. `select-slice` (`argelander/selector.py`) writes one `slices` row per run and
upserts Targets on `vsx_oid`: a rerun refreshes the Gaia columns only, and a Target keeps the
Slice that first selected it, its VSX columns and its status, so a later Slice's `n_targets`
counts the Targets that run selected, not the ones whose `slice_id` it is. It queues a fetch
Job only for a Target still `selected`, so a rerun does not fetch again what the fetcher has
already taken. When VSX lists one Gaia source under two OIDs, `gaia_source_id` being unique,
the Target already stored for the source keeps it, across runs too; otherwise the lower OID
does.

*Amended 2026-10-21 (NEU-1717):* `light_curve_files` is unique on `(target_id, survey)`, and
the fetch worker (`argelander/fetch/worker.py`) upserts on it, so a re-fetch replaces the row.
`path` is the survey's Cache directory for the Target relative to `CACHE_DIR`
(`{survey}/{gaia_source_id}`), not one file: a row covers every band in `bands`. `source_key`
joins the bands' source keys with "+", and `n_points`, `t_min_bjd` and `t_max_bjd` span all
of them. `fetcher_version` is `FETCHER_VERSION` in the worker, bumped whenever a fetcher
changes what it writes. The worker clears the directory before writing it, whatever the
outcome, and again when a write fails partway, so an `empty` or `failed` row never sits beside
band files. The files are written before the transaction commits: a commit that fails leaves
them ahead of their rows until the Job's next attempt rewrites both. A re-fetch that fails
for one survey discards that survey's earlier files with its row; the Cache is re-downloadable.

*Amended 2026-10-21 (NEU-1718):* `analyses` also has `agreement_tolerance` (float, null
without Agreement), the tolerance §6 step 6 judged the votes at, which varies per Analysis
and had no column. The analyze worker (`argelander/analysis/worker.py`) fills the JSONB
columns nested by survey and then band: `inputs` is survey → `light_curve_file_id`,
`n_points`, `t_min_bjd`, `t_max_bjd` and `bands` from that survey's `ok` row; `periods` is
survey → band → method (`lomb_scargle`, `bls`) → `n_points` after cleaning, `insufficient`,
the five `peaks` (period, power, FAP; strongest, so the best, first) and the `gaia` score; and
`alias_tests` is survey → band → `baseline` and the whole `ranking` of hypotheses, best
first. `blend` and `cmd` hold the `Blend` and `CmdPosition` dataclasses field by field.
Periodogram arrays are not stored; a plot redraws them from the Light Curves. NaN, which
JSONB refuses, is stored as JSON null inside a column; a column with no value (`plots`
before the hand-off, `blend` and `cmd` without a Gaia record) is SQL NULL, as every JSONB
column's Python None now is.

**Target status** is derived for display, not stored as truth: it follows the newest Job
and Proposal. A stored `status` column on `targets` is a cache updated by the workers
(`selected` → `fetching` → `fetched` → `analyzed` → `proposed` → `reviewed` →
`submitted` → `accepted`/`rejected`, plus `failed`), and may be recomputed from the other
tables by a CLI command.

*Amended 2026-10-21 (NEU-1722):* the rule is `status.derive` (`argelander/status.py`), and
`argelander recompute-status` rewrites the column from it. The Job that decides is the Target's
newest pending or claimed Job, or else its newest. A `failed` one makes the Target `failed`; a
pending or claimed analyze Job leaves it `fetched`; a claimed fetch Job, or a pending one
already tried, is `fetching`, and one never tried is `selected`, as is a Target with no Job; a
`done` fetch Job is `fetched`. After a `done` analyze Job the newest Proposal decides:
`proposed`, then `reviewed` once it has a Review or is approved or rejected, then its newest
Submission's status (`prepared` is still `reviewed`, `needs_changes` is `submitted`). The
workers write the same answers, so a clean run recomputes to no change; the one place they
differ is a crash leftover, a fetch claim released as stale (`selected`, where the worker left
`fetching`). `argelander enqueue <fetch|analyze>` re-queues by `--target` (VSX name) or
`--slice` (name, every `slices` row of it; a Slice's Targets are the ones it selected first,
§4 NEU-1716), narrowed or, alone, extended to every Slice by `--failed` (the newest Job of
that kind failed for good). It skips a Target with a Job of *either* kind pending or claimed,
since `queue.enqueue` keeps one active Job per kind only: a fetch queued beside a waiting
analyze Job would finish without queueing its own analysis. It recomputes the status of every
Target it names.
`argelander status [--slice]` counts Targets per status and Jobs per kind and status.

**Rerun semantics** (grilling decision): a rerun under a new Pipeline Version inserts a new
`analyses` row and a new `proposals` row. Any older Proposal for the same Target with
status `queued` is set to `superseded` with `superseded_by` pointing at the new row. A
Proposal with status `approved` or `rejected`, or one referenced by a Submission, is never
modified; the review app shows the newer Proposal beside it with a field-level diff.
Reruns under the *same* Pipeline Version overwrite the existing Analysis row (idempotent
stages, handover: Design principles).

*Amended 2026-10-21 (NEU-1721):* a same-version rerun replaces its Analysis's own `queued`
Proposal in place too, rather than superseding it, as the Analysis row itself is replaced.
Only when that Proposal has been reviewed, or a Submission references it, does a second
Proposal join the same Analysis, queued beside it; the reviewed one keeps its fields, though
its Analysis row and its version's plot directory now hold the rerun's. `classify.propose` moves the Target
to `proposed`. `analyses.plots` records each plot's path relative to `PLOTS_DIR`, as
`light_curve_files.path` is relative to `CACHE_DIR`. The `evidence` items also carry the
`type_code` each bears on, since the list holds every scored type's Evidence.

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

*Amended 2026-10-20 (NEU-1798):* `LightCurve` gains a last field, `oid: np.ndarray | None =
None`, the int64 survey-native object id of each point. ZTF fills it; the other surveys
leave it `None`. It is a fifth Parquet column when present, and a Cache file without the
column reads back `oid=None`, so files written before it still load. `clean()` and the
fixture trim subset it with the four arrays. NEU-1799 adds TESS's `sap` in the same shape:
an optional per-point column, not a list in `meta`, which `clean()` does not subset.

*Amended 2026-10-21 (NEU-1799):* `LightCurve` gains a second optional last field, `sap:
np.ndarray | None = None`, the float64 SAP flux of each point, normalised like `value` and
kept for the PDCSAP comparison (§5.1). TESS fills it; the other surveys leave it `None`. It
was a JSON list in `meta["sap"]`, which took 64 % of a large TESS file and fell out of step
with the arrays after `clean()`, which subsets the arrays but copies `meta` whole. It is now a
Parquet column after `oid`, written when present; a Cache file without the column reads back
`sap=None`, so files written before it still load. A pre-amendment TESS file would keep its
old list in `meta`, which nothing reads, and so lose its SAP until re-fetched; none exist
outside the fixtures, because the fetch worker (NEU-1717) comes after this change. `clean()` and the fixture trim subset it with the
other arrays. The TESS fixtures were migrated in place, the list moved into the column, not
re-fetched: their four arrays are byte-for-byte unchanged and the prototype gate's results
are identical.

Fetchers convert archive output to `LightCurve` at the boundary. Raw TESS cutouts are
deleted in the same job that extracted the light curve.

### 5.1 Per-survey fetch contracts

- **ZTF** (IRSA light-curve service, position query, radius ≈ 1.5″): returns several
  `oid`s per star (per field, CCD, filter). Group by filter, compute the median magnitude
  offset of each oid relative to the oid with the most points, apply the offset and record
  it in `meta`; flag a Target `blend_suspect` if any offset exceeds 0.05 mag. Keep
  `catflags == 0`. ZTF saturates near 12.5–13 mag; the 13–16 Slice avoids this.

  *Amended 2026-10-20 (NEU-1798):* the fetcher no longer flags anything. The median shift
  is a first alignment, good enough for the periodogram, and is still recorded in
  `meta["zero_point_offsets"]`, but a variable star's oids sample its cycle unevenly, so
  their medians differ with identical zero points; the old rule fired on 11 of the 20
  prototype positives. Each point's oid is kept in `LightCurve.oid`, and the blend
  judgement moves to §6 step 7, which compares the oids at equal phase.
- **TESS** (lightkurve via MAST): prefer prebuilt QLP or TGLC light curves, else SPOC;
  fall back to a TESScut cutout with a simple aperture only when nothing prebuilt covers
  the star. Which source wins per magnitude is resolved in the prototype (handover open
  item). Use the PDCSAP flux column by default but keep SAP as well; VSX warns that
  PDCSAP sometimes over-corrects and removes real features or mean-level changes, so the
  analyzer compares the two and falls back to SAP when PDCSAP has removed variance that
  SAP shows at the candidate period. Normalise flux per sector and stitch; keep
  `quality == 0`. The TESS photometric aperture is ~120″, so TESS data for a Target with
  a comparably bright Gaia neighbour inside that radius is flagged `tess_contaminated`
  and carries reduced weight in Agreement.

  *Amended 2026-10-19 (NEU-1702):* only SPOC (and TESS-SPOC, the same pipeline on
  full-frame images, preferred last) publishes PDCSAP. QLP's KSPSAP and TGLC's `cal_*`
  fluxes are detrended for transit searches and erase variable-star signals, so `value`
  is QLP's `sap_flux` and TGLC's `psf_flux`, with `meta["sap"]` holding QLP's `sap_flux`
  and TGLC's `aperture_flux`. A cutout's simple aperture flux fills both. The winning
  author's sectors are the only ones kept, so `meta["source"]` names one pipeline per
  Target. Prebuilt products are downloaded to the same temporary directory as cutouts and
  deleted with it.

  *Amended 2026-10-21 (NEU-1799):* the SAP flux this paragraph puts in `meta["sap"]` is the
  `LightCurve.sap` column instead (§5); `meta["sap_column"]` still names its source column.
- **Gaia DR3 epoch photometry** (DataLink, `Gaia.load_data(..., retrieval_type=
  "EPOCH_PHOTOMETRY")`): G, BP, RP per transit; drop `rejected_by_photometry` and
  `rejected_by_variability`. Fetched per Slice in batches of IDs, not per star.

  *Amended 2026-10-19 (NEU-1703):* the archive now refuses astroquery's default
  `data_structure` (`INDIVIDUAL`), and its per-transit replacement, `DATAMODEL_STANDARD`,
  has a `rejected_by_photometry` column that covers G only; BP and RP rejections sit in
  opaque `*_other_flags`, and some still carry a magnitude. The fetcher requests
  `DATAMODEL_GAIA` (one row per source, transits in arrays) in batches of 100 and drops,
  per band: G transits with a null flux (exactly the archive's `rejected_by_photometry`),
  `photometry_flag_{bp,rp}_reject`, `variability_flag_{g,bp,rp}_reject`, and any transit
  without a time, magnitude or positive flux signal-to-noise. Magnitude errors are
  2.5 / ln 10 over the published flux signal-to-noise.
- **ASAS-SN** (Sky Patrol v2 client): V and g; keep the quality flag the client marks good.
  Long baseline is its value; its cadence rarely settles short periods alone.

  *Amended 2026-10-19 (NEU-1704):* the fetcher calls Sky Patrol's HTTP API with `requests`
  instead of the `pyasassn` client, whose untimed requests, `multiprocessing` downloads and
  catch-all retry loop do not fit the fetch worker; the client stays the reference for the
  calls. A 3″ cone search of `master_list`, then the light-curve block from a server that
  `get_block_servers` names. The target nearest the position wins. Kept points have
  `quality == "G"` and `mag_err` below 99 (non-detections are upper limits with
  `mag_err` 99.99); the band comes from the camera by the client's camera lists. An empty
  server list (maintenance) is a transient failure. The service was down while this was
  built, so the offline test replays a constructed response in the documented schema;
  recording a real one, the first live fetch and the §14 V-or-g note follow in NEU-1764.

Per-archive concurrency is a local semaphore in `fetch/base.py` (defaults: IRSA 2, MAST 2,
Gaia 1, ASAS-SN 2) with exponential backoff on HTTP 429/5xx and a per-Target
`max_attempts` of 3. Gaia catalog data (colour, parallax, vari_*, neighbours) is **not** a
fetch Job: `select-slice` fetches it for the whole Slice through ADQL upload joins.

*Amended 2026-10-21 (NEU-1717):* each pass of the fetch worker (`argelander fetch-worker`)
releases stale claims, then claims up to `FETCH_BATCH_SIZE` fetch Jobs (default 10, so a
batch, paced by TESS's two MAST downloads at a time, ends well inside the 30-minute stale
window) in one transaction, moving their Targets to `fetching`. It runs ZTF, TESS and ASAS-SN once per
Target and Gaia's `fetch_many` once for the whole batch, all at once under the semaphores.
It then records each Target in its own transaction: an exception from one survey makes only
that survey's row `failed`, with the error, and the Job is `done`, the Target `fetched` and
an `analyze` Job queued. When every survey failed, `queue.fail` sends the Job back to
`pending`, or to `failed` with the Target once it has used its `max_attempts`; so does an error
outside any survey while recording a Target, which leaves the batch's other Targets to go on.
With nothing to claim, or after a pass that failed outright (Postgres restarting), it sleeps
`FETCH_POLL_INTERVAL` seconds (default 60) and tries again rather than exiting. It refuses to start when
`CACHE_DIR` is not a mount point unless `ALLOW_UNMOUNTED_CACHE=1`, which `compose.dev.yaml`
sets for the dev containers; `.env.example` says 0, so a prod `.env` copied from it keeps the
guard (§10).

**Per-survey contamination radii** (from VSX's submission notice): Gaia ~0.7″, ZTF ~2″,
ASAS-SN ~17″, TESS ~120″ and larger for bright stars. The Gaia neighbour query therefore
returns every source within 20″ with its separation and G, plus a count and the brightest
G of sources within 120″, so each survey's blend risk can be judged against its own
aperture. Survey useful ranges: ZTF g/r 13–21, ASAS-SN V 10–16.5, Gaia G 5–21, TESS
roughly 2–15. The 13–16 mag Slice sits inside all of them.

The same catalog join also returns the 2MASS designation via
`gaiadr3.tmass_psc_xsc_best_neighbour` joined to `gaiadr1.tmass_original_valid`, and the
UCAC4 id when a Gaia-hosted cross-match exists, because VSX prefers those over Gaia
names in Other names (Manual, §IV.a and §V.b).

*Amended 2026-10-20 (NEU-1705):* the join is four upload queries per chunk of 1,000 IDs,
not one, because of how the archive treats anonymous async jobs: they are aborted after
60 s, results are cut at 50,000 rows without warning, the 2MASS tables left-joined into
the catalog query stall it, and a cone join is fast only with the circle on the archive's
side (`CONTAINS(POINT(target), CIRCLE(neighbour))`). The queries are the catalog (colour,
parallax, RUWE, `vari_*`), the 2MASS designation, neighbours within 20″, and the 120″ TESS
field as a count, brightest G and combined G (the summed flux §6 step 7 tests). The 2MASS
designation comes from `tmass_psc_xsc_join.original_psc_source_id`; joining
`gaiadr1.tmass_original_valid` on it times out and adds nothing. No Gaia-hosted table maps
DR3 sources to UCAC4 (only `gaiadr1.ucac4_best_neighbour`, keyed by DR1 IDs), so there is
no UCAC4 id. Gaia's period per Target is taken from the most trusted table that has one:
RR Lyrae, then Cepheid, then eclipsing binary (1 / frequency), then short timescale.

## 6. Analysis

Order of operations in `analysis/worker.py` for one Target:

1. Load all `LightCurve`s for the Target from the Cache; skip surveys with `status != ok`.
2. **Time:** everything is already BJD_TDB because fetchers convert at the boundary, using
   `analysis/time.py`: ZTF MJD (UTC) → BJD_TDB with the Target's coordinates; TESS BTJD
   → BJD_TDB by adding 2457000; Gaia TCB → JD by adding 2455197.5, then TCB→TDB via
   astropy; ASAS-SN HJD (UTC) → BJD_TDB via astropy. `time.py` has a test against a
   published epoch of a known eclipsing binary to within 1 s.

   *Amended 2026-10-19 (NEU-1699):* no eclipsing-binary epoch published in both HJD (UTC)
   and BJD_TDB could be found, and Eastman's online converter, which papers use for that
   conversion, is offline. The ZTF and ASAS-SN tests use barycorrpy's published UTC →
   BJD_TDB reference for τ Ceti instead (Kanodia & Wright 2018; an independent
   implementation on JPL DE430). ASAS-SN's input HJD is built from the Astronomical
   Almanac's low-precision Sun, not from astropy. TESS is checked against the BTJD
   definition and Gaia against IAU 2006 Resolution B3. All agree within 1 s; ZTF and
   ASAS-SN agree within 0.05 s.
3. **Clean** (`clean.py`): drop flagged points, sigma-clip at 5σ around a running median,
   require ≥ 30 points per band to proceed.

   *Amended 2026-10-20 (NEU-1706):* the running median is taken over the two points either
   side of each point, leaving the point itself out, and a point is judged only when those
   four neighbours lie within ±0.005 d of it. In practice only TESS 120 s and 200 s data
   are clipped. ZTF, ASAS-SN, Gaia and TESS full-frame-image Light Curves are cleaned by
   their survey flags alone, because their neighbours are too far apart in phase for a
   median to stand for the star's level, and every point in an eclipse or at a pulsation's
   extreme would be clipped as an outlier.
4. **Periodogram** (`periodogram.py`): astropy `LombScargle` per band over 0.05–100 d
   (grid oversampled ×10), plus astropy `BoxLeastSquares` over 0.1–30 d for eclipsers.
   Record the top five peaks with power and false-alarm probability per band. Gaia's
   published `vari_*` period is the first hypothesis: it is always scored and plotted.

   *Amended 2026-10-20 (NEU-1706):* BLS needs a far finer grid than Lomb-Scargle's ×10,
   which smears eclipses under a few per cent of the cycle. Its step keeps the fold's drift
   across the baseline under half the narrowest box (0.01 d) at short periods and under
   half a 1 % duty-cycle eclipse at long ones. BLS peaks carry no false-alarm probability.
   The Gaia period's Lomb-Scargle FAP is the single-frequency one, since the period was
   chosen before the search. When the secondary eclipse is deeper than about 0.4 of the
   primary, BLS also folds at P/2, and the alias step has to settle the period.

   *Amended 2026-10-21 (NEU-1839):* BLS searches in two stages, because evaluating the
   fine grid was about 90 % of the analyzer's time. A coarse pass steps `BLS_COARSEN` = 8
   times wider (the widest box over the narrowest), which dilutes a narrow eclipse's peak
   rather than missing it. Then the `BLS_REFINE_PEAKS` = 500 highest coarse peaks are
   re-searched at the fine step, two coarse steps either side. The returned frequency and
   power are the coarse grid with the refined windows merged in; peaks and the Gaia score
   are unchanged in meaning. K is 500, not the 20 first planned, by measurement. Over 168
   synthetic detached binaries (1–1.5 % duty cycle, six seasons, 500–800 points), 135 were
   found by the fine search. K = 20 recovered 126 of them and K = 500 recovered 133; the
   two lost are coin flips even on the fine grid (true peak ≤ 1.02 × the runner-up). K = 500
   costs about 10 % over K = 20, and the minimum-duty-cycle test (a 600-point curve whose
   true peak ranks 108th at coarse resolution) fails at K = 20. A 500-point ZTF band's BLS
   fell from 12.0 s to 1.7 s, and every gate star's agreed period is unchanged within its
   tolerance. On clumped Light Curves, such as TESS sectors years apart, the fine power is
   a comb of spikes the coarse step can miss outright at short periods, where the widest
   box nears the period itself. CSS_J173907.6+550053's TESS best BLS peak moved from
   0.1001986 d to 0.1002092 d this way, a noise peak for an RRab.
   `box_least_squares(..., two_stage=False)` keeps the single pass for comparison.
5. **Aliases** (`aliases.py`): for each candidate test P, 2P, P/2 and the 1 d, 0.5 d and
   sidereal-day (0.99727 d) aliases; for TESS also 13.7 d orbital systematics. Prefer the
   candidate whose phase-folded scatter is lowest and record the ranking.

   *Amended 2026-10-20 (NEU-1707):* the candidates are every Lomb-Scargle and BLS peak of
   the band plus Gaia's period, and one ranking pools all their hypotheses. An alias of P
   for a sampling interval T is 1 / |1/P ± 1/T|, with T of 1 d, 0.5 d and the sidereal
   day for every survey, and TESS's 13.7 d orbit for TESS as well. Aliases outside the
   Lomb-Scargle range are not tried. Scatter is phase dispersion (Stellingwerf 1978):
   pooled within-bin variance over total variance. P and its aliases are cut into 20
   bins, or fewer below five points a bin; 2P takes twice that and P/2 half, so all three
   resolve the true cycle equally. A fixed bin count would blur 2P and settle a W UMa star
   at P/2, the `DSCTr` trap in §7. The ranking key is the binned fold's BIC,
   N ln(RSS/N) + bins × ln N, so a longer period must earn its extra bins. Minima too
   alike to separate stay at the shorter period.
6. **Agreement** (`agreement.py`): accept a period when at least two of {ZTF g, ZTF r,
   ASAS-SN, TESS, Gaia published period} agree within 0.1 % (or at a 2P/P/2 relation,
   recorded as such). Gaia epoch photometry alone never settles a period.

   *Amended 2026-10-20 (NEU-1707):* each band votes with its alias test's best period.
   ASAS-SN V and g are one source, and ZTF i is none. No two agreeing votes are more than
   0.1 % apart, so agreement cannot chain through a middle vote, and a source whose bands
   fall at both P and 2P is no evidence of a 2:1 relation. The period most sources find wins.
   When two periods tie, no period is agreed. A 2:1 Agreement takes the longer period, the
   one that separates an eclipsing binary's two eclipses, and the classifier sees the
   relation. The agreed period is the mean of the votes at that scale.

   *Amended 2026-10-20 (NEU-1711):* the prototype gate failed five of its 27 stars on two
   rules, both now changed. First, the tolerance is 0.1 % or P / baseline, whichever is
   wider, each pair of votes judged at the shorter of their two baselines: a periodogram peak is about 1 / baseline wide, so over ZTF's 7.5 years a
   40 d period is resolved only to 1.5 %, and the same peak found by ZTF g and r came out
   0.10–0.17 % apart. Gaia's published period counts with Gaia DR3's 1,038-day baseline, and
   the Agreement records the tolerance it used, from the shortest baseline among its votes.
   Below about 2.7 d nothing changes. Second, a band does not vote when its best fold's
   phase dispersion is above 0.75: on two of the three constant stars, ZTF g and r both
   settled on the sidereal half-day, folding at 0.82–0.97, and agreed on it. Every voting
   band of a positive folded at 0.71 or less. The threshold is set on those 27 stars, so
   the margin is thin. The fifth failure, a ROT star at 12.09 d, stays a strict xfail: ZTF's
   oid zero-point flag (`blend_suspect`, NEU-1798) and `tess_contaminated` leave every source
   at half weight.

   *Note 2026-10-20 (NEU-1798):* with the zero points judged at equal phase (step 7), neither
   ZTF band is flagged, and the ROT star agrees at 12.122 d from ZTF g, ZTF r and TESS (2.5
   sources). ZTF r was measured (largest offset 0.037 mag); ZTF g's only non-reference oid
   holds 8 points in the trimmed fixture and is unmeasured, so g counts in full without
   having been judged. It is out of `KNOWN_FAILURES`; the gate passes all 27 stars.
7. **Blend** (`blend.py`): per survey, using the contamination radii in §5.1. Flag
   `blend_suspect` (ZTF and Gaia) when a neighbour within 3″ is brighter than G + 2, or
   when ZTF oid offsets exceeded 0.05 mag; flag `asassn_contaminated` when a neighbour
   within 17″ is brighter than G + 2; flag `tess_contaminated` when any source within
   120″ is brighter than G + 1 or the summed flux of neighbours within 120″ exceeds half
   the Target's. Contaminated surveys still run through the periodogram but count at half
   weight in Agreement and are labelled on every plot.

   *Amended 2026-10-20 (NEU-1708):* the result is one `Contamination` per contaminated
   survey, so `blend_suspect` yields a ZTF and a Gaia entry, except that ZTF oid offsets
   alone flag ZTF only. Each carries the offending neighbours, the Remarks sentence in the
   VSX FAQ's shape (`<survey> magnitudes contaminated by Gaia DR3 <id> (G= <mag>; sep.
   <arcsec>"). Range not corrected.`, G and separation to one decimal) and the share of the
   aperture's light from every other source inside the radius, for a later COMBLEND-style
   correction. The 120″ TESS field reaches past the 20″ neighbour list, so TESS names the
   field's count, brightest G and combined G instead of a neighbour. Half weight means a
   contaminated source counts 0.5 towards Agreement's two sources, and towards the "most
   sources" comparison between periods.

   *Amended 2026-10-20 (NEU-1798):* the ZTF oid offsets are measured here, at equal phase,
   not taken from the fetcher's median shifts (§5.1). For each ZTF band with at least two
   oids, fold at the band's alias-test best period into its `bins` phase bins (the alias
   test's own cut). Skip the band, with no measurement and no flag, when `LightCurve.oid` is
   absent, the alias test has no best period, or its phase dispersion is above Agreement's
   0.75: such a band does not vote, and the neighbour rule still covers real blends. For
   each non-reference oid, the template is the per-bin median of the band's other oids; the
   residual is the median of the oid's points less the template, over bins the others
   populate; its zero-point offset is the fetcher's shift less that residual, in the sign
   of `meta["zero_point_offsets"]`. An oid with fewer than 10 such points is recorded as
   unmeasured. A band is suspect when any offset exceeds 0.05 mag, and a suspect band with
   no neighbour gives the ZTF-only `Contamination` with the remark `ZTF magnitudes possibly
   contaminated: the zero points of its fields differ by <largest> mag at equal phase.
   Range not corrected.`. `Blend.zero_points` records one `ZeroPointOffsets` per measured
   band (band, period, bins, reference oid, offsets, unmeasured oids, suspect), flagged or
   not; the curve itself is not re-aligned (NEU-1802). On the fixtures the rule flags none
   of the 20 positives (it flagged 11 by medians) and both blends stay flagged by their
   neighbours. The largest measured offset on a positive is 0.037 mag; V0364 Dra's g, 0.12
   mag by medians, is 0.007 at equal phase; the largest median shift, 0.176 mag, is 0.022.
   Five oids that carried flags (median shifts of 0.096–0.15 mag, the ROT star's g among
   them) hold only 2–9 points of the 500-point trimmed fixtures and are now unmeasured
   rather than measured small; on untrimmed Light Curves they hold several times as many.

   *Amended 2026-10-21 (NEU-1802):* the curve is now re-aligned at what was measured. Right
   after the blend step, each measured oid's points move by the negated median residual, its
   offset less the fetcher's shift (`blend.realign`). The band's oids then sit at the
   reference's zero point at equal phase. With three or more oids this holds only to second
   order, since each was measured against the others as the fetcher merged them and all
   move at once. Unmeasured oids, and bands with no `ZeroPointOffsets`, keep the fetcher's
   median alignment. A suspect band is re-aligned too, and its range stays uncorrected for
   the neighbour's light, as before. The re-aligned cleaned Light Curve is what Agreement,
   `shape`, `variability`, the plots and the range read. The Cache file is untouched, so the
   fetcher's first pass can still be reproduced. Its `meta["zero_point_offsets"]` in memory
   becomes the measured offsets, which is what has now been added to each oid's raw points.
   Each re-aligned band's best hypothesis is folded again (`aliases.refold`), so its
   recorded scatter is that of the curve the plots draw. It stays first in the ranking. The
   periodograms and the rest of the ranking stay as first scored: a second search per band
   would chase a shift the alias test has already resolved, and would make the period
   depend on itself. `ZeroPointOffsets.realignment` records the magnitudes added to each
   measured oid, beside `offsets`. On the fixtures, no gate result, agreed period, flag or
   best type changed. The re-alignments reach 0.155 mag (ASASSN-V J163919.05+740125.0's r),
   but every oid moved by more than 0.03 mag holds 10–40 of its band's ~500 trimmed points.
   The binned range is dominated by the reference oid, so no positive's ZTF maximum or
   minimum moved by more than 0.010 mag (V0379 Dra's g minimum). Untrimmed Light Curves give
   the non-reference oids more weight, so the change will be larger there.
8. **CMD** (`cmd.py`): absolute G from parallax when `parallax / parallax_error > 5`,
   BP−RP; record the position and the nearest classical instability-strip or
   main-sequence region label.

   *Amended 2026-10-20 (NEU-1708):* the label is one of main sequence, giant branch,
   instability strip, white dwarf or unknown, from fixed (BP−RP, M_G) polygons in
   `classify/types.py` drawn around the Gaia DR2 HR diagram, in observed colours. The
   polygons overlap and are tried in order (white dwarf, instability strip, main sequence,
   giant branch), so a δ Scuti star on the upper main sequence is labelled instability
   strip. Without a significant parallax or a colour the region is unknown. Extinction
   correction is a later improvement.

   *Amended 2026-10-21 (NEU-1720):* the classifier's rules read numbers, not Light Curves, so
   the pipeline also measures `Analysis.shape` and `Analysis.variability`
   (`analysis/shape.py`). `shape` folds one band at the agreed period (none without
   Agreement): the band whose fold stands clearest above its scatter, preferring surveys the
   blend checks found clean, then magnitude bands over TESS flux, whose amplitude its 21″
   pixels dilute; Gaia epochs are never folded. From the binned fold (up to 50 bins, medians
   smoothed over three) it takes the amplitude, scaled to V by `types.AMPLITUDE_TO_V`, the
   primary minimum's width at half depth, the secondary's depth as a fraction of the
   primary's, and the fraction of the cycle within a tenth of the amplitude of maximum; from
   a sixth-order Fourier fit, the rise time, the curve's skewness and R21. `variability` is
   the most variable clean band's 5–95 % spread over 3.29 × its median error, and is None
   when every survey is contaminated. Neither is stored in `analyses` yet: the classifier
   runs on the in-memory Analysis in the hand-off (NEU-1721). The eclipse width and depth
   ratio come from this fold, not from BLS as NEU-1720 first said: BLS fits a single box, so
   it has no secondary depth, and a box's duration means little on an EW star's continuous
   curve.
9. Write the `analyses` row; hand off to `classify.propose` and `plots`.

   *Amended 2026-10-21 (NEU-1718):* `argelander analyze-worker` takes one analyze Job a
   pass, since the work is CPU-bound and runs inline. The claim's transaction also reads the
   Target and its `ok` `light_curve_files` rows and commits; the Light Curves are loaded and
   analysed outside any transaction, with the Gaia record rebuilt from the Target's columns
   (`selector.gaia_record`, the inverse of `gaia_columns`). One transaction then upserts the
   `analyses` row on `(target_id, pipeline_version)`, clearing `plots`, moves the Target to
   `analyzed`, calls the hand-off (`worker.hand_off`, empty until NEU-1721 makes it call
   `classify.propose()` and `plots.render_all()`) and completes the Job. A Target with no `ok`
   Light Curve, or any error, goes to `queue.fail`; a spent Job fails its Target. The worker
   sleeps `ANALYZE_POLL_INTERVAL` seconds (default 60) when the queue is empty, and refuses to
   start on an unmounted `CACHE_DIR` as the fetch worker does, where every Target would
   otherwise fail on missing files.

   *Amended 2026-10-21 (NEU-1721):* the hand-off now calls `classify.propose()`, which
   writes the Proposal (§7), moves the Target on to `proposed` and draws the plots into
   `PLOTS_DIR` through `plots.render_all()` (§8), all in the Analysis's transaction. A failed
   plot fails the Job like any other error, so an Analysis is never committed without its
   Proposal and plots.

## 7. Classifier

Rules are Python objects (grilling decision):

```python
class Rule(Protocol):
    name: str
    applies_to: frozenset[str]            # VSX type codes this rule bears on
    def evaluate(self, a: Analysis, t: Target) -> Evidence  # verdict: supports|against|neutral, weight 0..1, explanation: str
```

*Amended 2026-10-21 (NEU-1719):* `evaluate` also takes the type code,
`evaluate(type_code, analysis, target) -> Evidence`, and is called once per code in
`applies_to`: `Evidence` names the one type it bears on, so a rule that applies to several
types (a period-range check against each `TypeSpec`) needs to know which it is scoring.
`Evidence` lives in `classify/rules/base.py` beside the protocol. A tie in Confidence goes
to a type Gaia's classifier gave (`types.gaia_types`), and a Proposal below
`types.UNCERTAIN_BELOW` (0.7) is marked uncertain.

`registry.py` lists rules per VSX type (initial set: EW, EA, EB, RRAB, RRC, DSCT, HADS,
DCEP, SR, L, M, ROT, ELL, and `CST` for "not variable"). All of these are confirmed
against the revision form's type dropdown captured 2026-10-19
(`docs/reference/vsx-revision-form.md`), and `classify/types.py` carries the full
dropdown vocabulary so a Proposal can never name a type VSX will not accept.

**Type definitions come from VSX's own document**, extracted into
`docs/reference/vsx-type-ranges.md` (period and amplitude ranges, shape discriminators,
secondary-minimum rules, epoch convention and rise-duration rule per type). `types.py`
encodes each as a `TypeSpec`; hard bounds oppose a type outright, soft bounds ("usually")
weigh about half. The confusion classes that share our period range (DCEPS, CWB/CWA,
GDOR, SPB, BCEP, SXPHE, RRD, BY, RS, ACV) are scored as well so the evidence can say
"fits GDOR better than ROT"; a Proposal names a type outside the initial set only as
`VAR` with that evidence attached. The alias traps VSX names (ASAS-3 `DSCTr` stars being
EW at 2P; `ELL|DSCT` at P/2) are the P/2P tests in `analysis/aliases.py`. `confidence.py` combines evidence per type
as (sum of supporting weights − sum of opposing weights) / (sum of all weights), clipped
to 0–1; the Proposal takes the best-scoring type and keeps the full evidence list for
every type that scored. A Target with no Agreement produces a Proposal of type `VAR`
(unchanged) with low Confidence so Tom can see why it failed. Explanations are one
sentence each and are shown verbatim in the review app and copied into Submission
reference text when relevant.

*Amended 2026-10-21 (NEU-1720):* the initial rule set is one module per concern under
`classify/rules/`: `ranges` (period and amplitude against each `TypeSpec`; ROT against BY's
ranges as soft bounds, since VSX points there), `eclipse` (an eclipse is a minimum at most
0.22 of the cycle wide at half depth; EW's near-equal minima, EB's shallower secondary, EA's
flat maxima), `pulsation` (rise time for steep against symmetric curves, skewness for RRAB
against RRC, the DSCT/HADS split at 0.15 mag, R21 for DCEP against DCEPS), `luminosity` (DCEP
against CW by the reddening-free Wesenheit magnitude W = G − 1.90 (BP − RP) and Ripepi et al.
2019's period–Wesenheit relation, CW 0.7–2 mag fainter), `cmd` (`types.EXPECTED_REGIONS`),
`colour`, `gaia` (supports only, at 0.25), `agreement` (no Agreement opposes every periodic
type at full weight; Agreement opposes L and CST) and `variability` (CST and L). Weights: a
hard bound 1, a soft bound 0.5, a statement the type document does not make (no period range
for RS) neutral at 0.5 so a thinly defined type is not confirmed by fewer checks, missing
data neutral at 0; the CMD region weighs 0.5 and colour 0.25, or 0.5 against a star bluer
than its type, since reddening only makes stars redder. The shape thresholds are in
`types.py`, measured on the 20 positives, since the type document's shape statements carry
no numbers: the fixture result below is therefore in-sample, and the margins are thin (a
steep rise at 0.33 of the cycle or less, HADS and DCEP at 0.29–0.32).
`registry.evaluate(analysis, target)` runs every rule for every
type in `types.SCORED_TYPES` order, the initial types first, so a tie Gaia does not break
goes to the initial type (evidence that cannot tell ROT from BY proposes ROT). On the fixture
set 18 of 20 positives get their published type; the two misses are low-amplitude SR stars,
which RS fits as well, and are strict xfails in `tests/test_prototype.py`. Blends and
constants score CST. The alias traps reach Agreement at their true periods and are classified
there as their VSX type, so they are not held to the "no type above 0.5" rule meant for stars
with no period.

**Magnitude range** (VSX FAQ): `mag_max` and `mag_min` are the mean magnitudes at
maximum and minimum read from the binned phase curve of the chosen band after outlier
rejection, never the brightest and faintest single points. For types whose mean level or
amplitude changes between cycles (SR, M, L), they are the mean magnitudes of the brightest
and faintest recorded cycles instead. When the chosen band is a relative-flux source
(TESS) the Proposal sets `min_is_amplitude` and takes the mean magnitude from Gaia G or
APASS V. If a blend flag is set, the range is reported uncorrected with the contamination
stated in Remarks; applying VSX's COMBLEND-style correction is a later improvement.

**Epoch convention** (VSX FAQ): epoch of maximum for pulsators (DSCT, HADS, RRAB, RRC,
DCEP) and hot-spotted stars; epoch of minimum for eclipsers (EA, EB, EW), ELL and
dark-spotted ROT stars; SR, L and M carry no epoch unless Agreement is strong.

*Amended 2026-10-21 (NEU-1721):* `classify/propose.py` turns an Analysis into its Proposal.
The type is the best-scoring one when it is in the initial set or `CST`, else `VAR`; a
periodic type without Agreement is `VAR` too, at the Confidence of the type that scored best,
so its evidence shows the Agreement rule's reason. `CST` and `L` need no Agreement. The range
and the epoch come from the range band: the most points among bands with a VSX passband,
magnitude bands before TESS flux and Gaia's epochs last, since VSX takes a calibrated range in
one band over a mean and an amplitude. Only when TESS is all there is does the Proposal set
`min_is_amplitude`, with Gaia's mean G as `mag_max` and the TESS amplitude in magnitudes as
`mag_min`, `band` being `TESS` (the form's `maxband` is then G). The range is the brightest and
faintest bins of the band's binned phase curve, `plots.phase.binned_median`, the very curve the
phase plot draws; SR, M and L (`types.CYCLE_TO_CYCLE_TYPES`) take the brightest cycle's 10th
percentile and the faintest's 90th over cycles of at least 10 points instead, and a Proposal
without a period takes the band's 5th and 95th percentiles. The epoch is that curve's
brightest or faintest bin by `TypeSpec.epoch_at`, in the cycle nearest the middle of the
Light Curve; SR and M get one only from three or more agreeing sources
(`types.STRONG_AGREEMENT_SOURCES`, asserted, not measured). `eclipse_duration_pct` holds the
rise or eclipse duration by `TypeSpec.rise_duration_rule`: for EA the box BLS fits at the
Proposal period over boxes of 0.5–30 % of it (`periodogram.eclipse_duration`; the search's own
boxes stop at two hours), for RRAB, RRC, DCEP and HADS `Analysis.shape.rise`, the Fourier fit's.
The binned curve's extreme bins were tried for the rise first and read V0364 Dra's at 8 %: a
bin is 4 % of the cycle at 25 bins. On the fixtures the EA eclipses come out at 5.6–10.5 % and
the RRAB rises at 15–16 %.

Thresholds and ranges live in `classify/types.py` and are part of what
`PIPELINE_VERSION` stamps. Verdict feedback (section 9.3) changes rules by hand; this
project does not automate that.

## 8. Plots

PNG via matplotlib, written to `$PLOTS_DIR/{gaia_source_id}/{pipeline_version}/`:
`phase-{survey}-{band}.png` (folded at the Proposal period with phase 0 at the Proposal
epoch, binned median overlaid; phase range by type per the VSX FAQ: 0 to 2 for pulsators
so the maximum is centred, −0.25 to 1.25 for eclipsers and ellipsoidals so the secondary
eclipse is centred, 0 to 2 for rotational and other types), `periodogram.png` (all bands,
candidate and alias markers),
`cmd.png` (Target on a Gaia background drawn from a committed subsample),
`combined.png` (all surveys' light curves on one time axis, magnitudes brightest at the
top, each survey in its own marker and colour with a legend, as VSX asks for data-mined
submissions), and `submission.png`: the one file the revision form accepts, a composite
of the chosen phase plot (the band with the most points unless Tom's Review picks
another) above the combined light curve, rendered under the form's 1 MB limit (a test
asserts the size). VSX forbids attaching light curves copied from survey websites; every
attached plot is rendered here from the Cache.
Paths are recorded in `analyses.plots`. Plots are disposable and regenerated by
`argelander classify --replot`.

*Amended 2026-10-21 (NEU-1721):* `submission.png` leads with the Proposal's range band
(§7), so the plot shows the band whose range goes into the form. `argelander classify
--replot [--target <VSX name>]` (`classify/replot.py`) redraws every Analysis at the current
Pipeline Version that has a Proposal, at its newest Proposal's type, period and epoch, and
rewrites `analyses.plots`; no rule runs and no Analysis or Proposal row changes. Since
periodogram arrays are not stored, it loads the Light Curves from the Cache, cleans them,
searches them again with Lomb-Scargle and BLS, and re-aligns them at the zero points stored in
`analyses.blend`, as the pipeline does. `classify` without `--replot` is refused: the analyze
worker proposes as it analyses.

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

`submission/form.py` renders the VSX revision-form fields from the Proposal and Review.
Rules confirmed from VSX's own submission notice on 2026-10-19
(`docs/reference/vsx-submission-rules.md`):

- Position: J2000 from Gaia DR3 as VizieR gives it, sub-arcsecond.
- Identifiers: the survey id of the object (ZTF object id, ASAS-SN id) and catalogue
  cross-ids in VSX's canonical spelling.
- Type, period in days, and **epoch in HJD**: time of minimum for eclipsers, time of
  maximum for pulsators. `analysis/time.py` provides `bjd_tdb_to_hjd(t, ra, dec)` for
  this conversion; the BJD_TDB value is kept in Remarks for precision.
- Maximum and minimum magnitude **preferably in V**: use ASAS-SN V when it exists;
  otherwise state a catalogue mean magnitude (Gaia G or APASS V) plus the amplitude in
  the band observed, with the band always named (ZTF g/r AB, ASAS-SN g, Gaia G).
  Maximum is the brightest (smallest) number.
- Reference: the survey references exactly as the VSX FAQ gives them (name and bibcode
  table in `docs/reference/vsx-submission-rules.md`), one per survey used, primary survey
  on the form and the rest added via draft; never a URL when a bibcode exists. The
  renderer also produces a reference block for Tom's own records listing each survey
  with date ranges and point counts, the Pipeline Version, and the supporting rule
  explanations; that block is **not** pasted into Remarks.
- Remarks: additional information on the star or photometry only (secondary period,
  Gaia DR3's own class and period, BJD_TDB epoch, blend contamination in the FAQ's
  shape: `ZTF magnitudes contaminated by Gaia DR3 … (G= 15.2; sep. 1.8"). Range not
  corrected.`). **No URLs, no references, nothing that belongs in a table field.**
- Other names: one per line, in VSX's canonical formats, in this order: the 2MASS and
  UCAC4 cross-ids (the Manual discourages Gaia names when any other identifier exists),
  then the ZTF and ASAS-SN identifiers as the surveys publish them, then `Gaia DR3 <id>`.
  Only identifiers not already on the VSX record are added. The exact spelling VSX wants
  for ZTF object ids is not documented anywhere we have read; the first manual
  Submissions (M3) ask the moderators and the answer is recorded here.
- Revision comment: generated from the set of fields that changed and their sources,
  e.g. `Type, period, epoch and range from ZTF, ASAS-SN and TESS data. Gaia DR3 position.`
- Uncertain flags are set only on filled fields; irrelevant fields (nova year, spectral
  type) are never touched.
- Attachment: **one** file of at most 1 MB on the revision form, so `submission.png` is a
  composite (phase plot above, combined multi-survey light curve below) rendered under
  that limit. More images only by saving a draft and editing it.

**Field list captured 2026-10-19** from the live revision form:
`docs/reference/vsx-revision-form.md` has every field, input type and vocabulary.
`FormFields` keys follow the form's `name` attributes: `coords`, `varType`,
`custVarType`, `varTypeUnc`, `magMax`, `maxband`, `custmaxband`, `maxUnc`, `magMin`,
`minband`, `custminband`, `minUnc`, `minAmp`, `period`, `periodUnc`, `epoch`,
`epochQual`, `riseDur`, `other`, `file1title`, `file1desc`, `xrefName`, `xrefCite`,
`xrefURL`, `remarks`, `comment`. `designation`, `specType` and `discoverer` are left as
VSX has them. Consequences:

- Magnitude band is a dropdown code (`g`, `r`, `V`, `G`, …), not free text; TESS goes in
  `custmaxband`/`custminband` and only with `minAmp` checked.
- `varType` must be one of the dropdown codes; compound types go in `custVarType`.
  `varTypeUnc` is set when the Proposal's `uncertain` flag is true (Confidence below the
  threshold in `classify/types.py`), which is VSX's `:` suffix.
- `riseDur` follows the Manual: for EA it is the primary-eclipse duration, first to last
  contact, as % of period, from the BLS fit; never for EW or EB. For asymmetric pulsators
  (RRAB, RRC, DCEP, HADS) it is the rise time from minimum to maximum as % of period,
  from the binned phase curve. Blank for every other type.
- Precision (Manual): magnitudes to 1 decimal for SR, L, M and any star whose mean level
  changes between cycles, 2 decimals otherwise, 3 only when the amplitude is below
  0.05 mag and the scatter supports it; epoch to the number of decimals the period
  resolution supports (integer for long-period M, up to 4 for sub-day periods); period to
  the significant digits the baseline supports (a `period_sigfigs` helper from baseline
  length and period uncertainty).
- `comment` (Revision comment, required, public) is one generated sentence naming the
  surveys used and what changed.
- Position is `HH MM SS.ss ±DD MM SS.s` from Gaia DR3.

### 9.2 Pacing

VSX limits: 5 submissions per week per user; **a new user may have only one submission
open until the first is approved**; fewer than 5 drafts before revising another object;
submissions left needing modification for 60 days, and drafts older than 60 days, are
deleted with one week's notice. The tracker enforces the one-open rule until the first
`accepted` Verdict exists, shows the weekly count against 5, recommends the next
Submission by Confidence, and warns at 45 days for any `submitted` item still without a
Verdict so Tom can respond to moderator requests before deletion.

A Submission whose moderator asks for changes gets status `needs_changes` in the tracker
(between `submitted` and a Verdict), and the detail page shows the Manual's workflow for
it: open the star under "My submissions" on VSX, make edits as "Draft only" so moderators
are not notified per save, tick the delete box on the old file before uploading a new
`submission.png`, then submit once.

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
  *Amended 2026-10-21 (NEU-1722):* it also has `fetcher` and `analyzer`, on the `app` image
  and mount, behind the `workers` profile, so `task up` leaves them stopped and `task workers`
  starts them (`docs/runbook.md`). When NEU-1723 adds prod services of the same names to
  `compose.yaml`, these entries become their dev overrides.
- `backup`: an image with `postgresql-client` and `restic`, run by supercronic at 03:30
  local: `pg_dump | restic backup --stdin --stdin-filename argelander.sql` to a B2 bucket,
  then `restic forget --keep-daily 14 --keep-weekly 8 --prune`. A documented restore drill
  (`deploy/restore.md`) is executed once as a ticket.
- USB SSD mounted by UUID with `nofail` and a `RequiresMountsFor=` systemd override on
  docker.service, documented in `deploy/`; the fetcher refuses to start if `$CACHE_DIR`
  is not a mount point (guards against writing to the boot disk), and so does the analyzer
  (NEU-1718), which would otherwise fail every Target on an empty Cache.
  *Amended 2026-10-21 (NEU-1723):* the guard checks for a marker file,
  `$CACHE_DIR/.argelander-cache`, created once on the SSD, instead of a mount point. Inside
  a container every bind-mount target is a mount point, so the old test always passed. The
  workers bind the SSD's mount point (`${USB_ROOT}` → `/usb`, `create_host_path: false`),
  so an unplugged SSD leaves an empty directory with no marker and the guard's error lands
  in their logs; the marker doubles as their healthcheck. `api` mounts only
  `${USB_ROOT}/plots`, read-only, and sits behind an `api` profile until M6's `web`
  command exists. `compose.yaml` is now the prod stack alone: the dev `app` service lives in
  `compose.dev.yaml`, which resets the workers' build, mounts, environment and healthcheck
  to the repo's. `docs/runbook.md` §10 has the SSD setup.
  *Amended 2026-10-21 (NEU-1724):* `tailscale` runs in userspace mode (no NET_ADMIN or
  `/dev/net/tun`, like the mini's other sidecars) with a 128 MB limit and its node key on
  the `tailscale-state` volume, and shares the `api` profile. tailscaled hands tailnet
  connections to loopback in the shared namespace, so `web` binds `127.0.0.1:8000`; bound
  to `0.0.0.0` it would also answer on the namespace's Compose-network address.
  `deploy/tailscale.md` has the auth key, the ACLs, key rotation and re-registration.
- Deploy: `git pull && docker compose pull/build && docker compose up -d` wrapped as
  `task deploy`, run over SSH by Tom. Not Coolify.
- Monitoring: JSON logs to stdout; Uptime Kuma (personal instance on the mini) checks
  `/healthz` over the tailnet; the backup job logs success/failure. No error tracker.

## 11. Testing and CI

- pytest; tests run fully offline. `tests/fixtures/` holds trimmed real Light Curves as
  Parquet for the prototype set (≈ 20 well-characterised variables of the initial rule
  types plus negatives: known blends, 1-day-alias traps, constant stars), capped at a few
  MB total. A fixture manifest records each star's published period, type and source.

  *Amended 2026-10-21 (NEU-1713):* "offline" means no archive calls, not no services:
  `tests/test_db.py` needs the dev stack's Postgres (CI's `ci` job runs one as a service). It
  migrates a throwaway `<name>_test` database, skips without `DATABASE_URL` outside CI, and
  fails without it in CI.

  *Amended 2026-10-21 (NEU-1714):* the `database_url` and `engine` fixtures live in
  `tests/conftest.py`, shared by `tests/test_db.py` and `tests/test_queue.py`; each database
  test module gets its own fresh `<name>_test` database.

  *Amended 2026-10-21 (NEU-1716):* `tests/test_selector.py` is a database test module too. It
  replays the recorded VSX and Gaia answers through `select-slice`'s own code.

  *Amended 2026-10-21 (NEU-1717):* so is `tests/test_fetch_worker.py`, which drives fake
  fetchers through the worker against real Jobs; its mount-guard tests need no database.
- Tests marked `live` hit archives and are skipped in CI (`-m "not live"`).
- The prototype pass criterion is a test: derived periods match published values within
  0.1 % (or a recorded 2P relation) and negatives produce no Agreement.

  *Amended 2026-10-20 (NEU-1711):* `tests/test_prototype.py` runs the pipeline
  (`analysis/pipeline.py`) on every manifest star and `argelander.prototype.judge` applies
  the criterion, which the notebook shares. A positive's agreed period matches the published
  one within the Agreement's own tolerance (§6 step 6), or at twice or half it, recorded as
  `2:1`. An alias trap reaches no Agreement at its published period, and when it reaches one,
  it is at the true period. A blend or constant star reaches no Agreement or is flagged
  `blend_suspect`. Gaia's period, the neighbours and the CMD inputs come from the catalog
  join recorded in `tests/fixtures/catalog/` (`scripts/build_fixtures.py --catalog`). A star
  the pipeline is known to fail is a strict xfail with its reason, so it shows in the run
  and the entry has to go when it starts passing. The stars run in parallel because BLS over
  ZTF's baseline is the slow step (a few seconds a band since NEU-1839).
- GitHub Actions on push and PR: `uv sync`, `ruff check`, `ruff format --check`, `ty check`,
  `pytest -m "not live"`, `alembic` single-head check, `docker build`. Node is not involved.
  CI also runs on PRs targeting `updates/dependencies`, so Dependabot PRs are checked.

  *Amended 2026-10-20 (NEU-1838):* the prototype gate's tests are marked `gate` and run in
  a second job, `gate`, beside `ci`, which runs `pytest -m "not live and not gate"`. The gate
  took over ten minutes on a hosted runner (BoxLeastSquares over ZTF's baseline, for 27
  stars on four cores) until NEU-1839's two-stage BLS search cut it to 3 min 17 s (3 min
  35 s for the whole job, measured 2026-10-21); CI took about two before it. On a PR the `gate` job runs the tests
  only when the PR changes `src/`, `tests/fixtures/`, `tests/test_prototype.py`,
  `tests/conftest.py`, `pyproject.toml` or `uv.lock`, and otherwise succeeds without running
  them; on a push to `main` it always runs them, each push in its own concurrency group. The job always reports, so the check exists on every PR.
  `task test` and the pre-push hook still run everything, gate included.
- Pre-commit hooks all run inside the `app` container, never on the host, following the
  pattern of the other container-based repos (music-discovery-engine is the template): the
  Homebrew-installed pre-commit framework is the orchestrator only, and every hook is
  `repo: local`, `language: system`, `entry: docker compose exec -T app <tool>`. At the
  commit stage: ruff in fix mode, ruff format, and `ty check`. At the push stage:
  `pytest -m "not live"`. Hooks fix rather than only check, so a failed commit is one
  `git add` from passing. The stack has to be up to commit; with it down a hook fails fast
  with a clear message. Commit normally.

## 12. Conventions

Conventional commits with the Linear ID parenthetical; one ticket = one PR to `main`.
Stage files by name. Secrets only in `.env` (gitignored) and the mini's Compose env. No
third-party API call in a request path of the review app: it reads Postgres and the plot
directory only.

Repository hygiene follows the pattern shared by the other neuroticsasquat-ch repos
(added 2026-10-19, built by NEU-1736). Three GitHub rulesets, created through the API
rather than files: `main` cannot be deleted or force-pushed and accepts only PRs merged as
merge commits; `release/*` and `updates/*` cannot be force-pushed and accept PRs merged by
squash or merge; `updates/*` cannot be deleted. Dependabot runs weekly (Saturday 09:00
New York) on the `uv` and `github-actions` ecosystems, grouped minor-and-patch, targeting
the long-lived `updates/dependencies` branch with `build` and `ci` commit prefixes. A weekly
workflow merges `main` into `updates/dependencies` through a PR so Dependabot diffs stay
small; Tom opens the PR from `updates/dependencies` back to `main` when he chooses. CI runs
on PRs to both branches. The rulesets are the reason every change, including a docs edit,
lands through a PR.

## 13. Milestones (confirmed in Phase 2)

1. **Groundwork** — VSX registration and reading (human); package skeleton, Dockerfile,
   dev Compose with postgres + jupyter, CI, Taskfile.
2. **Prototype science** — LightCurve + Cache, time conversion, four fetchers, periodogram,
   aliases, Agreement, blend, CMD, plots, fixtures, the notebook proving ~20 known stars;
   per-star storage measured.
3. **First manual submissions** — one Submission straight from the notebook, wait for
   approval (VSX allows a new user one open submission), then one or two more (human);
   the evidence format moderators expect is captured into this spec.
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

*Resolved 2026-10-20 (NEU-1711)*, from an untrimmed fetch of the 27 fixture stars
(`python scripts/build_fixtures.py --measure --survey ztf --survey tess --survey gaia`; ASAS-SN
was still down, NEU-1764):

**TESS source.** QLP for 12 stars, a TESScut cutout for 14, SPOC for one, TGLC for none. It
follows magnitude loosely: 10 of the 12 QLP stars are brighter than G 14, and 13 of the 14
cutout stars are G 14.3 or fainter, but QLP also covers two stars at G 14.5 and 15.7 and a
cutout serves one at 13.8. What decides it is whether a prebuilt light curve exists for the
star, so the fetcher keeps choosing per star by availability (prebuilt first, cutout last)
rather than by a magnitude rule.

**Per-star size.** Median 16.0 MB, mean 13.6 MB, largest 18.3 MB, of which TESS is 99 %: ZTF
takes 9–200 kB a star and Gaia epoch photometry about 10 kB. That is 10–30 times the
handover's 0.5–1.5 MB. A TESS Light Curve stitched from every sector runs to 25,000–255,000
points, and `meta["sap"]`, the SAP flux kept for the PDCSAP comparison (§5.1), is written as a
JSON list in the Parquet file metadata. On a constructed 250,000-point Light Curve the four
columns take 6.5 MB and the SAP list another 11.5 MB, 64 % of the file.

Against the handover's storage table (without cutouts):

| Scope | Stars | Handover | Measured, at 13.6 MB a star |
| --- | ---: | ---: | ---: |
| Prototype | 20–27 | ~20 MB | 370 MB for the 27 |
| Realistic first year | 5k–20k | 5–30 GB | 70–270 GB |
| Whole Gaia VAR backlog | ~166k | 80–250 GB | ~2.3 TB |

The first year still fits on the 2 TB SSD; the whole backlog does not. Storing the SAP flux
as a Parquet column instead of in the metadata would take a 250,000-point TESS file from
18 MB to about 8.5 MB, roughly halving the Cache and bringing the backlog to about 1.1–1.4 TB,
before any change to precision or cadence. That is NEU-1799, not part of this measurement.

Per star and survey, in kB:

| Star | G | ztf kB | tess kB | gaia kB | Total kB | TESS source |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| NSVS 2887379 | 13.91 | 116 | 15,008 | 9 | 15,132 | QLP |
| ASASSN-V J164726.16+724659.3 | 14.84 | 44 | 18,087 | 8 | 18,138 | TESScut |
| LINEAR 20427289 | 15.34 | 106 | 12,676 | 8 | 12,791 | TESScut |
| ASASSN-V J163706.32+633921.6 | 14.73 | 98 | 9,187 | 11 | 9,296 | SPOC |
| DW Dra | 13.04 | 111 | 17,183 | 8 | 17,302 | QLP |
| T-Dra0-09947 | 15.08 | 121 | 9,817 | 9 | 9,947 | TESScut |
| 1SWASP J191601.56+560055.3 | 13.60 | 62 | 12,607 | 9 | 12,678 | QLP |
| CSS_J173907.6+550053 | 14.69 | 80 | 17,327 | 9 | 17,417 | TESScut |
| V0364 Dra | 14.85 | 105 | 8,461 | 9 | 8,575 | TESScut |
| V0502 Dra | 14.92 | 138 | 16,349 | 9 | 16,496 | TESScut |
| ASASSN-V J163919.05+740125.0 | 15.08 | 55 | 18,005 | 8 | 18,068 | TESScut |
| V0379 Dra | 13.33 | 188 | 14,426 | 9 | 14,623 | QLP |
| ASASSN-V J164540.98+652508.2 | 15.07 | 199 | 17,079 | 12 | 17,290 | TESScut |
| ASASSN-V J170333.89+604850.1 | 13.38 | 114 | 16,968 | 9 | 17,092 | QLP |
| V0553 Dra | 13.78 | 62 | 17,663 | 12 | 17,736 | TESScut |
| KO Cep | 13.06 | 60 | 4,644 | 10 | 4,714 | QLP |
| ASASSN-V J194707.50+750321.7 | 13.93 | 45 | 14,548 | 9 | 14,603 | QLP |
| ASASSN-V J192915.72+664402.8 | 13.10 | 45 | 17,205 | – | 17,250 | QLP |
| ASASSN-V J164537.58+592828.7 | 14.34 | 98 | 17,744 | – | 17,842 | TESScut |
| ASASSN-V J183551.48+614330.1 | 15.19 | 106 | 18,224 | 9 | 18,338 | TESScut |
| PS1-3PI J190107.42+492456.3 | 15.17 | 79 | 5,737 | 9 | 5,825 | TESScut |
| CSS_J181701.4+514940 | 14.41 | 156 | 9,757 | 9 | 9,922 | TESScut |
| CSS_J160212.7+530724 | 15.70 | 81 | 1,893 | – | 1,974 | QLP |
| CSS_J172350.6+504650 | 14.48 | 86 | 4,453 | – | 4,539 | QLP |
| Gaia DR3 1435854639894380800 | 13.66 | 99 | 16,285 | – | 16,384 | QLP |
| Gaia DR3 1434111643382583936 | 13.96 | 112 | 15,931 | – | 16,043 | QLP |
| Gaia DR3 1435683567052954368 | 15.01 | 9 | 18,212 | – | 18,221 | TESScut |

*Re-measured 2026-10-21 (NEU-1799)*, after SAP moved from `meta` to a Parquet column (§5),
with `python scripts/build_fixtures.py --measure --survey tess`. ZTF and Gaia are unchanged
by it, so their sizes above are carried over. Every star used the same TESS source as on
2026-10-20.

**Per-star size.** Median 7.8 MB, mean 6.7 MB, largest 8.9 MB: half the 2026-10-20 figures.
Each TESS file shrank to 48–52 % of its old size (49 % overall), and TESS is still 98 % of a
star. That bears out the 2026-10-20 estimate of about 8.5 MB for a 250,000-point TESS file:
the largest is now 8.8 MB.

| Scope | Stars | Handover | Measured 2026-10-20 | Measured, at 6.7 MB a star |
| --- | ---: | ---: | ---: | ---: |
| Prototype | 20–27 | ~20 MB | 370 MB for the 27 | 180 MB for the 27 |
| Realistic first year | 5k–20k | 5–30 GB | 70–270 GB | 34–135 GB |
| Whole Gaia VAR backlog | ~166k | 80–250 GB | ~2.3 TB | ~1.1 TB |

The whole backlog now fits on the 2 TB SSD. Float32 storage and cadence binning, each of
which loses information, remain open and are not needed for v1.

Per star, in kB (total = new TESS + ZTF and Gaia from the table above):

| Star | G | tess kB, 2026-10-20 | tess kB, 2026-10-21 | Total kB | TESS source |
| --- | ---: | ---: | ---: | ---: | --- |
| NSVS 2887379 | 13.91 | 15,008 | 7,377 | 7,502 | QLP |
| ASASSN-V J164726.16+724659.3 | 14.84 | 18,087 | 8,721 | 8,773 | TESScut |
| LINEAR 20427289 | 15.34 | 12,676 | 6,293 | 6,407 | TESScut |
| ASASSN-V J163706.32+633921.6 | 14.73 | 9,187 | 4,718 | 4,827 | SPOC |
| DW Dra | 13.04 | 17,183 | 8,333 | 8,452 | QLP |
| T-Dra0-09947 | 15.08 | 9,817 | 5,064 | 5,194 | TESScut |
| 1SWASP J191601.56+560055.3 | 13.60 | 12,607 | 6,310 | 6,381 | QLP |
| CSS_J173907.6+550053 | 14.69 | 17,327 | 8,393 | 8,482 | TESScut |
| V0364 Dra | 14.85 | 8,461 | 4,363 | 4,477 | TESScut |
| V0502 Dra | 14.92 | 16,349 | 7,946 | 8,093 | TESScut |
| ASASSN-V J163919.05+740125.0 | 15.08 | 18,005 | 8,692 | 8,755 | TESScut |
| V0379 Dra | 13.33 | 14,426 | 7,109 | 7,306 | QLP |
| ASASSN-V J164540.98+652508.2 | 15.07 | 17,079 | 8,214 | 8,425 | TESScut |
| ASASSN-V J170333.89+604850.1 | 13.38 | 16,968 | 8,192 | 8,315 | QLP |
| V0553 Dra | 13.78 | 17,663 | 8,520 | 8,594 | TESScut |
| KO Cep | 13.06 | 4,644 | 2,382 | 2,452 | QLP |
| ASASSN-V J194707.50+750321.7 | 13.93 | 14,548 | 7,167 | 7,221 | QLP |
| ASASSN-V J192915.72+664402.8 | 13.10 | 17,205 | 8,338 | 8,383 | QLP |
| ASASSN-V J164537.58+592828.7 | 14.34 | 17,744 | 8,575 | 8,673 | TESScut |
| ASASSN-V J183551.48+614330.1 | 15.19 | 18,224 | 8,774 | 8,889 | TESScut |
| PS1-3PI J190107.42+492456.3 | 15.17 | 5,737 | 2,908 | 2,996 | TESScut |
| CSS_J181701.4+514940 | 14.41 | 9,757 | 5,038 | 5,203 | TESScut |
| CSS_J160212.7+530724 | 15.70 | 1,893 | 970 | 1,051 | QLP |
| CSS_J172350.6+504650 | 14.48 | 4,453 | 2,287 | 2,373 | QLP |
| Gaia DR3 1435854639894380800 | 13.66 | 16,285 | 7,928 | 8,027 | QLP |
| Gaia DR3 1434111643382583936 | 13.96 | 15,931 | 7,716 | 7,828 | QLP |
| Gaia DR3 1435683567052954368 | 15.01 | 18,212 | 8,781 | 8,790 | TESScut |

- Whether ASAS-SN g or V is more useful at 13–16 mag.
- The Draco VAR Target count at 13–16 mag (one VizieR query before milestone 4).

*Resolved 2026-10-21 (NEU-1715)*, from `catalog/vsx.py`'s live test against `B/vsx/vsx`
version 2026-10-18:

**Draco count.** 430 VAR entries with Gaia DR3 IDs whose magnitude at maximum is 13–16, all
typed `VAR` (the four `VAR:` entries in the bounding box are outside Draco or carry no Gaia
ID), all with a period and a G-band range. The VizieR copy has no cross-identification
column: the Gaia DR3 ID is the entry's Name, `Gaia DR3 <source_id>`, for entries VSX imported
from Gaia. Another 88 VAR entries in Draco at 13–16 are named for other surveys (ZTF 56,
ASAS-SN 17, and others) and are not Targets in v1, since VizieR does not carry the Other
names that might hold their Gaia IDs.

*Recorded 2026-10-21 (NEU-1716)*, from `argelander select-slice Dra 13 16` against the dev
database (78 s for the VizieR query and the Gaia join): 430 Targets, 430 fetch Jobs, every
Target with a Gaia DR3 record. By magnitude at maximum: 106 at 13–14, 119 at 14–15, 205 at
15–16. None has a Gaia vari_* class, and all 430 have a Gaia period, each from
`vari_short_timescale`: the entries VSX named for Gaia DR3 and typed `VAR` are Gaia's
short-timescale variables, which are not in `vari_classifier_result`. So for the whole first
Slice the hypothesis the analyzer tests first is the short-timescale frequency, the least
trusted of Gaia's periods (§5.1). 258 Targets have a Gaia neighbour within 20″, and 426 a 2MASS
designation. A second run added no Targets and no Jobs.
