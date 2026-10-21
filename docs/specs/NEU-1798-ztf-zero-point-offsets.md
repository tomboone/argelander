# NEU-1798: ZTF zero-point offsets measured at equal phase

**Ticket:** [NEU-1798](https://linear.app/neuroticsasquatch/issue/NEU-1798) · Milestone M2
Prototype science · Blocks NEU-1796 · Related: NEU-1711 (the gate run that found it),
NEU-1799 (the same `LightCurve` contract change for TESS SAP), NEU-1800 (the guide promises
this fix).
**Project spec:** `docs/specs/argelander-initial-buildout-project-spec.md` §5, §5.1, §6 step 7,
§11. **Decided:** 2026-10-20, grilling session.

## 1. Problem

The ZTF fetcher merges the several IRSA object ids (oids) a star has per band, one per
field and CCD quadrant, by shifting every oid to the median of the oid with the most points.
It flags the band `blend_suspect` when any such shift exceeds 0.05 mag (spec §5.1), and the
blend step lifts that to a ZTF `Contamination`, so both ZTF votes count half in Agreement.

On the committed fixtures the flag fires on 11 of the 20 positives, none of which has a
Gaia neighbour within 3″. The medians of two oids differ for a variable star whenever their
epochs sample the cycle unevenly, and the difference scales with amplitude. The offsets read
from the current fixtures' `meta["zero_point_offsets"]` confirm it: every flagged positive is
a large-amplitude star, and no DSCT, HADS, SR, 2.5 d ROT or constant star is flagged.

| Star | Type | G | g offsets (mag) | r offsets (mag) | Flagged |
| --- | --- | ---: | --- | --- | --- |
| NSVS 2887379 | EW | 13.91 | +0.012 | −0.057 | r |
| ASASSN-V J164726.16+724659.3 | EW | 14.84 | −0.006 | +0.050 | r |
| 1SWASP J191601.56+560055.3 | EB | 13.60 | +0.047, +0.057 | −0.029, −0.016 | g |
| CSS_J173907.6+550053 | RRAB | 14.69 | −0.093, −0.140 | +0.057, +0.008 | g, r |
| V0364 Dra | RRAB | 14.85 | −0.118 | −0.038 | g |
| V0502 Dra | RRAB | 14.92 | −0.004, −0.103 | +0.011, +0.117 | g, r |
| ASASSN-V J163919.05+740125.0 | RRAB | 15.08 | +0.150 | +0.176 | g, r |
| V0379 Dra | RRC | 13.33 | +0.019, +0.088 | −0.025, +0.060 | g, r |
| ASASSN-V J164540.98+652508.2 | RRC | 15.07 | +0.017, +0.053 | +0.001, −0.076 | g, r |
| KO Cep | DCEP | 13.06 | −0.140 | −0.065 | g, r |
| ASASSN-V J164537.58+592828.7 | ROT 12.09 d | 14.34 | +0.096 | +0.042 | g |
| CSS_J181701.4+514940 (alias trap) | EW | 14.41 | −0.015, −0.076 | −0.023, +0.033 | g |

Every other fixture star's largest offset is 0.038 mag or less. The two blend negatives are
flagged by the neighbour rule, not by offsets, so this rule has caught no real blend on the
fixture set and has cost one positive its Agreement: ASASSN-V J164537.58+592828.7 (ROT,
12.088 d) has ZTF g and r agreeing at 12.110 d, but with ZTF at half weight and TESS
`tess_contaminated`, no period reaches two sources. It is a strict xfail in
`tests/test_prototype.py` (`KNOWN_FAILURES`).

Raising the threshold was rejected in the ticket: it would hide real zero-point blends and
still scale with amplitude.

## 2. What changes

Measure each oid's zero-point offset **at equal phase**, in the analyzer, after the alias
test has settled the band's period, and flag only offsets above 0.05 mag. The fetcher keeps
its median alignment as a first pass, which is good enough for the periodogram, but no
longer judges blends.

That requires one thing the Cache does not hold today: which point came from which oid. The
merged Light Curve keeps the oid list and the applied shifts in `meta`, nothing per point.

### 2.1 Decisions (grilling, 2026-10-20)

1. **Per-point oid identity is an optional `oid` column on `LightCurve`**, not a per-point
   list in `meta` (the pattern NEU-1799 is removing because `clean()` does not subset `meta`)
   and not a generic extras dict (would redesign NEU-1799 inside this ticket). NEU-1799 adds
   TESS `sap` in the same shape, so spec §5 is amended twice in one form.
2. **Measure only; do not re-align.** The curve the plots and the magnitude range see is
   unchanged in this ticket. Re-aligning the merged curve at the measured offsets would fix
   up to ~0.15 mag of range error on RRAB stars and is NEU-1802 (M4), with its own
   before/after on the ranges, so two effects do not land in one gate run.
3. **Fold at the band's own alias-test best period.** A band with no trustworthy fold
   (`AliasTest.best` is None, or its phase dispersion is above `agreement.MAX_SCATTER`) gets
   no offset measurement and no offset flag: it does not vote in Agreement anyway, and the
   neighbour rule still covers real blends. No fallback to the median rule.
4. The fetcher stops writing `meta["blend_suspect"]`. Only the analyzer flags.
5. The measurement lives in `analysis/blend.py`. `blend()` takes the alias tests; the pipeline
   passes them.
6. `Blend` records the measured offsets per ZTF band, flagged or not, so the notebook and the
   before/after table read them from the Analysis.
7. All 27 fixture stars' ZTF Light Curves are re-fetched (the oid identity was never saved).
   TESS and Gaia fixtures are untouched.
8. Guide chapters 3 and 4, notebook 02's sentence on the flag and the ZTF fetcher test are
   updated in the same PR. The threshold stays 0.05 mag.

## 3. Design

### 3.1 `LightCurve` (spec §5, shared contract; amend with a dated note)

```python
oid: np.ndarray | None = None   # int64, one survey-native object id per point; ZTF only
```

- Last field, default `None`, so every existing constructor call still works.
- `__post_init__`: when not None, one-dimensional, same length as `time`, integer dtype;
  stored as a read-only native-endian int64 copy like the other arrays. ZTF oids
  (≈ 1.8 × 10¹⁵) fit int64.
- `__eq__` compares it (both None, or `np.array_equal`).
- `to_parquet` writes it as a fifth column `oid` when not None; `from_parquet` reads the
  column when present and returns `oid=None` when absent. Old Cache files and the TESS and
  Gaia fixtures read back unchanged.
- `clean()` applies the same `keep` index to `oid` as to the four arrays.
- `scripts/build_fixtures.py::trim` subsets `oid` with the arrays. `_ALIGNED_META` is left
  for NEU-1799 to remove.

### 3.2 Fetcher (`fetch/ztf.py`, spec §5.1; amend)

- `_merge_oids` builds the `oid` array alongside the rows (the oid of each point, as int64)
  and passes it to `LightCurve`.
- `meta` keeps `oids`, `reference_oid`, `zero_point_offsets` (the magnitudes added to each
  non-reference oid's points: `reference_median − oid_median`), `time_system`, `fetched_at`.
  `blend_suspect` is removed. `BLEND_OFFSET_MAG` moves to `analysis/blend.py`.
- The module docstring says the median shift is a first alignment for the periodogram, and
  that the analyzer judges the zero points at equal phase.

### 3.3 Analyzer (`analysis/blend.py`, spec §6 step 7; amend)

New dataclass in `analysis/model.py`:

```python
@dataclass(frozen=True)
class ZeroPointOffsets:
    """One ZTF band's oid zero points compared at equal phase (spec §6 step 7)."""
    band: str
    period: float                 # the band's alias-test best period, folded at
    bins: int                     # phase bins, as the alias test cut it
    reference_oid: str            # defines zero
    offsets: dict[str, float]     # non-reference oid → zero-point offset from the reference, mag
    unmeasured: tuple[str, ...]   # oids with too few points in populated bins
    suspect: bool                 # any |offset| > BLEND_OFFSET_MAG
```

`Blend` gains `zero_points: tuple[ZeroPointOffsets, ...] = ()`, one per ZTF band that was
measured. It maps onto the `analyses.blend` JSONB column (§4) with the rest of `Blend`.

Measurement, per cleaned ZTF Light Curve `lc` with its `AliasTest`:

1. Skip (no record, no flag) when `lc.oid is None`, fewer than two distinct oids,
   `alias_test.best is None`, or `best.scatter > agreement.MAX_SCATTER`.
2. Fold at `best.period` into `best.bins` bins, phase from `lc.time[0]`, the same cut as
   `aliases._fold`.
3. For each non-reference oid `k`: the template is the per-bin median of the band's **other**
   oids' values (leave-one-oid-out); the residual is the median over `k`'s points, in bins
   the others populate, of `value − template[bin]`. Oids with fewer than `MIN_OID_POINTS`
   (10) such points go in `unmeasured`.
4. `offsets[k] = zero_point_offsets[k] − residual_k`. The fetcher shifted `k` by its median
   difference; the residual is how wrong that shift was at equal phase; the difference is
   the shift that would align `k` with the others at the same phase, which is the true
   zero-point difference. For a variable star with uneven sampling the two terms cancel; for
   a field with a neighbour in its photometry they do not.
5. `suspect = any(abs(o) > BLEND_OFFSET_MAG for o in offsets.values())`, `BLEND_OFFSET_MAG =
   0.05`.

`blend(record, curves=(), alias_tests=())`: the ZTF `blend_suspect` `Contamination` with no
neighbour (today's `elif` branch) is produced when any band's `ZeroPointOffsets.suspect` is
true. Its remark names the measured size: `ZTF magnitudes possibly contaminated: the zero
points of its fields differ by 0.12 mag at equal phase. Range not corrected.` (largest
offset, two decimals). The neighbour branch and the Target-without-G case keep their
behaviour. `pipeline.analyze` calls `blend(record, cleaned, analysis.alias_tests)`.

### 3.4 Fixtures

- Inside the `app` container: `python scripts/build_fixtures.py --survey ztf` for every
  manifest star. The trim seed is the Gaia source ID, so if IRSA's data are unchanged since
  2026-10-20 the kept points are identical and only the `oid` column is new. The PR records
  any star whose point count changed. `tests/test_fixtures.py`'s ≤ 5 MB cap must still pass
  (the column adds ≈ 4 kB per band).
- `tests/fixtures/ztf/css_j173907_irsa.csv` stays as the fetcher's recorded response.

### 3.5 Before and after

The PR carries a table of the 20 positives, both ZTF bands: the fetcher's median shift
(§1 above, "before") beside the equal-phase offset from `Analysis.blend.zero_points`
("after"), and which stars remain flagged. Any remaining flag on a positive without a
neighbour within 3″ is explained in the PR. The "after" column is also what goes into the
spec §6 step 7 amendment, summarised.

### 3.6 Documents

- Spec §5: dated amendment naming the `oid` field, its `None` default for old files, and
  that NEU-1799's `sap` follows the same shape.
- Spec §5.1 (ZTF): the fetcher aligns by medians as a first pass and records the shifts;
  the blend judgement moves to §6 step 7.
- Spec §6 step 7: dated amendment describing the equal-phase measurement, the skip rule,
  what `ZeroPointOffsets` records, and the before/after summary.
- Spec §6 step 6: the NEU-1711 amendment's last sentence about the ROT star gets a dated
  note with the outcome.
- `docs/guide/03-reading-the-evidence.md` (§3.6 table row and the caveat paragraph) and
  `docs/guide/04-can-you-trust-it.md` ("The one known failure", the thresholds table row,
  and the results table's Flags column where it changes): rewritten in the past tense for
  what the rule now does. `notebooks/02_guide_one_star.ipynb`: the one sentence on the
  zero-point rule.
- `CONTEXT.md`: glossary entry **Zero-point offset** (added in this session).
- `tests/test_prototype.py`: the ROT star leaves `KNOWN_FAILURES` if it passes (ZTF g + r at
  full weight is 2.0, Agreement's minimum); otherwise its entry names the remaining reason.

## 4. Tests

- `tests/test_lightcurve.py`: `oid` round-trips through Parquet; a file without the column
  reads back `oid=None`; a length mismatch and a float `oid` raise; equality covers `oid`.
- `tests/test_clean.py` (or where `clean()` is tested): a dense synthetic curve with `oid`
  keeps `len(oid) == len(time)` after clipping, with the kept points' oids unchanged.
- `tests/test_fetch_ztf.py`: each point's `oid` matches the CSV row it came from (matched
  through time, as the offsets test already does); `blend_suspect` is no longer in `meta`;
  the offsets test stays; the flag test is removed.
- `tests/test_blend.py`, on constructed curves:
  - a sine with two oids sampled at different phase ranges, whose median shift is above
    0.05 mag, measures an equal-phase offset near zero and is not flagged;
  - the same with a 0.10 mag constant shift added to one oid measures ≈ 0.10 and is flagged,
    with the remark naming 0.10 mag;
  - a single-oid band, a band with `oid=None`, and a band whose best fold's scatter is above
    `MAX_SCATTER` produce no `ZeroPointOffsets` and no flag;
  - a Target without G and a suspect band still yields the ZTF-only `Contamination`.
- `tests/test_fixtures.py`: `trim` subsets `oid` (if a trim test exists; add one otherwise).
- `tests/test_prototype.py` is the regression gate.

## 5. Acceptance criteria (ticket DoD plus the above)

- [ ] On the fixture set, the offset rule flags no positive that has no neighbour within 3″,
      or each remaining flag is explained in the PR.
- [ ] Both blend negatives (CSS_J160212.7+530724, CSS_J172350.6+504650) are still flagged
      `blend_suspect`.
- [ ] `ASASSN-V J164537.58+592828.7` is out of `KNOWN_FAILURES` in `tests/test_prototype.py`,
      or its entry names the remaining reason.
- [ ] Spec §5, §5.1 and §6 step 7 carry dated amendments as in §3.6.
- [ ] `grep -rn blend_suspect src/argelander/fetch` finds nothing.
- [ ] Every ZTF fixture has an `oid` column; TESS and Gaia fixtures are byte-for-byte unchanged.
- [ ] `pytest -m "not live"` passes offline; the fixture size test passes.
- [ ] The PR has the before/after offsets table.
- [ ] Guide chapters 3 and 4 and notebook 02 no longer describe the fix as pending.

## 6. Out of scope

- Re-aligning the merged curve at the equal-phase offsets (affects ranges and plots):
  NEU-1802, blocked by this ticket.
- The neighbour-based `blend_suspect` rule and its 3″ / G + 2 thresholds.
- TESS `sap` as a column (NEU-1799), removing `_ALIGNED_META`, a generic extras mechanism.
- ASAS-SN fixtures and the gate re-run with them (NEU-1796, which this ticket blocks).
- Changing the 0.05 mag threshold.

## 7. Risks

- **IRSA data drift.** If ZTF's light-curve service has new epochs since 2026-10-20, the
  re-fetched fixtures sample different points and a gate result could move for reasons
  unrelated to this change. The PR states whether any point count changed.
- **Thin template coverage.** An oid whose points fall in bins the others never populate
  cannot be measured; it is recorded in `unmeasured` rather than silently passing.
- **The ROT star.** It passes only if ZTF g and r both lose the flag; if TESS also agrees it
  adds 0.5. If it still fails, the `KNOWN_FAILURES` entry names what remains.
