"""The prototype pass criterion (spec §11, NEU-1711), run on the fixture set offline.

Every manifest star goes through the pipeline the notebook runs, clean → periodograms →
aliases → blend → Agreement → CMD, on its trimmed Light Curves and the Gaia catalog join
recorded in `fixtures/catalog/`; `argelander.prototype.judge` applies the criterion. ASAS-SN
is a coverage gap until NEU-1796, so Agreement rests on ZTF g, ZTF r, TESS and Gaia's period.

The classifier's rules (NEU-1720) are held to the same set: at least `MIN_CLASSIFIED` of the 20
positives get their published type as the best-scoring type, and a blend or constant star
scores "not variable" or no type above 0.5. Their Proposals (NEU-1721) read the rise or
eclipse duration, the range and the epoch off the binned phase curve as VSX asks.

BoxLeastSquares over ZTF's 7.5-year baseline is the slow step, so the stars run in parallel,
once for the module. The tests that need them are marked `gate`, which CI runs
as its own job, only when the science can have changed (NEU-1838); `task test` runs them.
"""

import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pytest
import yaml
from astropy.table import Table

from argelander.analysis.model import Agreement, Analysis, Blend, Contamination
from argelander.analysis.pipeline import analyze
from argelander.analysis.shape import magnitudes
from argelander.catalog.gaia import GaiaRecord, records_from_tables
from argelander.classify import registry, types
from argelander.classify.confidence import best_type, scores
from argelander.classify.propose import Elements, elements, range_curve
from argelander.classify.rules.gaia import target_gaia_types
from argelander.db.models import Target
from argelander.lightcurve import LightCurve, load_target
from argelander.plots import render_all
from argelander.plots.phase import MAX_BINS, MIN_BINS, POINTS_PER_BIN, binned_median, fold
from argelander.prototype import judge, relation
from argelander.selector import gaia_columns

FIXTURES = Path(__file__).parent / "fixtures"
LIGHT_CURVES = FIXTURES / "lightcurves"
CATALOG_TABLES = ("catalog", "cross_ids", "neighbours", "tess_field")
STARS = yaml.safe_load((FIXTURES / "manifest.yaml").read_text())

# Stars the pipeline is known to fail, with why. Strict: one that starts passing fails the
# run, so the entry is removed in the change that fixed it.
KNOWN_FAILURES: dict[str, str] = {}

# Positives the classifier proposes another type for, with why; strict, as above.
LOW_AMPLITUDE_SR = (
    "A {0} mag red giant at {1} d: RS fits its period, amplitude, colour and place on the "
    "giant branch as well as SR does, and SR's usual 1–2 mag amplitude opposes SR."
)
KNOWN_MISCLASSIFIED: dict[str, str] = {
    "ASASSN-V J194707.50+750321.7": LOW_AMPLITUDE_SR.format(0.15, 55.1) + " Gaia says RS.",
    "ASASSN-V J192915.72+664402.8": LOW_AMPLITUDE_SR.format(0.07, 42.3),
}
MIN_CLASSIFIED = 16  # of the 20 positives


def catalog() -> dict[int, GaiaRecord]:
    return records_from_tables(
        *(
            Table.read(FIXTURES / "catalog" / f"{name}.xml", format="votable")
            for name in CATALOG_TABLES
        )
    )


def run(star: dict) -> tuple[Analysis, list[LightCurve]]:
    gaia_source_id = star["gaia_source_id"]
    return analyze(
        load_target(LIGHT_CURVES, gaia_source_id),
        catalog().get(gaia_source_id),
        target_id=gaia_source_id,
    )


@pytest.fixture(scope="module")
def analyses() -> dict[str, tuple[Analysis, list[LightCurve]]]:
    """Every star's Analysis and cleaned Light Curves, by star name."""
    with ProcessPoolExecutor(max_workers=os.cpu_count()) as pool:
        return dict(zip((s["star"] for s in STARS), pool.map(run, STARS), strict=True))


def gate_case(star: dict, known: dict[str, str] = KNOWN_FAILURES) -> object:
    reason = known.get(star["star"])
    marks = [pytest.mark.xfail(reason=reason, strict=True)] if reason else []
    return pytest.param(star, id=star["star"], marks=marks)


def target_of(star: dict) -> Target:
    return Target(
        gaia_source_id=star["gaia_source_id"], **gaia_columns(catalog()[star["gaia_source_id"]])
    )


def classified(star: dict, analysis: Analysis) -> tuple[str | None, dict[str, float]]:
    """The best-scoring type for `star`, and every type's Confidence."""
    target = target_of(star)
    scored = scores(registry.evaluate(analysis, target))
    return best_type(scored, target_gaia_types(target)), scored


def test_known_failures_are_manifest_stars() -> None:
    assert set(KNOWN_FAILURES) <= {s["star"] for s in STARS}


def test_known_misclassifications_are_few_enough_positives() -> None:
    positives = {s["star"] for s in STARS if s["role"] == "positive"}
    assert set(KNOWN_MISCLASSIFIED) <= positives
    assert len(positives) - len(KNOWN_MISCLASSIFIED) >= MIN_CLASSIFIED


def test_catalog_has_a_record_for_every_star() -> None:
    assert set(catalog()) == {s["gaia_source_id"] for s in STARS}


@pytest.mark.gate
@pytest.mark.parametrize("star", [gate_case(s) for s in STARS])
def test_star_passes_the_gate(star: dict, analyses: dict) -> None:
    analysis, _ = analyses[star["star"]]
    result = judge(star, analysis)
    assert result.passed, result.reason


@pytest.mark.gate
@pytest.mark.parametrize(
    "star", [gate_case(s, KNOWN_MISCLASSIFIED) for s in STARS if s["role"] == "positive"]
)
def test_positive_gets_its_published_type(star: dict, analyses: dict) -> None:
    analysis, _ = analyses[star["star"]]
    proposed, scored = classified(star, analysis)
    assert proposed == star["published_type"], scored


@pytest.mark.gate
@pytest.mark.parametrize(
    "star",
    [s for s in STARS if s.get("negative_kind") in ("blend", "constant")],
    ids=lambda s: s["star"],
)
def test_blend_or_constant_scores_not_variable(star: dict, analyses: dict) -> None:
    analysis, _ = analyses[star["star"]]
    proposed, scored = classified(star, analysis)
    assert proposed == types.CONSTANT or max(scored.values()) <= 0.5, scored


@pytest.mark.gate
@pytest.mark.parametrize(
    "star", [s for s in STARS if s.get("negative_kind") == "alias_trap"], ids=lambda s: s["star"]
)
def test_alias_trap_keeps_its_type_at_the_true_period(star: dict, analyses: dict) -> None:
    """An alias trap is a real variable with a wrong published period: it agrees at its true
    period (the gate above) and is classified there, as the type VSX gives it."""
    analysis, _ = analyses[star["star"]]
    proposed, scored = classified(star, analysis)
    assert proposed == star["published_type"], scored


def proposal_of(star: dict, analyses: dict) -> tuple[Elements, LightCurve]:
    """`star`'s Proposal and the band its range and epoch were read from."""
    analysis, cleaned = analyses[star["star"]]
    found, lead = elements(analysis, cleaned, target_of(star)), range_curve(cleaned)
    assert found.proposed_type == star["published_type"], found.proposed_type
    assert lead is not None
    return found, lead


def positives(*codes: str) -> list[dict]:
    """The positives of the given types the classifier names correctly."""
    return [
        s
        for s in STARS
        if s["role"] == "positive"
        and s["published_type"] in codes
        and s["star"] not in KNOWN_MISCLASSIFIED
    ]


# The form's rise/eclipse duration, % of the period: EA's primary eclipse, an RR Lyrae's rise,
# and nothing for EW, whose light changes continuously (VSX Manual).
DURATION_PCT = {"EA": (1.0, 30.0), "RRAB": (10.0, 40.0), "EW": None}


@pytest.mark.gate
@pytest.mark.parametrize("star", positives(*DURATION_PCT), ids=lambda s: s["star"])
def test_proposal_rise_or_eclipse_duration(star: dict, analyses: dict) -> None:
    found, _ = proposal_of(star, analyses)
    expected = DURATION_PCT[star["published_type"]]
    if expected is None:
        assert found.rise_or_eclipse is None
    else:
        assert found.rise_or_eclipse is not None
        assert expected[0] <= 100 * found.rise_or_eclipse <= expected[1]


@pytest.mark.gate
@pytest.mark.parametrize("star", positives("RRAB"), ids=lambda s: s["star"])
def test_proposal_range_is_the_binned_curve_not_its_extreme_points(
    star: dict, analyses: dict
) -> None:
    found, lead = proposal_of(star, analyses)
    _, mag, _ = magnitudes(lead)
    assert found.mag_max is not None and found.mag_min is not None
    assert mag.min() < found.mag_max < np.median(mag) < found.mag_min < mag.max()
    assert found.band == lead.band and not found.min_is_amplitude


@pytest.mark.gate
@pytest.mark.parametrize("star", positives("EA"), ids=lambda s: s["star"])
def test_proposal_epoch_is_at_primary_minimum(star: dict, analyses: dict) -> None:
    """Folded at the Proposal's period and epoch, the binned curve's faintest bin is the
    one that starts or ends at phase 0: the epoch is the faintest bin's centre, and re-cutting
    the fold there moves the bin edges by half a bin."""
    found, lead = proposal_of(star, analyses)
    assert found.period is not None and found.epoch_bjd is not None
    time, mag, _ = magnitudes(lead)
    centres, medians = binned_median(fold(time, found.period, found.epoch_bjd), mag)
    width = 1.0 / int(np.clip(mag.size // POINTS_PER_BIN, MIN_BINS, MAX_BINS))
    faintest = float(centres[medians.argmax()])
    assert min(faintest, 1.0 - faintest) == pytest.approx(width / 2)


@pytest.mark.gate
def test_missing_asassn_is_a_coverage_gap(analyses: dict) -> None:
    """No star has ASAS-SN Light Curves yet; each is analysed on the sources it has."""
    for star in STARS:
        analysis, _ = analyses[star["star"]]
        assert analysis.alias_tests, star["star"]
        assert all(t.survey != "asassn" for t in analysis.alias_tests)


@pytest.mark.gate
def test_a_positive_renders_every_plot(analyses: dict, tmp_path: Path) -> None:
    star = next(s for s in STARS if s["star"] == "V0364 Dra")
    analysis, cleaned = analyses[star["star"]]
    assert analysis.agreement is not None
    paths = render_all(
        analysis,
        cleaned,
        vsx_type=star["published_type"],
        period=analysis.agreement.agreed_period,
        epoch=None,
        out_dir=tmp_path,
    )
    assert {"periodogram", "cmd", "combined", "submission"} <= set(paths)
    assert any(kind.startswith("phase-") for kind in paths)
    assert all(path.stat().st_size > 0 for path in paths.values())


@pytest.mark.parametrize(
    ("period", "reference", "tolerance", "expected"),
    [
        (0.5, 0.5, 1e-3, "1:1"),
        (0.5004, 0.5, 1e-3, "1:1"),
        (0.5006, 0.5, 1e-3, None),
        (0.5006, 0.5, 2e-3, "1:1"),
        (1.0, 0.5, 1e-3, "2:1"),
        (0.25, 0.5, 1e-3, "2:1"),
        (0.75, 0.5, 1e-3, None),
    ],
)
def test_relation(period: float, reference: float, tolerance: float, expected: str | None) -> None:
    assert relation(period, reference, tolerance) == expected


def analysis_at(period: float | None, *, blend_suspect: bool = False) -> Analysis:
    """An Analysis that agreed on `period` (none when None), optionally flagged."""
    analysis = Analysis(target_id=1, pipeline_version="test")
    if period is not None:
        analysis.agreement = Agreement(
            agreed_period=period,
            agreed_sources=("ztf_g", "ztf_r"),
            relation="1:1",
            tolerance=1e-3,
        )
    flags = (
        (Contamination("ztf", "blend_suspect", (), None, ""),)
        if blend_suspect
        else (Contamination("tess", "tess_contaminated", (), None, ""),)
    )
    analysis.blend = Blend(contaminations=flags)
    return analysis


POSITIVE = {"star": "P", "role": "positive", "published_period": 0.5}
TRAP = {
    "star": "T",
    "role": "negative",
    "negative_kind": "alias_trap",
    "published_period": 1 / (1 / 0.3 - 1),
    "true_period": 0.3,
}
CONSTANT = {"star": "C", "role": "negative", "negative_kind": "constant", "published_period": None}
BLEND = {"star": "B", "role": "negative", "negative_kind": "blend", "published_period": 0.5}


@pytest.mark.parametrize(
    ("star", "analysis", "passed"),
    [
        (POSITIVE, analysis_at(0.5), True),
        (POSITIVE, analysis_at(0.25), True),
        (POSITIVE, analysis_at(None), False),
        (POSITIVE, analysis_at(0.6), False),
        (TRAP, analysis_at(None), True),
        (TRAP, analysis_at(0.3), True),
        (TRAP, analysis_at(TRAP["published_period"]), False),
        (TRAP, analysis_at(0.7), False),
        (CONSTANT, analysis_at(None), True),
        (CONSTANT, analysis_at(0.5), False),
        (BLEND, analysis_at(0.5, blend_suspect=True), True),
        (BLEND, analysis_at(None), True),
        (BLEND, analysis_at(0.5), False),
    ],
)
def test_judge(star: dict, analysis: Analysis, passed: bool) -> None:
    result = judge(star, analysis)
    assert result.passed is passed, result.reason
