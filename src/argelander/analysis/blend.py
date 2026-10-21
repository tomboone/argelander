"""The blend checks: is a period or a range really the Target's, or a neighbour's?

Spec §6 step 7. A variable neighbour inside a survey's aperture produces a convincing period
that belongs to the wrong star, so each survey is judged against its own aperture, using the
contamination radii from VSX's submission notice (§5.1):

- `blend_suspect` covers ZTF and Gaia: a neighbour within `BLEND_RADIUS_ARCSEC` brighter
  than the Target's G + `BLEND_DELTA_G`, or (ZTF only) zero-point offsets between a band's
  oids above `BLEND_OFFSET_MAG`, measured at equal phase (below);
- `asassn_contaminated`: a neighbour within `ASASSN_RADIUS_ARCSEC` brighter than G +
  `ASASSN_DELTA_G`;
- `tess_contaminated`: any source in the 120″ TESS field brighter than G + `TESS_DELTA_G`,
  or all of the field's sources together carrying more than `TESS_MAX_FLUX_RATIO` of the
  Target's flux. The field reaches past the 20″ neighbour list, so it is judged from the
  catalog join's count, brightest G and combined G, and names no single neighbour.

A contaminated survey still runs through the periodogram but counts at half weight in
Agreement. Each contamination carries the sentence VSX asks for in Remarks (VSX FAQ) and the
share of the aperture's light that is not the Target's, from every source inside the radius,
so a COMBLEND-style range correction can be added later. Deblending the photometry is out of
scope.

ZTF's zero points (NEU-1798). Each field a star fell in is a separate oid with its own zero
point; the fetcher merged them by shifting each to the reference oid's median. A neighbour
whose light sits in one field's photometry and not another's shows as a difference between
the zero points. The medians cannot show it: a variable star's fields sample its cycle
unevenly, so on large-amplitude stars their medians differ by a tenth of a magnitude with
the zero points identical. So each band is folded at the period its alias test settled on,
cut into the same phase bins, and each non-reference oid is compared with a template made of
the band's other oids, bin by bin: the median residual is what the median shift got wrong at
equal phase, and the shift less that residual is the oid's zero point. A band whose fold is
not trustworthy (no best period, or phase dispersion above `agreement.MAX_SCATTER`) is not
measured and not flagged: it does not vote in Agreement, and the neighbour rule still covers
real blends.

The same residual is what `realign` takes off (NEU-1802): the pipeline moves each measured
oid's points by it, so the band's oids sit at the reference's zero point at equal phase and
the plots and the magnitude range read one curve. Unmeasured oids keep the fetcher's shift,
and the Cache file keeps the fetcher's alignment.
"""

from collections.abc import Iterable
from dataclasses import replace

import numpy as np

from argelander.analysis.agreement import MAX_SCATTER
from argelander.analysis.model import (
    AliasTest,
    Blend,
    BlendFlag,
    Contamination,
    ZeroPointOffsets,
)
from argelander.catalog.gaia import TESS_FIELD_RADIUS_ARCSEC, GaiaRecord, Neighbour
from argelander.fetch.base import TessField
from argelander.lightcurve import LightCurve

# Wider than VSX's ~2″ for ZTF and ~0.7″ for Gaia G (§5.1): a margin over ZTF's seeing, and
# Gaia's BP and RP windows (3.5″ × 2.1″) take in light that G resolves.
BLEND_RADIUS_ARCSEC = 3.0
BLEND_DELTA_G = 2.0
ASASSN_RADIUS_ARCSEC = 17.0
ASASSN_DELTA_G = 2.0
TESS_DELTA_G = 1.0
TESS_MAX_FLUX_RATIO = 0.5
# An oid zero-point difference above this is more than ZTF's calibration explains.
BLEND_OFFSET_MAG = 0.05
# Points an oid needs in bins its band's other oids populate before its offset is measured.
MIN_OID_POINTS = 10

# How each survey is named in Remarks.
SURVEY_NAMES = {"ztf": "ZTF", "gaia": "Gaia", "asassn": "ASAS-SN", "tess": "TESS"}
NOT_CORRECTED = "Range not corrected."


def blend(
    record: GaiaRecord,
    curves: Iterable[LightCurve] = (),
    alias_tests: Iterable[AliasTest] = (),
) -> Blend:
    """Every survey whose photometry of `record`'s Target is contaminated.

    `curves` are the Target's cleaned Light Curves and `alias_tests` their alias tests; only
    the ZTF bands are read from them, for their zero points. A Target without a G magnitude
    is judged by the ZTF zero points alone.
    """
    tests = {(t.survey, t.band): t for t in alias_tests}
    zero_points = tuple(
        measured
        for lc in curves
        if lc.survey == "ztf" and (lc.survey, lc.band) in tests
        if (measured := zero_point_offsets(lc, tests[lc.survey, lc.band])) is not None
    )
    suspect = [o for z in zero_points if z.suspect for o in z.offsets.values()]

    g = record.g
    near = wide = ()
    if g is not None:
        near = _offending(record.neighbours, g, BLEND_RADIUS_ARCSEC, BLEND_DELTA_G)
        wide = _offending(record.neighbours, g, ASASSN_RADIUS_ARCSEC, ASASSN_DELTA_G)

    contaminations: list[Contamination] = []
    if g is not None and near:
        contaminations += [
            _from_neighbours(
                record.neighbours, g, survey, "blend_suspect", near, BLEND_RADIUS_ARCSEC
            )
            for survey in ("ztf", "gaia")
        ]
    elif suspect:
        largest = max(abs(o) for o in suspect)
        contaminations.append(
            Contamination(
                survey="ztf",
                flag="blend_suspect",
                neighbours=(),
                flux_fraction=None,
                remark=(
                    "ZTF magnitudes possibly contaminated: the zero points of its fields "
                    f"differ by {largest:.2f} mag at equal phase. {NOT_CORRECTED}"
                ),
            )
        )
    if g is not None and wide:
        contaminations.append(
            _from_neighbours(
                record.neighbours, g, "asassn", "asassn_contaminated", wide, ASASSN_RADIUS_ARCSEC
            )
        )
    if g is not None and (tess := _from_tess_field(g, record.tess_field)) is not None:
        contaminations.append(tess)
    return Blend(contaminations=tuple(contaminations), zero_points=zero_points)


def zero_point_offsets(lc: LightCurve, alias_test: AliasTest) -> ZeroPointOffsets | None:
    """A cleaned ZTF band's oid zero points compared at equal phase, at its alias test's best
    period; None when the band has under two oids or no trustworthy fold."""
    best = alias_test.best
    if lc.oid is None or best is None or best.scatter > MAX_SCATTER:
        return None
    oids = np.unique(lc.oid)
    if oids.size < 2:
        return None
    reference = lc.meta["reference_oid"]
    shifts = lc.meta["zero_point_offsets"]
    # The alias test's cut (`aliases._fold`), so the bins are the ones its scatter was from.
    phase = ((lc.time - lc.time[0]) / best.period) % 1.0
    bins = np.minimum((phase * best.bins).astype(np.intp), best.bins - 1)

    offsets: dict[str, float] = {}
    realignment: dict[str, float] = {}
    unmeasured: list[str] = []
    for oid in oids:
        if str(oid) == reference:
            continue
        own = lc.oid == oid
        template = _binned_median(bins[~own], lc.value[~own], best.bins)
        residual = lc.value[own] - template[bins[own]]
        residual = residual[np.isfinite(residual)]
        if residual.size < MIN_OID_POINTS:
            unmeasured.append(str(oid))
            continue
        realignment[str(oid)] = -float(np.median(residual))
        offsets[str(oid)] = shifts[str(oid)] + realignment[str(oid)]
    return ZeroPointOffsets(
        band=lc.band,
        period=best.period,
        bins=best.bins,
        reference_oid=reference,
        offsets=offsets,
        realignment=realignment,
        unmeasured=tuple(unmeasured),
        suspect=any(abs(o) > BLEND_OFFSET_MAG for o in offsets.values()),
    )


def realign(lc: LightCurve, zero_points: Iterable[ZeroPointOffsets]) -> LightCurve:
    """`lc` with each measured oid's points moved to the reference's zero point at equal
    phase; `lc` itself when it is not a ZTF band with a measured oid.

    `meta["zero_point_offsets"]` then holds the measured offsets, which is what has been
    added to those oids' raw points. With more than two oids each was measured against the
    others as the fetcher merged them, and all are moved at once rather than in turn, which
    leaves an error of the second order in the corrections.
    """
    if lc.survey != "ztf" or lc.oid is None:
        return lc
    measured = next((z for z in zero_points if z.band == lc.band), None)
    if measured is None or not measured.realignment:
        return lc
    value = lc.value.copy()
    for oid, shift in measured.realignment.items():
        value[lc.oid == int(oid)] += shift
    shifts = {**lc.meta["zero_point_offsets"], **measured.offsets}
    return replace(lc, value=value, meta={**lc.meta, "zero_point_offsets": shifts})


def _binned_median(bins: np.ndarray, value: np.ndarray, n_bins: int) -> np.ndarray:
    """The median of `value` in each of `n_bins` phase bins; NaN where a bin is empty."""
    medians = np.full(n_bins, np.nan)
    for b in np.unique(bins):
        medians[b] = np.median(value[bins == b])
    return medians


def _offending(
    neighbours: tuple[Neighbour, ...], g: float, radius: float, delta_g: float
) -> tuple[Neighbour, ...]:
    """The neighbours within `radius` brighter than the Target's `g` + `delta_g`."""
    return tuple(
        n for n in neighbours if n.separation <= radius and n.g is not None and n.g < g + delta_g
    )


def _from_neighbours(
    neighbours: tuple[Neighbour, ...],
    g: float,
    survey: str,
    flag: BlendFlag,
    offending: tuple[Neighbour, ...],
    radius: float,
) -> Contamination:
    inside = [n.g for n in neighbours if n.separation <= radius and n.g is not None]
    named = " and ".join(
        f'Gaia DR3 {n.source_id} (G= {n.g:.1f}; sep. {n.separation:.1f}")' for n in offending
    )
    return Contamination(
        survey=survey,
        flag=flag,
        neighbours=offending,
        flux_fraction=_flux_fraction(g, sum(_flux(m) for m in inside)),
        remark=f"{SURVEY_NAMES[survey]} magnitudes contaminated by {named}. {NOT_CORRECTED}",
    )


def _from_tess_field(g: float, field: TessField) -> Contamination | None:
    """TESS's contamination by the sources in its field, or None when they are too faint."""
    brightest, combined = field.brightest_g, field.combined_g
    if brightest is None or combined is None:
        return None
    if brightest >= g + TESS_DELTA_G and _flux(combined) <= TESS_MAX_FLUX_RATIO * _flux(g):
        return None
    sources = "source" if field.n_sources == 1 else "sources"
    return Contamination(
        survey="tess",
        flag="tess_contaminated",
        neighbours=(),
        flux_fraction=_flux_fraction(g, _flux(combined)),
        remark=(
            f"TESS magnitudes contaminated by {field.n_sources} Gaia DR3 {sources} within "
            f'{TESS_FIELD_RADIUS_ARCSEC:.0f}" (brightest G= {brightest:.1f}; '
            f"combined G= {combined:.1f}). {NOT_CORRECTED}"
        ),
    )


def _flux(g: float) -> float:
    """G flux relative to a G 0 source."""
    return 10.0 ** (-0.4 * g)


def _flux_fraction(target_g: float, other_flux: float) -> float:
    return other_flux / (_flux(target_g) + other_flux)
