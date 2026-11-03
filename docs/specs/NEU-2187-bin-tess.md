# NEU-2187: Bin TESS before the period search

**Ticket:** [NEU-2187](https://linear.app/neuroticsasquatch/issue/NEU-2187) · Milestone M3 First
manual submissions · Related: NEU-1839 (two-stage BLS, the last cut to analyzer time), NEU-2185
(whose prod re-run exposed the cost), NEU-1706 (cleaning at native cadence).
**Project spec:** `docs/specs/argelander-initial-buildout-project-spec.md` §6 steps 3–8, §4
(rerun semantics).
**Decided:** 2026-11-03, `/planit` grilling.

## 1. Problem

The prod analyzer on the mini takes minutes per Target, and the time is set by how many points
the Target's TESS Light Curve holds. Draco contains the north ecliptic pole, inside TESS's
northern continuous viewing zone, so a stitched TESS curve there runs to 250,000 points.

Measured on prod, 2026-11-03, over every finished Analysis (`analyses.inputs->'tess'->'n_points'`
against `finished_at - started_at`):

| TESS points | Analyses | Mean time |
| ---: | ---: | ---: |
| 16k–50k | 97 | 144 s |
| 51k–99k | 25 | 208 s |
| 100k–142k | 16 | 301 s |
| 152k–198k | 11 | 358 s |
| 200k–250k | 61 | 493 s |
| 250k–254k | 21 | 542 s |

That is about 85 s a Target plus 1.8 s per 1,000 TESS points. 82 of the 231 have over 200k
points. The analyzer is CPU-bound (one core at 100 %, under 1 GB of its 3 GB).

On the dev laptop, one real Draco Target (Gaia DR3 1692536561407598336, TESS 49,015 points at
200 s over 1,684 d) spent 8.7 s of its 22.8 s of periodograms in TESS BLS. Binned at 10 minutes
(28,900 points) TESS BLS took 6.3 s; at 30 minutes (12,119), 4.8 s. BLS cost there is roughly
3.6 s set by the period grid plus 0.1 ms per point. The plots took 3.7 s. The fixtures trim
TESS to 500 points, so the gate never sees any of this.

## 2. What changes

### 2.1 Binning

New `analysis/bin.py` with `bin_tess(lc: LightCurve) -> LightCurve`, and a constant
`TESS_BIN_WIDTH = 10 / 1440` days (10 minutes), commented with the reason below.

- Applied to TESS only (`lc.survey == "tess"`). ZTF, ASAS-SN and Gaia are a few hundred points a
  band and are left alone.
- Bins are a fixed grid from the curve's first point: bin k holds the points with
  `floor((time - time[0]) / TESS_BIN_WIDTH + 0.5) == k`. Empty bins produce nothing, so gaps
  between sectors stay gaps. (The `+ 0.5`, added in implementation, starts the grid half a
  bin early, so a cadence of exactly 10 minutes puts each point mid-bin, not on an edge
  where floating-point rounding would pair it with its neighbour.)
- Each non-empty bin becomes one point: `time` the mean time of its points, `value` the mean,
  `error` the propagated error `sqrt(sum(error²)) / n`, `quality` 0. `sap` is binned by mean
  like `value`. `oid` stays None.
  - A bin with one point passes it through unchanged, so 600 s and 1,800 s full-frame curves
    come out as they went in.
  - Mean, not median: cleaning (§6 step 3) has already clipped the outliers at native cadence,
    and the mean keeps the photometric precision the median loses.
- `meta["bin"] = {"width_days": TESS_BIN_WIDTH, "n_input": <points before>}` records it, beside
  `meta["clean"]`.

**Why 10 minutes.** The shortest period searched is Lomb-Scargle's 0.05 d (72 min). A
10-minute bin is 14 % of that cycle, so a sinusoid at 0.05 d keeps about 97 % of its amplitude
(sinc(π × 0.139)). It is also narrower than BLS's narrowest box (0.01 d, 14.4 min). 200 s data
shrinks about 3×, 120 s data about 5×. At the prod rate above, a 254k-point Target goes from
about 9 minutes to about 4.

### 2.2 Where it sits

In `pipeline.analyze`, immediately after `clean()`, for every Light Curve:
`cleaned = [bin_tess(clean(lc)) if lc.survey == "tess" else clean(lc) for lc in curves]`.

Cleaning stays at native cadence, because its running-median clip needs neighbours within
±0.005 d (§6 step 3, NEU-1706). Everything after reads the binned curve: Lomb-Scargle, BLS, the
alias tests, the blend checks, Agreement, `shape`, `variability`, the Proposal's range and
epoch (`classify.propose`), and every plot. `analyze` returns the binned curve as TESS's
cleaned curve, so the worker's hand-off and `classify --replot` draw what was searched.
`analyses.inputs` keeps recording the Cache file's point count, not the binned count.

### 2.3 Pipeline Version

`PIPELINE_VERSION` goes from `"0.1.0"` to `"0.2.0"`, as spec §3.1 and the runbook (§6) require
when the analysis changes; it has never been bumped before. Under §4's rerun semantics each
re-analysed Target gets a new Analysis beside its 0.1.0 one, and its queued 0.1.0 Proposal is
superseded by the 0.2.0 one. 0.1.0 plots stay in their own version directory.

## 3. Acceptance criteria

- [ ] `tests/test_bin.py`:
  - a 120 s synthetic TESS curve of about 250,000 points over many sectors years apart, with
    an RRAB-like signal at a known period, bins to no more than its span in 10-minute bins
    covers, and Lomb-Scargle and the alias test find the same period binned as unbinned,
    within the Agreement tolerance;
  - a DSCT-like sinusoid at 0.06 d is still the top Lomb-Scargle peak after binning, with its
    fitted amplitude within 5 % of the unbinned one;
  - a 600 s curve, one point per bin, comes back point-for-point identical;
  - errors propagate as `sqrt(sum(error²)) / n`; gaps produce no bins; `meta["bin"]` records the
    width and the input count;
  - ZTF, ASAS-SN and Gaia curves pass through `analyze` unbinned.
- [ ] The prototype gate passes unchanged. Its trimmed TESS fixtures hold about one point per
  10-minute bin, so they are close to a pass-through; the gate guards against regressions,
  not the binning itself.
- [ ] A one-off comparison on the dev Cache's 60 Draco Targets, binned against unbinned, is
  recorded in the spec amendment (§4 below): how many Targets keep the same agreed period
  within tolerance, and each one that changes, with its periods and why. Run on the laptop; not
  a committed test, since the dev Cache is not in the repo.
- [ ] `PIPELINE_VERSION == "0.2.0"`. Most tests that write `"0.1.0"` use it as an arbitrary
  stamp and stay; those that assert the current version (e.g. `test_analyze_worker.py`'s
  `row.pipeline_version == "0.1.0"`) read `PIPELINE_VERSION` instead.
- [ ] After deploy, `avg(finished_at - started_at)` over a fresh batch of at least 50 0.2.0
  Dra-13-16 Analyses is posted on the ticket beside the 0.1.0 table in §1, by the TESS-point
  bucket query used there.
- [ ] `docs/runbook.md` §3's "about 20 seconds per Target for the analyzer" is replaced with the
  measured prod figure and its date.

## 4. Docs

- Spec §6: a dated `*Amended (NEU-2187)*` paragraph under step 3, saying where TESS is binned,
  the width and why, the mean and error, and the dev-Cache comparison's result.
- `analysis/pipeline.py`'s module docstring names the binning step.

## 5. Out of scope

- The ~85 s a Target costs with no TESS at all, mostly the BLS grid over ZTF's and ASAS-SN's
  7–8-year baselines. A separate ticket if it matters once TESS is binned.
- Binning in the fetcher or the Cache. The Cache stays the raw record, so cleaning and binning
  can change without re-fetching.
- Running a second analyzer on the mini.
- Shrinking the Cache's TESS files (spec §14's SAP-in-metadata note).

## 6. Manual steps after merge

1. Wait for Watchtower, then on the mini check the analyzer runs 0.2.0:
   `docker compose exec analyzer argelander version` prints `0.2.0`.
2. If the analyzer was stopped while waiting for this fix, start it: `docker compose up -d analyzer`.
3. Re-queue the Slice: `docker compose exec fetcher argelander enqueue analyze --slice
   Dra-13-16`. Targets that already have a pending Job are skipped; that Job will run at 0.2.0
   anyway.
4. After the first 50 or more 0.2.0 Analyses, run the bucket query from §1, filtered to
   `pipeline_version = '0.2.0'`, and post it on the ticket.
