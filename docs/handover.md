# VSX revision pipeline: planning handover

2026-10-19 · Tom

Source: the Claude Doc at https://claude.ai/artifact/R33y31vAcSJ9nB7WEVc3Pk,
converted to Markdown on 2026-10-19. Decisions made after that date are
recorded in the "Decisions since handover" section at the end.

## Purpose

Fable is asked to turn this design into an implementation plan: repo layout,
schemas, tickets and milestones for a self-hosted pipeline that proposes
corrections to the AAVSO's VSX variable star catalog.

Everything below was settled in an exploratory conversation with Tom. Treat
decisions as decided unless Tom reopens them; treat the open questions at the
end as the planning agenda.

About Tom: solo developer, Python/FastAPI and Docker, does ETL for a living, no
physics training. The project is a personal hobby, not part of his business
work.

## The gap

VSX has roughly 166,000 entries with Gaia DR3 IDs typed only as "VAR"
(variable, type unknown), and many short-period ones appear to have wrong
periods, per an AAVSO forum count.

- **Why it exists:** VSX bulk-imported big survey catalogs. Professionals get no
  career credit for cleaning them up, so nobody does.
- **Why it matters:** VSX is the reference catalog people and automated
  classifiers cross-match against, so a wrong period or class misleads everyone
  downstream.
- **Why Tom can fill it:** VSX staff explicitly invite revisions and say
  individual analysis combining Gaia with ZTF and ASAS-SN beats the bulk
  imports. Anyone can register and submit; no sponsor or collaborator is
  needed.
- **The work itself:** reclassify vague entries with correct periods and types
  using public survey light curves. A secondary job is auditing VSX against
  other catalogs (TNS supernovae, quasar catalogs) to find entries that aren't
  variable stars at all.

## Goals and scope

The deliverable is accepted VSX revisions, each a verified claim with traceable
evidence. The pipeline is a private instrument for producing them, not a
product.

**v1 in scope**

- Select VAR-typed VSX entries with Gaia IDs, one sky region and magnitude
  range at a time
- Fetch and cache light curves from ZTF, TESS, Gaia DR3 epoch photometry and
  ASAS-SN
- Derive periods, check aliases and blends, propose a type with a confidence
  score
- Render evidence plots and queue each proposal for Tom's review
- Prepare approved revisions for manual VSX submission and track moderator
  verdicts

**Later, not v1**

- Cross-catalog audits (VSX vs TNS, Milliquas, SDSS quasars)
- A Research Notes of the AAS write-up once a few hundred stars are done
- Batch list submissions, once a track record exists

**Non-goals**

- Automated form submission or scraping VSX's web forms
- A public tool, multi-user access or anything exposed outside Tom's tailnet
- Kubernetes
- Downloading full survey catalogs (the full Gaia source catalog especially)
- Real-time Rubin alert work (a separate future project)

## Data sources and access

Every read is programmatic; the only manual step is submission, which VSX
rate-limits.

| Source | Used for | Access |
| --- | --- | --- |
| VSX (VizieR copy, B/vsx) | Target selection, bulk cross-match | `astroquery` VizieR; weekly-updated copy |
| VSX API | Spot checks on single objects or cone searches | Undocumented `api.object` / `api.list` endpoints returning JSON, XML or CSV |
| ZTF | Main light curves, multi-band | IRSA light-curve service, per position; one star returns several object IDs (per field, CCD and filter) that must be merged, with a zero-point check between fields. ZTF saturates near 12.5–13 mag. |
| TESS | High-cadence light curves | `lightkurve` via MAST; prefer prebuilt QLP or TGLC light curves where they cover the star |
| Gaia DR3 | Epoch photometry, color, parallax, neighbor lookups for blends | `astroquery.gaia` ADQL with the target slice uploaded as a table and joined in one query per slice (no per-star cone searches); epoch photometry via DataLink (Gaia.load_data) |
| Gaia DR3 vari_* tables | Gaia's own class and period for each target: the hypothesis to test, and the likely source of the wrong periods | vari_classifier_result, vari_summary, vari_eclipsing_binary, vari_rrlyrae, vari_cepheid, vari_short_timescale, joined in the same slice query |
| ASAS-SN | Long-baseline photometry | ASAS-SN Sky Patrol |

**Submission constraints**

- No submission API. Revisions go through the VSX web forms to volunteer
  moderators.
- The 2017 policy still stands as of Oct 2026: prior light-curve experience is
  expected, revisions are treated the same as new submissions, several can be
  submitted in one day but moderators review one per person per day (about
  five a week), and the form expects an attached phase plot. Re-check the
  Guidelines page after registering.
- **Checked 2026-10-19 against the VSX one-time submission notice** (Tom
  registered; see `docs/reference/vsx-submission-rules.md`): the limit is
  stated as 5 submissions per week per user, a new user may have only 1
  submission until it is approved, fewer than 5 drafts are allowed before
  revising another object, and submissions needing modification or drafts
  older than 60 days are deleted. Epoch must be HJD; magnitudes preferably in
  V; do not attach survey-site light curves. The VSX login is separate from
  the aavso.org account and uses a username.
- For more than about 4 or 5 stars, VSX guidance prefers a spreadsheet list
  sent to the VSX team. Use this only after individual submissions have built
  a track record.
- Never script the web forms.

## Architecture

Six stages, four automated and two run by Tom, share state through Postgres
and a Parquet light-curve cache:

1. Target selector (automated)
2. Fetcher (automated)
3. Analyzer (automated)
4. Classifier and plots (automated)
5. Review app (Tom)
6. Submission tracker (Tom)

Moderator verdicts loop back to tune the classifier rules.

**Stage details**

- **Target selector:** pulls a slice of VSX VAR entries with Gaia IDs (one
  constellation, magnitude 13–16 for the first slice) into a `targets` table
  with status `pending`, and fetches Gaia color, parallax, neighbors and vari_*
  class/period for the whole slice in one table-upload join.
- **Fetcher:** one async process (I/O-bound), per-archive concurrency limits as
  local semaphores, retries with backoff. Caches raw light curves as Parquet
  keyed by survey and source. Merges ZTF object IDs per star.
- **Analyzer:** one process (CPU-bound; the host has two cores). Convert every
  survey to BJD_TDB first (ZTF MJD, TESS BTJD = BJD−2457000, Gaia TCB
  JD−2455197.5, ASAS-SN HJD) with astropy, tested against a known epoch. Clean
  with survey quality flags; Lomb-Scargle per band plus a box-shaped search for
  eclipsing binaries, starting from Gaia's published period as the first
  hypothesis; test P, 2P, P/2, the 1-day, half-day and sidereal-day aliases,
  and TESS's 13.7-day orbital systematics; require agreement as defined under
  design principles; query Gaia neighbors within a few arcseconds for blends;
  use Gaia color and parallax for a color-magnitude position.
- **Classifier and plots:** outputs proposed type, period, epoch, magnitude
  range and confidence, plus a phase-folded curve per survey, the periodogram
  and the color-magnitude position.
- **Review app:** FastAPI with a minimal frontend; queue sorted by confidence,
  evidence beside the proposed revision, notes on every decision. Stored
  decisions are the evidence trail and future training data.
- **Submission tracker:** generates the exact VSX revision-form field values
  and reference text, with the magnitude range stated in its band (ZTF g/r AB,
  ASAS-SN V or g, Gaia G) and the phase plot to attach. Models a queue of
  submitted-but-unreviewed items (moderators review one per day) and tracks
  each through pending, accepted and rejected with reasons.

## Infrastructure and deployment

Everything runs in Docker Compose on Tom's 2011 Mac mini running Ubuntu Server,
with a 2 TB USB SSD. It stays off the Hetzner/Coolify host, which is reserved
for business tools.

**Services**

- `api`: FastAPI review app
- `fetcher`: one async process, light-curve downloads only
- `analyzer`: one process; period search, classify, plot
- `scheduler`: a small cron container (or a loop in the fetcher) that queues
  target-selection and batch runs
- `postgres`: results, reviews, submissions and the job queue (`SKIP LOCKED`);
  no Redis

**Host constraints**

- Old dual-core CPU, 16 GB RAM maximum, shared with Navidrome and Uptime Kuma.
  Set memory limits per service.
- Internal drive is a 256 GB SATA SSD (Crucial M4); the 2 TB drive is a
  Samsung T7 Shield on USB 2.0 (about 35–40 MB/s). Postgres stays on the
  internal SSD; only the Parquet cache, plots and reference catalogs go on the
  USB drive. Hostname `kumadrome`.
- Some wheels assume newer CPU features (Polars needs `polars-lts-cpu`). An
  "illegal instruction" crash means this. Pin versions once a build works.
- 15-year-old hardware: back up Postgres nightly to Backblaze B2
  (`pg_dump | restic backup --stdin`). Light curves can be re-downloaded;
  reviews and submission history can't.

**Container notes**

- Base image `python:3.12-slim`, not Alpine.
- `MPLBACKEND=Agg` for headless plotting.
- Point astropy, astroquery and lightkurve caches at a mounted volume.
- Mount the USB SSD by UUID in `/etc/fstab` with `nofail`, and make Docker wait
  for it (`RequiresMountsFor=` systemd override). Otherwise a reboot can leave
  the bind mount pointing at the boot disk.
- Review app reachable only on Tom's personal tailnet, through a
  `tailscale/tailscale` sidecar container (the mini already serves Navidrome
  and Uptime Kuma on separate tailnets this way; no host-level `tailscale up`).
- Plain Compose plus `git pull` deploys; not managed by Coolify.

## Storage budget

At about 0.5–1.5 MB per star, even the full 166k-star backlog fits on the 2 TB
SSD, as long as raw TESS cutouts are discarded after extraction. These are
order-of-magnitude estimates; the prototype should measure real per-star sizes.

| Scope | Stars | Without TESS cutouts | With cutouts kept |
| --- | --- | --- | --- |
| Prototype | 20 | ~20 MB | ~1 GB |
| Realistic first year | 5k–20k | 5–30 GB | 50 GB–1 TB |
| Whole Gaia VAR backlog | ~166k | 80–250 GB | 2–8 TB |

**Retention rules**

- Light-curve cache: Parquet on the USB SSD, keyed by survey and source;
  re-downloadable.
- TESS: extract the light curve and delete the cutout in the same job.
- Plots: regenerate on demand or keep on the SSD; disposable.
- Reference catalogs for cross-matching (VSX, Gaia variability tables, ZTF and
  ASAS-SN variable catalogs, TNS, quasar catalogs): column subsets only, about
  5–10 GB total.
- Postgres: internal SSD, about 1 GB even at full backlog scale; backed up
  nightly to Backblaze B2.

## Design principles

The submission rate limit means quality per submission matters far more than
throughput, so engineering effort goes into vetting.

- **Rules before machine learning.** The classifier starts as explicit rules
  (period, amplitude, light-curve shape, Gaia color, VSX type definitions).
  Moderators need explainable reasoning, and rules are easy to adjust after a
  rejection.
- **Provenance on every claim.** Store which survey data, date ranges,
  parameters and pipeline version produced each result.
- **Versioned analysis.** Stamp results with the pipeline version so reruns can
  be compared after a rule change.
- **Idempotent stages.** Each stage reads status from Postgres and can be rerun
  safely; a rerun overwrites rather than duplicates.
- **Postgres job queue.** Workers claim jobs with
  `SELECT ... FOR UPDATE SKIP LOCKED`; stale claims (older than 30 minutes) are
  released. One fetcher and one analyzer process; the claim logic exists for
  idempotency, not for scaling out.
- **Polite fetching.** A single fetcher process keeps per-archive limits as
  local semaphores. Gaia is queried in bulk per slice by table upload, never
  per star.
- **Multi-survey agreement.** A period is accepted when at least two of these
  agree: ZTF g, ZTF r, ASAS-SN, TESS, Gaia's published vari_* period. Gaia
  epoch photometry alone (tens of epochs) rarely settles a period. Always test
  P, 2P, P/2 and the 1-day, half-day and sidereal-day aliases.
- **Human review is mandatory.** Nothing reaches the submission queue without
  Tom approving it.
- **Verdicts feed back.** Moderator rejections and their reasons are stored and
  drive rule changes.

## Build order

Each milestone has to prove its output before the next one starts; the first
gate is matching published periods on known stars.

1. **Register with VSX and read the manual, FAQ and type-designations PDF.** No
   code until this is done.
2. **Notebook prototype on about 20 well-characterized variables plus
   negatives** (known blends, 1-day-alias traps, a few constant stars). Pass
   when derived periods and types match published values and the negatives are
   rejected. Verify the BJD_TDB conversion against a known epoch. Measure real
   per-star storage.
3. **Two or three manual submissions straight from the notebook.** The cheapest
   way to learn moderator expectations; their feedback sets the evidence format
   for everything below.
4. **Command-line pipeline.** Postgres schema and queue, Parquet cache,
   fetcher, analyzer, rule classifier, plot rendering. Run on a small 13–16 mag
   VAR slice.
5. **Containers on the Mac mini.** Compose stack, USB mount safeguards, nightly
   B2 backup, tailscale sidecar.
6. **Review app.** Queue sorted by confidence, evidence plots beside the
   proposed revision, approve/edit/reject with notes.
7. **Submission tracker.** Paste-ready VSX field values, reference text and
   plot, plus status tracking (pending, accepted, rejected with reason).
8. **Ongoing submissions.** One reviewed per day; feed rejections back into the
   rules.
9. **Scale up.** Larger sky slices, then ask the VSX team about list
   submissions once the track record supports it.

## Open questions for planning (as of the handover)

- [x] Repo under `tomboone/`; tracked in the neuroticsasquatch Linear workspace
      (where personal projects already live). Create the Linear project before
      planning starts.
- [x] Project name: Fable to propose unless Tom supplies one.
- [x] Job queue: Postgres `SKIP LOCKED` only; no Redis.
- [x] First slice magnitude range: 13–16.
- [x] First slice constellation or sky region.
- [x] Review app frontend: server-rendered templates or a separate frontend?
- [x] Backup destination: Backblaze B2.
- [x] Tailnet: personal tailnet via tailscale sidecar.
- [x] Submission policy confirmed Oct 2026 (see submission constraints);
      re-checked 2026-10-19 after registering (submission notice captured).
- [ ] VSX type codes, manual and FAQ: build-order step 1. Registration done
      and the revision form captured 2026-10-19
      (`docs/reference/vsx-revision-form.md`, with the type and passband
      vocabularies); manual, FAQ and type-designations PDF still to be read
      (NEU-1694).
- [ ] TESS source per star (QLP, TGLC, cutout fallback): coverage differs by
      magnitude, so resolve in the prototype, not before.

## Decisions since handover (2026-10-19)

- **Project name: Argelander.** Repo `tomboone/argelander`, public, MIT.
  Linear initiative "Argelander"; the v1 buildout is a project inside it.
- **First slice: Draco, 13–16 mag.** The ecliptic north pole lies in Draco, so
  it is the centre of TESS's northern continuous viewing zone; it is fully
  covered by ZTF and sits at high galactic latitude, so blends are rarer. The
  prototype confirms the VAR target count with a VizieR query before
  committing.
- **Review app frontend: server-rendered.** FastAPI + Jinja2 templates + htmx.
  No separate frontend build.
- **One repo.** All services share one Python package and one image, with a
  different entrypoint per Compose service. The notebook prototype, Compose
  stack and deploy notes live here too.

## References

- [AAVSO forum: Gaia DR3 variables: Periods](https://forums.aavso.org/t/gaia-dr3-variables-periods/2495):
  the VAR backlog count and period problems
- [AAVSO: VSX - Do It Yourself](https://www.aavso.org/index.php/node/89651):
  VSX invites revisions; batch submission discussion
- [AAVSO forum: Some misclassified variable star entries in VSX](https://forums.aavso.org/t/some-misclassified-variable-star-entries-in-vsx/2963):
  VSX vs TNS cross-match finding supernovae
- [AAVSO: VSX policy changes and FAQ page added](https://aavso.org/node/83028):
  2017 one-submission-per-day policy
- [AAVSO: Volunteers VSX (PDF)](https://dev-mintaka.aavso.org/sites/default/files/Volunteers_VSX.pdf):
  list submissions and the VizieR copy
- [AAVSO: REST API specification for VSX](https://www.aavso.org/index.php/node/84968):
  undocumented VSX API endpoints
- [AAVSO: What do I do when I discover a new variable star?](https://aavso.org/node/74808):
  VSX registration and submission forms
- [VSX variable star type designations (PDF)](http://www.aavso.org/vsx/help/VariableStarTypeDesignationsInVSX.pdf)
