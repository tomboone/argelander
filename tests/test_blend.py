"""The per-survey blend checks (spec §6 step 7), on constructed neighbours and recorded Gaia data.

A Target is G 14.0 throughout the constructed cases, so "G + 1" is a G 15.0 neighbour.
"""

from dataclasses import replace

import numpy as np
import pytest

from argelander.analysis.blend import blend
from argelander.catalog.gaia import GaiaRecord, Neighbour
from argelander.fetch.base import TessField
from argelander.lightcurve import LightCurve

TARGET_G = 14.0
# Recorded Gaia sources (tests/test_catalog_gaia.py).
RRAB = 1418010841782026880
ECL_NO_2MASS = 1435149135683337216
CONSTANT_BRIGHT_FIELD = 1422733312942999424


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


def ztf_curve(*, blend_suspect: bool) -> LightCurve:
    time = np.linspace(2459000.0, 2459100.0, 5)
    return LightCurve(
        survey="ztf",
        source_key="1+2",
        band="g",
        time=time,
        value=np.full(5, 14.0),
        error=np.full(5, 0.01),
        quality=np.zeros(5, dtype=np.int32),
        value_kind="mag",
        meta={"blend_suspect": blend_suspect},
    )


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


def test_ztf_zero_point_offsets_flag_blend_suspect_for_ztf() -> None:
    found = blend(record(), [ztf_curve(blend_suspect=False), ztf_curve(blend_suspect=True)])

    assert found.flags == {"blend_suspect"}
    assert found.surveys == {"ztf"}
    (ztf,) = found.contaminations
    assert ztf.neighbours == ()
    assert ztf.flux_fraction is None
    assert "Range not corrected." in ztf.remark


def test_ztf_without_offsets_and_no_neighbours_is_clean() -> None:
    assert blend(record(), [ztf_curve(blend_suspect=False)]).contaminations == ()


def test_a_target_without_g_is_judged_by_ztf_offsets_alone() -> None:
    near = Neighbour(source_id=7, separation=1.0, g=10.0)
    no_g = replace(record(near), g=None)

    assert blend(no_g).contaminations == ()
    assert blend(no_g, [ztf_curve(blend_suspect=True)]).surveys == {"ztf"}


def test_recorded_gaia_sources(recorded_gaia: dict[int, GaiaRecord]) -> None:
    records = recorded_gaia
    # A G 14.9 neighbour at 1.8″ of a G 14.1 Target.
    assert "blend_suspect" in blend(records[ECL_NO_2MASS]).flags
    # A G 10.7 source 62″ from a G 13.0 Target.
    assert blend(records[CONSTANT_BRIGHT_FIELD]).flags == {"tess_contaminated"}
    assert blend(records[RRAB]).flags <= {"tess_contaminated"}
