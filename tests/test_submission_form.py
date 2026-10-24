"""VSX revision-form values (spec §9.1) that the plots already print."""

import pytest

from argelander.submission.form import PASSBANDS, format_period


@pytest.mark.parametrize(
    ("survey", "band", "code"),
    [
        ("ztf", "g", "g"),
        ("ztf", "r", "r"),
        ("gaia", "G", "G"),
        ("asassn", "V", "V"),
        ("tess", "T", "TESS"),
    ],
)
def test_survey_bands_map_to_the_forms_passbands(survey: str, band: str, code: str) -> None:
    assert PASSBANDS[(survey, band)] == code


def test_gaia_bp_and_rp_have_no_passband() -> None:
    assert ("gaia", "BP") not in PASSBANDS
    assert ("gaia", "RP") not in PASSBANDS


@pytest.mark.parametrize(
    ("period", "baseline", "text"),
    [
        (0.5123456, 1825.0, "0.512346"),  # ZTF's five years fix six decimals at half a day
        (0.5123456, 27.0, "0.5123"),  # one TESS sector, four
        (12.3456789, 1825.0, "12.346"),
        (312.4, 1825.0, "312"),  # a Mira over five years: whole days
        (0.0612345, 1825.0, "0.06123450"),  # a δ Scuti star over five years
    ],
)
def test_period_keeps_the_decimals_the_baseline_determines(
    period: float, baseline: float, text: str
) -> None:
    assert format_period(period, baseline) == text


def test_the_last_decimal_kept_drifts_the_fold_by_at_most_a_hundredth_of_a_cycle() -> None:
    period, baseline = 0.3333333, 2000.0
    rounded = float(format_period(period, baseline))
    assert baseline * abs(rounded - period) / period**2 <= 0.01


@pytest.mark.parametrize(("period", "baseline"), [(0.0, 100.0), (1.0, 0.0), (-1.0, 100.0)])
def test_period_and_baseline_must_be_positive(period: float, baseline: float) -> None:
    with pytest.raises(ValueError):
        format_period(period, baseline)
