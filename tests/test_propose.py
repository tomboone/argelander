"""The bands a Proposal reads from (NEU-2185): the range from ASAS-SN V when V is clean, the
epoch from the band with the most points, a tie by a fixed band order."""

from pathlib import Path

import numpy as np
import pytest

from argelander.analysis.model import Agreement, Analysis, Blend, Contamination
from argelander.classify import propose
from argelander.classify.propose import BAND_ORDER, elements, epoch_curve, range_curve
from argelander.db.models import Analysis as AnalysisRow
from argelander.db.models import Proposal, Target
from argelander.lightcurve import LightCurve
from argelander.submission.form import PASSBANDS

PERIOD = 0.5123456  # days
EPOCH = 2456000.1234  # BJD_TDB, a primary minimum
V_START, G_START = 2456000.5, 2458300.5  # ASAS-SN V from 2012, ZTF g from 2018


def curve(
    survey: str, band: str, n: int, start: float = G_START, level: float = 15.0
) -> LightCurve:
    """`n` nightly points of an EA star over five years from `start`, at `level` out of eclipse."""
    rng = np.random.default_rng(n)
    time = np.sort(start + rng.integers(0, 5 * 365, n) + rng.uniform(0.15, 0.4, n))
    phase = ((time - EPOCH) / PERIOD) % 1.0
    primary = np.minimum(phase, 1.0 - phase)
    value = level + 0.6 * np.exp(-0.5 * (primary / 0.03) ** 2) + rng.normal(0, 0.01, n)
    return LightCurve(
        survey=survey,
        source_key="1",
        band=band,
        time=time,
        value=value,
        error=np.full(n, 0.02),
        quality=np.zeros(n, dtype=np.int32),
        value_kind="mag",
        meta={},
    )


def contaminated(survey: str) -> Blend:
    flag = "asassn_contaminated" if survey == "asassn" else "blend_suspect"
    return Blend(contaminations=(Contamination(survey, flag, (), None, ""),))


def test_every_passband_has_a_place_in_the_tie_break_order() -> None:
    assert BAND_ORDER.keys() == PASSBANDS.keys()


def test_the_range_is_read_from_a_clean_asassn_v_curve_with_fewer_points() -> None:
    v, g = curve("asassn", "V", 300, V_START), curve("ztf", "g", 900)

    assert range_curve([g, v], frozenset()) is v
    assert range_curve([g, v], frozenset({"ztf"})) is v
    assert epoch_curve([g, v]) is g


def test_the_range_leaves_asassn_v_when_asassn_is_contaminated() -> None:
    v, g = curve("asassn", "V", 300, V_START), curve("ztf", "g", 900)

    assert range_curve([g, v], frozenset({"asassn"})) is g


def test_the_range_leaves_asassn_v_when_it_has_too_few_points() -> None:
    v, g = curve("asassn", "V", 20, V_START), curve("ztf", "g", 900)

    assert range_curve([g, v], frozenset()) is g


def test_a_tie_in_points_resolves_by_band_order_not_list_order() -> None:
    curves = [
        curve("asassn", "V", 500, V_START),
        curve("asassn", "g", 500, V_START),
        curve("ztf", "r", 500),
        curve("ztf", "g", 500),
    ]

    for ordered in (curves, curves[::-1]):
        timed = epoch_curve(ordered)
        assert timed is not None and (timed.survey, timed.band) == ("ztf", "g")
        lead = range_curve(ordered, frozenset({"asassn"}))
        assert lead is not None and (lead.survey, lead.band) == ("ztf", "g")
    asassn = curves[:2]
    for ordered in (asassn, asassn[::-1]):
        lead = epoch_curve(ordered)
        assert lead is not None and (lead.survey, lead.band) == ("asassn", "g")


def ea_analysis(blend: Blend | None = None) -> Analysis:
    analysis = Analysis(target_id=1, pipeline_version="test")
    analysis.agreement = Agreement(
        agreed_period=PERIOD, agreed_sources=("ztf_g", "asassn_V"), relation="1:1", tolerance=1e-3
    )
    analysis.blend = blend
    return analysis


@pytest.fixture
def as_ea(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every Analysis is proposed as EA, whatever its rules would score."""
    monkeypatch.setattr(propose.registry, "evaluate", lambda analysis, target: [])
    monkeypatch.setattr(propose, "proposed_type", lambda best, agreement: "EA")


@pytest.mark.usefixtures("as_ea")
def test_the_range_comes_from_v_and_the_epoch_from_the_band_with_the_most_points() -> None:
    v = curve("asassn", "V", 300, V_START, level=14.0)
    g = curve("ztf", "g", 900, level=15.0)

    found = elements(ea_analysis(), [g, v], Target(gaia_g=14.5))

    assert found.band == "V"
    assert found.mag_max == pytest.approx(14.0, abs=0.05)
    assert found.epoch_bjd is not None
    assert g.time[0] <= found.epoch_bjd <= g.time[-1]
    cycles = (found.epoch_bjd - EPOCH) / PERIOD
    assert cycles - round(cycles) == pytest.approx(0.0, abs=0.05)


@pytest.mark.usefixtures("as_ea")
def test_a_contaminated_asassn_leaves_the_range_on_the_epoch_band() -> None:
    v = curve("asassn", "V", 300, V_START, level=14.0)
    g = curve("ztf", "g", 900, level=15.0)

    found = elements(ea_analysis(contaminated("asassn")), [g, v], Target(gaia_g=14.5))

    assert found.band == "g"
    assert found.mag_max == pytest.approx(15.0, abs=0.05)


def test_submission_png_leads_with_the_range_band(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    v, g = curve("asassn", "V", 300, V_START), curve("ztf", "g", 900)
    drawn: dict = {}

    def render_all(*args, chosen, **kwargs) -> dict[str, Path]:
        drawn["chosen"] = chosen
        return {}

    monkeypatch.setattr(propose, "render_all", render_all)
    row = AnalysisRow(pipeline_version="test")
    proposal = Proposal(proposed_type="EA", period=PERIOD, epoch_bjd=EPOCH)

    propose.draw(row, proposal, ea_analysis(), [g, v], 1, tmp_path)
    assert drawn["chosen"] == ("asassn", "V")

    propose.draw(row, proposal, ea_analysis(contaminated("asassn")), [g, v], 1, tmp_path)
    assert drawn["chosen"] == ("ztf", "g")
