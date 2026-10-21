"""The prototype pass criterion (spec §11, NEU-1711), run on the fixture set offline.

Every manifest star goes through the pipeline the notebook runs, clean → periodograms →
aliases → blend → Agreement → CMD, on its trimmed Light Curves and the Gaia catalog join
recorded in `fixtures/catalog/`; `argelander.prototype.judge` applies the criterion. ASAS-SN
is a coverage gap until NEU-1796, so Agreement rests on ZTF g, ZTF r, TESS and Gaia's period.

BoxLeastSquares over ZTF's 7.5-year baseline takes most of a minute a star, so the stars run
in parallel, once for the module. The tests that need them are marked `gate`, which CI runs
as its own job, only when the science can have changed (NEU-1838); `task test` runs them.
"""

import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest
import yaml
from astropy.table import Table

from argelander.analysis.model import Agreement, Analysis, Blend, Contamination
from argelander.analysis.pipeline import analyze
from argelander.catalog.gaia import GaiaRecord, records_from_tables
from argelander.lightcurve import LightCurve, load_target
from argelander.plots import render_all
from argelander.prototype import judge, relation

FIXTURES = Path(__file__).parent / "fixtures"
LIGHT_CURVES = FIXTURES / "lightcurves"
CATALOG_TABLES = ("catalog", "cross_ids", "neighbours", "tess_field")
STARS = yaml.safe_load((FIXTURES / "manifest.yaml").read_text())

# Stars the pipeline is known to fail, with why. Strict: one that starts passing fails the
# run, so the entry is removed in the change that fixed it.
KNOWN_FAILURES: dict[str, str] = {}


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


def gate_case(star: dict) -> object:
    reason = KNOWN_FAILURES.get(star["star"])
    marks = [pytest.mark.xfail(reason=reason, strict=True)] if reason else []
    return pytest.param(star, id=star["star"], marks=marks)


def test_known_failures_are_manifest_stars() -> None:
    assert set(KNOWN_FAILURES) <= {s["star"] for s in STARS}


def test_catalog_has_a_record_for_every_star() -> None:
    assert set(catalog()) == {s["gaia_source_id"] for s in STARS}


@pytest.mark.gate
@pytest.mark.parametrize("star", [gate_case(s) for s in STARS])
def test_star_passes_the_gate(star: dict, analyses: dict) -> None:
    analysis, _ = analyses[star["star"]]
    result = judge(star, analysis)
    assert result.passed, result.reason


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
