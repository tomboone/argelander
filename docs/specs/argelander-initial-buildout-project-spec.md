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
| `proposals` | one Proposal per Analysis | `analysis_id`, `target_id`, `pipeline_version`, `proposed_type` (a code from the VSX dropdown vocabulary), `uncertain` bool, `period`, `epoch_bjd`, `eclipse_duration_pct` nullable, `mag_max`, `mag_min`, `min_is_amplitude` bool, `band` (VSX passband code), `confidence` numeric(4,3), `evidence` jsonb (list of rule name, verdict, weight, explanation), `status` (`queued`/`superseded`/`approved`/`rejected`), `superseded_by` nullable FK |
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
- **Gaia DR3 epoch photometry** (DataLink, `Gaia.load_data(..., retrieval_type=
  "EPOCH_PHOTOMETRY")`): G, BP, RP per transit; drop `rejected_by_photometry` and
  `rejected_by_variability`. Fetched per Slice in batches of IDs, not per star.
- **ASAS-SN** (Sky Patrol v2 client): V and g; keep the quality flag the client marks good.
  Long baseline is its value; its cadence rarely settles short periods alone.

Per-archive concurrency is a local semaphore in `fetch/base.py` (defaults: IRSA 2, MAST 2,
Gaia 1, ASAS-SN 2) with exponential backoff on HTTP 429/5xx and a per-Target
`max_attempts` of 3. Gaia catalog data (colour, parallax, vari_*, neighbours) is **not** a
fetch Job: `select-slice` fetches it for the whole Slice in one ADQL upload join.

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
7. **Blend** (`blend.py`): per survey, using the contamination radii in §5.1. Flag
   `blend_suspect` (ZTF and Gaia) when a neighbour within 3″ is brighter than G + 2, or
   when ZTF oid offsets exceeded 0.05 mag; flag `asassn_contaminated` when a neighbour
   within 17″ is brighter than G + 2; flag `tess_contaminated` when any source within
   120″ is brighter than G + 1 or the summed flux of neighbours within 120″ exceeds half
   the Target's. Contaminated surveys still run through the periodogram but count at half
   weight in Agreement and are labelled on every plot.
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
- GitHub Actions on push and PR: `uv sync`, `ruff check`, `ruff format --check`, `ty check`,
  `pytest -m "not live"`, `alembic` single-head check, `docker build`. Node is not involved.
  CI also runs on PRs targeting `updates/dependencies`, so Dependabot PRs are checked.
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
- Whether ASAS-SN g or V is more useful at 13–16 mag.
- The Draco VAR Target count at 13–16 mag (one VizieR query before milestone 4).
