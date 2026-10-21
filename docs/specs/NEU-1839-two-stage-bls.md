# NEU-1839: Two-stage BoxLeastSquares search

**Ticket:** [NEU-1839](https://linear.app/neuroticsasquatch/issue/NEU-1839) · Milestone M4
CLI pipeline · Related: NEU-1711 (the prototype gate this must keep green), NEU-1838 (the
gate's own CI job; the sibling that split the cost this ticket cuts), NEU-1798 (fixture
point counts).
**Project spec:** `docs/specs/argelander-initial-buildout-project-spec.md` §6 step 4, §11.
**Decided:** 2026-10-21, grilling session.

## 1. Problem

BoxLeastSquares is about 90 % of the analyzer's time per star. Its frequency grid
(`periodogram._bls_grid`) steps finely enough that the fold never slides more than the
narrowest box (0.01 d) across the baseline, so a 1 % duty-cycle eclipse cannot slip between
two trial periods. Over ZTF's 2,764-day fixture baseline that is 903,623 trial periods, each
evaluated at four box durations: 267k regular steps above the 1-day knee and 636k geometric
steps below it. Gaia's 942-day baseline still needs 307,965.

Measured 2026-10-20 in the app container, one core, CSS_J173907.6+550053's trimmed fixtures:

| Band | Points | Baseline | Lomb-Scargle | BLS |
| --- | ---: | ---: | ---: | ---: |
| ZTF g | 500 | 2,764 d | 1.6 s | 12.1 s |
| ZTF r | 500 | 2,750 d | 1.7 s | 12.1 s |
| TESS | 500 | 1,977 d | 1.2 s | 8.4 s |
| Gaia G / BP / RP | 42–44 | 942 d | 0.5–0.8 s | 2.5 s each |

The prototype gate (27 stars) takes about 11 minutes on a 4-vCPU GitHub runner because of
this, and the production analyzer will run untrimmed Light Curves (870–2,414+ ZTF points per
band) on a 2011 Mac mini.

The fine step exists for a reason the module docstring records: a coarser grid on its own
misses narrow eclipses at long baselines. The fix has to keep that guarantee.

## 2. What changes

`box_least_squares` in `src/argelander/analysis/periodogram.py` searches in two stages.

### 2.1 Coarse pass

`_bls_grid` gains a `coarsen: float = 1.0` factor that multiplies both of its steps: the
regular step above the knee becomes `coarsen × BLS_MIN_DUTY_CYCLE / baseline`, and the
geometric ratio below it becomes `1 + coarsen × BLS_DURATIONS[0] / baseline`. The knee and
the period range do not move.

The coarse pass uses `coarsen = BLS_COARSEN`, a module constant derived as
`BLS_DURATIONS[-1] / BLS_DURATIONS[0]` (8 today). The fold then slides at most one widest box
(0.08 d) below the knee and 8 % of the period above it, so a 1 % duty-cycle eclipse is
diluted by at most a factor of 8 at every period: the widest box still contains all of its
points. For the ZTF baseline that is about 113k periods, 8× fewer than today. Decision: one
factor for both regions, rather than a constant 0.08 d slide at every period (which would
have been safer above 8 d but left 197k periods and changed the grid's two-part shape).

### 2.2 Refine

Take the `BLS_REFINE_PEAKS = 20` highest local maxima of the coarse power (`_top_peaks`
generalised to take a count). For each, re-search the fine grid (`_bls_grid(time)`, today's
step) over the window from two coarse steps below the peak's frequency to two coarse steps
above it, clipped to the grid's ends. That is about 33 fine periods per peak, 660 in all,
which is negligible beside the coarse pass. K is 20 rather than 5 because the coarse pass
dilutes the true peak up to 8× while leaving noise peaks alone, so the true peak may rank
well below fifth at coarse resolution; the narrow-eclipse test in §3 is the guard. The PR
may raise `BLS_REFINE_PEAKS` if that test or the gate needs it, and records the measured
coarse rank of each synthetic true peak either way.

*Amended 2026-10-21 (implementation):* `BLS_REFINE_PEAKS` is 500, by measurement. A sweep
of 168 synthetic detached binaries (1–1.5 % duty cycle, six seasons, 500–800 points) put
the true peak's coarse rank above 20 in 9 of the 135 the fine search finds, and above 500 in
2, both coin flips on the fine grid too (true peak ≤ 1.02 × the runner-up). K = 500 costs
about 10 % over K = 20 on a ZTF band. The minimum-duty-cycle test uses a 600-point curve
whose true peak ranks 108th at coarse resolution, so it fails at K = 20.

Building the full fine grid stays cheap (a few MB of floats); only evaluating power on it
was expensive. The implementation may build both grids up front and pick the refine windows
out of the fine one by frequency bounds.

### 2.3 The result

`frequency` and `power` hold the coarse grid with every refined window merged in, sorted
ascending, with duplicate frequencies dropped (above the knee every eighth fine point
coincides with a coarse one; their powers are identical, so either copy may be kept). The
`PeriodogramResult` contract is otherwise unchanged:

- `frequency` is ascending, as `model.py` documents, and about 8× shorter than today.
- `peaks` are the five highest local maxima of the merged array, strongest first, picked by
  the existing `_top_peaks`. Every one lies on the array. They are always refined peaks,
  because refinement can only raise a peak and the top five of the coarse pass are among
  the twenty refined.
- `gaia` is scored exactly as today, at the published period with the boxes shorter than it.
- `insufficient` is still judged on the cleaned Light Curve and the fine grid (fewer than
  three frequencies), so no band changes from searchable to insufficient.

The periodogram plot (`plots/periodogram.py`) draws the merged array unchanged. Its BLS
panels show a coarse curve with fine spikes at the refined peaks, which is what the plot is
for. The alias step takes only `peaks` and does not change.

### 2.4 Keep the single pass reachable

`box_least_squares(lc, gaia_period=None, *, two_stage: bool = True)`. With `two_stage=False`
it runs today's search over the full fine grid. The pipeline never passes it; it exists for
the equivalence test in §3 and for the PR's before/after timing, so neither has to
reconstruct the old search.

### 2.5 What does not change

- Every band, Gaia's three included, still gets a BLS search. Decision: no survey special
  case in the periodogram step; the two-stage search makes Gaia's cost negligible, and its
  BLS peaks keep feeding its alias test and plot panels as today.
- `BLS_PERIOD_RANGE`, `BLS_DURATIONS`, `BLS_MIN_DUTY_CYCLE`, `N_PEAKS`, the Lomb-Scargle
  grid, astropy's phase oversampling of 10.
- `PIPELINE_VERSION` stays `0.1.0`. NEU-1798's analysis change did not bump it and nothing
  is in Postgres yet; the version starts moving when M4 writes Analyses.
- The pipeline (`analysis/pipeline.py`), the alias test, Agreement, the blend step.

## 3. Acceptance criteria

- `tests/test_periodogram.py`'s existing BLS tests pass unchanged, including
  `test_bls_finds_narrow_eclipses` and `test_eclipsing_binary_bls_finds_true_period_ls_finds_half`.
- New test: a detached binary whose eclipse duty cycle is at or near `BLS_MIN_DUTY_CYCLE`
  (1 %), in a synthetic curve of at least six 200-day seasons, is found at its period
  within `TOLERANCE` by the two-stage search. This is the case the fine step was built for
  and the one the coarse pass dilutes most.
- New test: on the synthetic eclipsing binaries, the two-stage `best.period` equals the
  single-pass (`two_stage=False`) `best.period` to within one fine step.
- New test: the merged `frequency` is strictly ascending, every returned peak's period lies
  on it, and its length is well under a quarter of the fine grid's for a multi-season
  baseline.
- New test: `_bls_grid(time, coarsen=8)` has about an eighth of the points of
  `_bls_grid(time)` for the same baseline, and the same first frequency.
- The PR carries a per-band BLS timing table, before and after, measured the way the ticket
  measured: app container, one core, CSS_J173907.6+550053's trimmed fixtures. A 500-point
  ZTF band's BLS is at least 4× faster (about 8× is expected).
- The prototype gate (`pytest -m gate`) passes all 27 stars, and the PR carries a table of
  each star's agreed period before and after; every pair matches within the Agreement's own
  tolerance (§6 step 6). The gate's wall time on the GitHub runner goes in the PR too.
- Spec §6 step 4 gets a dated amendment describing the two-stage search, `BLS_COARSEN` and
  `BLS_REFINE_PEAKS`; the §11 NEU-1838 amendment's "over ten minutes" clause is updated with
  the measured gate time.
- ruff, ruff format, ty and the full offline suite pass.

## 4. Out of scope

- The Lomb-Scargle grid (552k frequencies, but only about 1.6 s per band).
- Changing `BLS_PERIOD_RANGE`, `BLS_DURATIONS` or `BLS_MIN_DUTY_CYCLE`.
- Skipping BLS for sparse bands (a point-count floor). Principled, since 42 Gaia points
  cannot resolve a 1 % eclipse, but a science change for its own ticket if ever wanted.
- Measuring how BLS cost scales with N on the Mac mini; untrimmed fixtures are not committed.
- GPU or numba implementations of BLS; a larger CI runner.
