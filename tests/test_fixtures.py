"""The prototype fixture set: the manifest, the trimmed Light Curves it indexes, and the size
cap on `tests/fixtures/` (spec §11, NEU-1710).

`scripts/build_fixtures.py` writes the Light Curves from the archives; these tests check what
it left behind, offline, and test its trimming on constructed data.
"""

import asyncio
import copy
import dataclasses
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest

from argelander.lightcurve import LightCurve

FIXTURES = Path(__file__).parent / "fixtures"
SCRIPT = Path(__file__).parents[1] / "scripts" / "build_fixtures.py"
MAX_FIXTURE_BYTES = 5 * 1024 * 1024

CONTRACT_FIELDS = (
    "star",
    "gaia_source_id",
    "published_period",
    "published_type",
    "source",
    "role",
)
# The types the first classifier rules cover (spec §7); HADS is a DSCT subtype in VSX.
RULE_TYPES = {"EW", "EA", "RRAB", "RRC", "DSCT", "DCEP", "SR", "ROT"}
NEGATIVE_KINDS = {"blend": 2, "alias_trap": 2, "constant": 3}


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_fixtures", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build_fixtures = load_script()
HEADER, STARS = build_fixtures.read_manifest(FIXTURES / "manifest.yaml")


def make_lc(n: int, **meta: object) -> LightCurve:
    return LightCurve(
        survey="tess",
        source_key="TIC 1",
        band="T",
        time=np.arange(n, dtype=np.float64),
        value=np.linspace(0.9, 1.1, n),
        error=np.full(n, 0.01),
        quality=np.zeros(n, dtype=np.int32),
        value_kind="flux",
        meta=dict(meta),
    )


def test_fixture_directory_is_within_the_size_cap() -> None:
    size = sum(path.stat().st_size for path in FIXTURES.rglob("*") if path.is_file())
    assert size <= MAX_FIXTURE_BYTES, f"tests/fixtures/ holds {size / 1e6:.2f} MB"


@pytest.mark.parametrize("star", STARS, ids=lambda s: s["star"])
def test_manifest_entry_has_the_contract_fields(star: dict) -> None:
    assert all(field in star for field in CONTRACT_FIELDS)
    assert star["source"]
    assert set(star["coverage"]) == set(build_fixtures.SURVEYS)
    if star["role"] == "positive":
        assert star["published_period"] > 0
        assert star["published_type"]
        assert "negative_kind" not in star
    else:
        assert star["role"] == "negative"
        assert star["negative_kind"] in NEGATIVE_KINDS
    if star.get("negative_kind") == "alias_trap":
        # The two periods are a 1 cycle/day alias pair (solar or sidereal day).
        beat = abs(1 / star["published_period"] - 1 / star["true_period"])
        assert beat == pytest.approx(1.0, abs=0.005)
    if star.get("negative_kind") == "blend":
        assert star["blend_host"]["gaia_source_id"] != star["gaia_source_id"]


def test_manifest_spans_the_rule_types_and_every_kind_of_negative() -> None:
    positives = [s for s in STARS if s["role"] == "positive"]
    types = {"DSCT" if s["published_type"] == "HADS" else s["published_type"] for s in positives}
    assert RULE_TYPES <= types
    assert len(positives) >= 18
    kinds = [s["negative_kind"] for s in STARS if s["role"] == "negative"]
    assert {kind: kinds.count(kind) for kind in NEGATIVE_KINDS} == NEGATIVE_KINDS
    assert len({s["gaia_source_id"] for s in STARS}) == len(STARS)
    assert len({s["star"] for s in STARS}) == len(STARS)


@pytest.mark.parametrize("survey", ["ztf", "tess", "gaia"])
def test_every_star_has_been_fetched_from(survey: str) -> None:
    """Only ASAS-SN may still be pending (NEU-1796); the other three were fetched."""
    assert [s["star"] for s in STARS if s["coverage"][survey] == build_fixtures.PENDING] == []


@pytest.mark.parametrize("star", STARS, ids=lambda s: s["star"])
def test_coverage_names_exactly_the_fixture_files(star: dict) -> None:
    for survey, coverage in star["coverage"].items():
        directory = build_fixtures.LIGHT_CURVES / survey / str(star["gaia_source_id"])
        bands = sorted(path.stem for path in directory.glob("*.parquet"))
        assert bands == ([] if coverage == build_fixtures.PENDING else coverage), survey


def test_no_fixture_belongs_to_a_star_outside_the_manifest() -> None:
    ids = {str(s["gaia_source_id"]) for s in STARS}
    directories = {p.name for p in build_fixtures.LIGHT_CURVES.glob("*/*") if p.is_dir()}
    assert directories <= ids


@pytest.mark.parametrize(
    "path",
    sorted(build_fixtures.LIGHT_CURVES.glob("*/*/*.parquet")),
    ids=lambda p: "/".join(p.parts[-3:]),
)
def test_fixture_is_a_trimmed_light_curve_of_its_path(path: Path) -> None:
    lc = LightCurve.from_parquet(path)
    assert (lc.survey, lc.band) == (path.parts[-3], path.stem)
    assert 0 < len(lc.time) <= build_fixtures.MAX_POINTS
    if "sap" in lc.meta:
        assert len(lc.meta["sap"]) == len(lc.time)


def test_trim_keeps_a_seeded_random_subset_in_time_order() -> None:
    lc = make_lc(2000)
    trimmed = build_fixtures.trim(lc, 500, seed=7)
    assert len(trimmed.time) == 500
    assert np.all(np.diff(trimmed.time) > 0)
    assert np.isin(trimmed.time, lc.time).all()
    # Not a regular stride, which would impose a Nyquist limit the real data lacks.
    assert len(np.unique(np.diff(trimmed.time))) > 1
    assert build_fixtures.trim(lc, 500, seed=7) == trimmed
    assert build_fixtures.trim(lc, 500, seed=8) != trimmed
    assert trimmed.meta["fixture_trim"] == {"points": 2000, "kept": 500, "seed": 7}


def test_trim_keeps_value_error_and_sap_aligned_with_time() -> None:
    lc = make_lc(1000, sap=[float(i) for i in range(1000)])
    lc = dataclasses.replace(lc, value=lc.time * 2.0, error=lc.time * 3.0)
    trimmed = build_fixtures.trim(lc, 100, seed=1)
    np.testing.assert_array_equal(trimmed.value, trimmed.time * 2.0)
    np.testing.assert_array_equal(trimmed.error, trimmed.time * 3.0)
    np.testing.assert_array_equal(trimmed.meta["sap"], trimmed.time)


def test_trim_leaves_a_short_light_curve_alone() -> None:
    lc = make_lc(300)
    assert build_fixtures.trim(lc, 500, seed=1) is lc


def test_manifest_rewrite_keeps_the_header(tmp_path: Path) -> None:
    path = tmp_path / "manifest.yaml"
    build_fixtures.write_manifest(HEADER, STARS, path)
    header, stars = build_fixtures.read_manifest(path)
    assert header == HEADER
    assert stars == STARS
    assert path.read_text() == (FIXTURES / "manifest.yaml").read_text()


@pytest.mark.live
def test_live_build_writes_a_stars_ztf_fixtures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build_fixtures, "LIGHT_CURVES", tmp_path)
    star = copy.deepcopy(next(s for s in STARS if s["star"] == "V0364 Dra"))

    failures = asyncio.run(build_fixtures.build([star], ["ztf"]))

    assert failures == []
    assert star["coverage"]["ztf"] == ["g", "r"]
    for band in ("g", "r"):
        lc = LightCurve.from_parquet(
            tmp_path / "ztf" / str(star["gaia_source_id"]) / f"{band}.parquet"
        )
        assert len(lc.time) == build_fixtures.MAX_POINTS
