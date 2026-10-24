"""The colour-magnitude position (spec §6 step 8), on constructed and recorded Gaia values."""

import pytest

from argelander.analysis.cmd import cmd_position, region
from argelander.catalog.gaia import GaiaRecord
from argelander.fetch.base import TessField

# Recorded Gaia sources (tests/test_catalog_gaia.py).
RRAB = 1418010841782026880
DSCT = 1438034941388614528


def target(
    *, bp_rp: float | None = 0.8, parallax: float | None = 1.0, error: float | None = 0.1
) -> GaiaRecord:
    """A G 14 Target."""
    return GaiaRecord(
        source_id=1,
        ra=270.0,
        dec=60.0,
        g=14.0,
        bp_rp=bp_rp,
        parallax=parallax,
        parallax_error=error,
        ruwe=1.0,
        in_vari_summary=False,
        vari_g_trimmed_range=None,
        vari_class=None,
        vari_class_score=None,
        vari_subclass=None,
        vari_periods={},
        neighbours=(),
        tess_field=TessField(0, None, None),
        cross_ids={},
    )


def test_a_significant_parallax_gives_absolute_g_and_distance() -> None:
    # G 14 at 1 mas (1 kpc) is M_G 4.
    position = cmd_position(target())

    assert position.abs_g == pytest.approx(4.0)
    assert position.distance_pc == pytest.approx(1000.0)
    assert position.bp_rp == 0.8
    assert position.region == "main_sequence"


def test_an_insignificant_parallax_gives_no_absolute_magnitude_and_region_unknown() -> None:
    # parallax / error = 5 exactly, which is not above 5.
    position = cmd_position(target(parallax=0.5, error=0.1))

    assert position.abs_g is None
    assert position.distance_pc is None
    assert position.bp_rp == 0.8
    assert position.region == "unknown"


@pytest.mark.parametrize(("parallax", "error"), [(None, 0.1), (1.0, None), (1.0, 0.0), (-1.0, 0.1)])
def test_a_missing_or_unusable_parallax_gives_region_unknown(
    parallax: float | None, error: float | None
) -> None:
    position = cmd_position(target(parallax=parallax, error=error))

    assert position.abs_g is None
    assert position.region == "unknown"


def test_no_colour_gives_region_unknown_but_keeps_absolute_g() -> None:
    position = cmd_position(target(bp_rp=None))

    assert position.abs_g is not None
    assert position.region == "unknown"


def test_a_known_rr_lyrae_lands_in_the_instability_strip(
    recorded_gaia: dict[int, GaiaRecord],
) -> None:
    # CSS_J173907.6+550053, an RRAB: BP−RP 0.56, parallax / error 8, M_G 0.26.
    position = cmd_position(recorded_gaia[RRAB])

    assert position.abs_g == pytest.approx(0.256, abs=1e-3)
    assert position.region == "instability_strip"


def test_a_gaia_delta_scuti_candidate_lands_in_the_instability_strip(
    recorded_gaia: dict[int, GaiaRecord],
) -> None:
    # Gaia class DSCT|GDOR|SXPHE: BP−RP 0.33, M_G 2.57, at the main sequence's upper end.
    assert cmd_position(recorded_gaia[DSCT]).region == "instability_strip"


@pytest.mark.parametrize(
    ("bp_rp", "abs_g", "expected"),
    [
        (0.82, 4.67, "main_sequence"),  # the Sun
        (2.5, 10.5, "main_sequence"),  # an M dwarf
        (0.6, 3.0, "main_sequence"),  # an F-type W UMa binary, overluminous by its two stars
        (0.55, 2.5, "main_sequence"),
        (1.2, 0.5, "giant_branch"),  # the red clump
        (2.5, -2.5, "giant_branch"),  # the upper red giant branch
        (0.9, -4.0, "instability_strip"),  # a classical Cepheid
        (0.0, 11.5, "white_dwarf"),
        (-0.4, 4.5, "unknown"),  # a hot subdwarf, between the sequences
    ],
)
def test_regions(bp_rp: float, abs_g: float, expected: str) -> None:
    assert region(bp_rp, abs_g) == expected
