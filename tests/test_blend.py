"""The per-survey blend checks (spec §6 step 7), on constructed neighbours and recorded Gaia data.

A Target is G 14.0 throughout the constructed cases, so "G + 1" is a G 15.0 neighbour. The
ZTF zero-point cases merge two oids of a constructed variable the way the fetcher does, and
the re-alignment cases (NEU-1802) check the merged curve against the sine it was drawn from.
"""

from dataclasses import replace

import numpy as np
import pytest

from argelander.analysis.agreement import MAX_SCATTER
from argelander.analysis.aliases import alias_test
from argelander.analysis.blend import BLEND_OFFSET_MAG, blend, realign, zero_point_offsets
from argelander.analysis.clean import clean
from argelander.analysis.model import AliasTest, Hypothesis
from argelander.analysis.pipeline import analyze
from argelander.catalog.gaia import GaiaRecord, Neighbour
from argelander.fetch.base import TessField
from argelander.lightcurve import LightCurve

TARGET_G = 14.0
# Recorded Gaia sources (tests/test_catalog_gaia.py).
RRAB = 1418010841782026880
ECL_NO_2MASS = 1435149135683337216
CONSTANT_BRIGHT_FIELD = 1422733312942999424
PERIOD = 0.6  # days, of the constructed ZTF variable
EPOCH = 2459000.5  # BJD, its phase zero
AMPLITUDE = 0.5  # mag, semi-amplitude
REFERENCE, OTHER = 797112400008865, 1830101400004824


def record(*neighbours: Neighbour, tess_field: TessField | None = None) -> GaiaRecord:
    """A G 14 Target with `neighbours`; its TESS field holds just them unless given."""
    if tess_field is None:
        tess_field = field_of(*neighbours)
    return GaiaRecord(
        source_id=1,
        ra=270.0,
        dec=60.0,
        g=TARGET_G,
        bp_rp=0.8,
        parallax=1.0,
        parallax_error=0.05,
        ruwe=1.0,
        in_vari_summary=True,
        vari_g_trimmed_range=0.5,
        vari_class=None,
        vari_class_score=None,
        vari_subclass=None,
        vari_periods={},
        neighbours=neighbours,
        tess_field=tess_field,
        cross_ids={},
    )


def field_of(*sources: Neighbour) -> TessField:
    """The TESS field the catalog join would report for `sources`."""
    mags = [n.g for n in sources if n.g is not None]
    if not mags:
        return TessField(len(sources), None, None)
    combined = -2.5 * np.log10(np.sum(10 ** (-0.4 * np.array(mags))))
    return TessField(len(sources), min(mags), float(combined))


def ztf_curve(*, zero_point: float = 0.0, band: str = "g") -> LightCurve:
    """A G 14 sinusoid seen in two oids, merged by medians as the fetcher merges them.

    The reference oid samples the whole cycle; the other only the phases 0–0.4, near the
    peak of the sine, so its median is fainter by a tenth of a magnitude with its zero point
    identical. `zero_point` adds a true offset to the other oid's raw magnitudes.
    """
    rng = np.random.default_rng(1798)

    def sampled(n: int, phase_high: float) -> tuple[np.ndarray, np.ndarray]:
        cycles = rng.integers(0, 2000, n)
        phase = rng.uniform(0.0, phase_high, n)
        time = EPOCH + (cycles + phase) * PERIOD
        mag = 14.0 + AMPLITUDE * np.sin(2 * np.pi * phase) + rng.normal(0, 0.01, n)
        return time, mag

    ref_time, ref_mag = sampled(300, 1.0)
    other_time, other_mag = sampled(150, 0.4)
    other_mag = other_mag + zero_point
    shift = float(np.median(ref_mag) - np.median(other_mag))
    time = np.concatenate([ref_time, other_time])
    value = np.concatenate([ref_mag, other_mag + shift])
    oid = np.concatenate([np.full(300, REFERENCE), np.full(150, OTHER)])
    order = np.argsort(time)
    return LightCurve(
        survey="ztf",
        source_key=f"{REFERENCE}+{OTHER}",
        band=band,
        time=time[order],
        value=value[order],
        error=np.full(time.size, 0.01),
        quality=np.zeros(time.size, dtype=np.int32),
        value_kind="mag",
        meta={"reference_oid": str(REFERENCE), "zero_point_offsets": {str(OTHER): shift}},
        oid=oid[order],
    )


def folded(lc: LightCurve, *, scatter: float = 0.1) -> AliasTest:
    """An alias test whose best fold is `lc` at `PERIOD`, 20 bins, with `scatter`."""
    best = Hypothesis(
        period=PERIOD, candidate=PERIOD, relation="P", bins=20, scatter=scatter, score=0.0
    )
    return AliasTest(survey=lc.survey, band=lc.band, ranking=(best,))


def off_the_sine(lc: LightCurve, oid: int) -> float:
    """The median of `oid`'s points less the sine they were drawn from, at their own phases."""
    assert lc.oid is not None
    own = lc.oid == oid
    phase = ((lc.time[own] - EPOCH) / PERIOD) % 1.0
    return float(np.median(lc.value[own] - 14.0 - AMPLITUDE * np.sin(2 * np.pi * phase)))


def test_a_neighbour_at_2_arcsec_and_g_plus_1_flags_blend_suspect() -> None:
    near = Neighbour(source_id=7, separation=2.0, g=TARGET_G + 1)

    found = blend(record(near))

    assert "blend_suspect" in found.flags
    assert {"ztf", "gaia"} <= found.surveys
    ztf = next(c for c in found.contaminations if c.survey == "ztf")
    assert ztf.neighbours == (near,)


def test_a_neighbour_at_4_arcsec_does_not_flag_blend_suspect() -> None:
    found = blend(record(Neighbour(source_id=7, separation=4.0, g=TARGET_G + 1)))

    assert "blend_suspect" not in found.flags
    assert not {"ztf", "gaia"} & found.surveys


def test_a_neighbour_fainter_than_g_plus_2_is_no_blend() -> None:
    found = blend(record(Neighbour(source_id=7, separation=1.0, g=TARGET_G + 2.5)))

    assert found.flags == frozenset()
    assert found.contaminations == ()


def test_a_neighbour_at_10_arcsec_and_g_plus_1_contaminates_asassn_only() -> None:
    found = blend(record(Neighbour(source_id=7, separation=10.0, g=TARGET_G + 1)))

    assert found.flags == {"asassn_contaminated"}
    assert found.surveys == {"asassn"}


def test_the_10_arcsec_case_produces_vsx_remarks_sentence() -> None:
    neighbour = Neighbour(source_id=1234567890123456789, separation=10.04, g=15.0)

    found = blend(record(neighbour))

    assert found.remark("asassn") == (
        'ASAS-SN magnitudes contaminated by Gaia DR3 1234567890123456789 (G= 15.0; sep. 10.0"). '
        "Range not corrected."
    )
    # The neighbour holds 10^(-0.4) of the Target's flux.
    (asassn,) = found.contaminations
    ratio = 10**-0.4
    assert asassn.flux_fraction == pytest.approx(ratio / (1 + ratio))


def test_a_g_minus_1_source_at_90_arcsec_contaminates_tess_only() -> None:
    # Outside the 20″ neighbour list, so only the TESS field sees it.
    field = TessField(n_sources=1, brightest_g=TARGET_G - 1, combined_g=TARGET_G - 1)

    found = blend(record(tess_field=field))

    assert found.flags == {"tess_contaminated"}
    assert found.surveys == {"tess"}
    (tess,) = found.contaminations
    assert tess.neighbours == ()
    ratio = 10**0.4
    assert tess.flux_fraction == pytest.approx(ratio / (1 + ratio))
    assert found.remark("tess") == (
        'TESS magnitudes contaminated by 1 Gaia DR3 source within 120" '
        "(brightest G= 13.0; combined G= 13.0). Range not corrected."
    )


def test_many_faint_sources_summing_past_half_the_targets_flux_contaminate_tess() -> None:
    # Each G + 1.5 source is fainter than G + 1, but four of them carry ~1.0 of its flux.
    faint = [Neighbour(source_id=i, separation=60.0 + i, g=TARGET_G + 1.5) for i in range(4)]
    field = field_of(*faint)

    assert blend(record(tess_field=field)).flags == {"tess_contaminated"}
    assert blend(record(tess_field=field_of(*faint[:1]))).flags == frozenset()


def test_uneven_phase_sampling_is_not_a_zero_point_offset() -> None:
    lc = ztf_curve()
    assert abs(lc.meta["zero_point_offsets"][str(OTHER)]) > BLEND_OFFSET_MAG

    found = blend(record(), [lc], [folded(lc)])

    assert found.contaminations == ()
    (measured,) = found.zero_points
    assert (measured.band, measured.period, measured.bins) == ("g", PERIOD, 20)
    assert measured.reference_oid == str(REFERENCE)
    assert measured.offsets[str(OTHER)] == pytest.approx(0.0, abs=0.01)
    assert measured.unmeasured == ()
    assert not measured.suspect


def test_a_constant_zero_point_offset_flags_blend_suspect_for_ztf() -> None:
    lc = ztf_curve(zero_point=0.10)

    found = blend(record(), [lc], [folded(lc)])

    assert found.flags == {"blend_suspect"}
    assert found.surveys == {"ztf"}
    (ztf,) = found.contaminations
    assert ztf.neighbours == ()
    assert ztf.flux_fraction is None
    assert ztf.remark == (
        "ZTF magnitudes possibly contaminated: the zero points of its fields differ by "
        "0.10 mag at equal phase. Range not corrected."
    )
    (measured,) = found.zero_points
    # The magnitudes that bring the other oid back to the reference's zero point.
    assert measured.offsets[str(OTHER)] == pytest.approx(-0.10, abs=0.01)
    assert measured.suspect


@pytest.mark.parametrize(
    "case",
    ["single_oid", "no_oid", "loose_fold", "no_fold"],
)
def test_a_band_without_two_oids_or_a_trustworthy_fold_is_not_measured(case: str) -> None:
    lc = ztf_curve(zero_point=0.10)
    test = folded(lc)
    if case == "single_oid":
        assert lc.oid is not None
        lc = replace(lc, oid=np.full(lc.time.size, REFERENCE))
    elif case == "no_oid":
        lc = replace(lc, oid=None)
    elif case == "loose_fold":
        test = folded(lc, scatter=MAX_SCATTER + 0.01)
    else:
        test = AliasTest(survey="ztf", band="g", ranking=())

    found = blend(record(), [lc], [test])

    assert zero_point_offsets(lc, test) is None
    assert found.zero_points == ()
    assert found.contaminations == ()
    assert realign(lc, found.zero_points) is lc


def test_an_oid_with_too_few_points_at_shared_phases_is_unmeasured() -> None:
    lc = ztf_curve(zero_point=0.10)
    assert lc.oid is not None
    keep = (lc.oid == REFERENCE) | (np.cumsum(lc.oid == OTHER) <= 9)
    few = replace(
        lc,
        time=lc.time[keep],
        value=lc.value[keep],
        error=lc.error[keep],
        quality=lc.quality[keep],
        oid=lc.oid[keep],
    )

    measured = zero_point_offsets(few, folded(few))

    assert measured is not None
    assert measured.offsets == {}
    assert measured.unmeasured == (str(OTHER),)
    assert not measured.suspect


def test_ztf_curves_without_alias_tests_are_not_judged() -> None:
    assert blend(record(), [ztf_curve(zero_point=0.10)]) == blend(record())


def test_a_target_without_g_is_judged_by_ztf_zero_points_alone() -> None:
    near = Neighbour(source_id=7, separation=1.0, g=10.0)
    no_g = replace(record(near), g=None)
    lc = ztf_curve(zero_point=0.10)

    assert blend(no_g).contaminations == ()
    assert blend(no_g, [lc], [folded(lc)]).surveys == {"ztf"}


@pytest.mark.parametrize("zero_point", [0.0, 0.10])
def test_realign_puts_each_measured_oid_at_the_references_zero_point(zero_point: float) -> None:
    lc = ztf_curve(zero_point=zero_point)
    (measured,) = blend(record(), [lc], [folded(lc)]).zero_points
    shift = lc.meta["zero_point_offsets"][str(OTHER)]
    # The fetcher's median shift left the other oid a tenth of a magnitude off the sine.
    assert abs(off_the_sine(lc, OTHER)) > 0.05

    realigned = realign(lc, [measured])

    assert off_the_sine(realigned, REFERENCE) == pytest.approx(0.0, abs=0.005)
    assert off_the_sine(realigned, OTHER) == pytest.approx(0.0, abs=0.005)
    assert measured.realignment == {str(OTHER): pytest.approx(measured.offsets[str(OTHER)] - shift)}
    assert realigned.oid is not None and lc.oid is not None
    reference = realigned.oid == REFERENCE
    assert np.array_equal(realigned.value[reference], lc.value[lc.oid == REFERENCE])
    # The meta still says what was added to each oid's raw points, now the measured offset.
    assert realigned.meta["zero_point_offsets"] == {
        str(OTHER): pytest.approx(measured.offsets[str(OTHER)])
    }
    # Measured again, the curve needs no further re-alignment.
    again = zero_point_offsets(realigned, folded(realigned))
    assert again is not None
    assert again.realignment[str(OTHER)] == pytest.approx(0.0, abs=0.005)


def test_realign_leaves_unmeasured_oids_and_other_bands_alone() -> None:
    lc = ztf_curve(zero_point=0.10)
    (measured,) = blend(record(), [lc], [folded(lc)]).zero_points
    unmeasured = replace(measured, offsets={}, realignment={}, unmeasured=(str(OTHER),))

    other_band = replace(lc, band="r")

    assert realign(lc, [unmeasured]) is lc
    assert realign(other_band, [measured]) is other_band
    assert realign(lc, []) is lc


def test_the_analyzer_draws_both_oids_on_one_curve() -> None:
    lc = ztf_curve()
    assert abs(off_the_sine(lc, OTHER)) > 0.05

    analysis, (cleaned,) = analyze([lc], record(), target_id=1)

    assert analysis.blend is not None
    (measured,) = analysis.blend.zero_points
    assert measured.period == pytest.approx(PERIOD, rel=1e-3)
    assert off_the_sine(cleaned, REFERENCE) == pytest.approx(0.0, abs=0.005)
    assert off_the_sine(cleaned, OTHER) == pytest.approx(0.0, abs=0.005)
    # The best fold is scored again on the re-aligned curve, which folds more tightly.
    (test,) = analysis.alias_tests
    best = test.best
    assert best is not None
    candidate, relation = best.candidate, best.relation

    def scored(curve: LightCurve) -> Hypothesis:
        ranking = alias_test(curve, [candidate]).ranking
        return next(h for h in ranking if h.relation == relation)

    assert best == scored(cleaned)
    assert best.scatter < scored(clean(lc)).scatter


def test_recorded_gaia_sources(recorded_gaia: dict[int, GaiaRecord]) -> None:
    records = recorded_gaia
    # A G 14.9 neighbour at 1.8″ of a G 14.1 Target.
    assert "blend_suspect" in blend(records[ECL_NO_2MASS]).flags
    # A G 10.7 source 62″ from a G 13.0 Target.
    assert blend(records[CONSTANT_BRIGHT_FIELD]).flags == {"tess_contaminated"}
    assert blend(records[RRAB]).flags <= {"tess_contaminated"}
