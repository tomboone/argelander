"""The ZTF fetcher: per-filter oid merge, zero-point offsets and the blend flag (spec §5.1).

The offline tests replay a recorded IRSA response for CSS_J173907.6+550053, an RRAB in Draco
at G 14.7 that ZTF saw in fields 797, 1830 and 1831, so each band arrives as several oids.
The response was fetched on 2026-10-19 with the fetcher's own 1.5″ query and trimmed to
stay small: every 11th row of the two field-797 oids is kept, and the field-1830 r oid is
dropped so that one band's offsets stay under 0.05 mag. The flagged rows are original.

Expected values come from the CSV with the standard library, not from the code under test.
"""

import asyncio
import csv
import statistics
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from argelander.analysis.time import ztf_mjd_to_bjd_tdb
from argelander.fetch import ztf
from argelander.fetch.base import ArchiveLimits, Fetcher, TargetRef
from argelander.fetch.ztf import (
    BLEND_OFFSET_MAG,
    ZtfFetcher,
    light_curves_from_irsa_csv,
    query_irsa,
)
from argelander.lightcurve import LightCurve

RECORDED = Path(__file__).parent / "fixtures" / "ztf" / "css_j173907_irsa.csv"
TARGET = TargetRef(
    gaia_source_id=1418010841782026880,
    ra=264.78197,
    dec=55.01459,
    vsx_name="CSS_J173907.6+550053",
)
G_OIDS = ["1830101400004824", "1831104300004145", "797112400008865"]
R_OIDS = ["1831204300013878", "797212400007704"]


def recorded_rows() -> list[dict[str, str]]:
    with RECORDED.open(newline="") as f:
        return list(csv.DictReader(f))


def good_mags(oid: str) -> list[float]:
    return [float(r["mag"]) for r in recorded_rows() if r["oid"] == oid and r["catflags"] == "0"]


def good_mjds(oid: str) -> list[float]:
    return [float(r["mjd"]) for r in recorded_rows() if r["oid"] == oid and r["catflags"] == "0"]


def fetch_recorded(text: str | None = None) -> dict[str, LightCurve]:
    body = RECORDED.read_text() if text is None else text
    fetcher = ZtfFetcher(ArchiveLimits({"irsa": 1}), max_attempts=1, query=lambda ra, dec: body)
    return {lc.band: lc for lc in asyncio.run(fetcher.fetch(TARGET))}


def test_ztf_fetcher_satisfies_the_fetcher_protocol() -> None:
    fetcher: Fetcher = ZtfFetcher(ArchiveLimits({"irsa": 1}), max_attempts=1)

    assert fetcher.survey == "ztf"


def test_oids_in_the_same_filter_merge_into_one_light_curve_per_band() -> None:
    curves = fetch_recorded()

    assert sorted(curves) == ["g", "r"]
    g = curves["g"]
    assert g.survey == "ztf"
    assert g.value_kind == "mag"
    assert g.source_key == "+".join(G_OIDS)
    assert g.meta["oids"] == G_OIDS
    assert len(g.time) == sum(len(good_mags(oid)) for oid in G_OIDS)
    assert np.all(g.quality == 0)


def test_the_oid_with_most_points_is_the_reference_and_the_others_are_shifted_to_it() -> None:
    g = fetch_recorded()["g"]
    reference = "797112400008865"
    reference_median = statistics.median(good_mags(reference))

    assert g.meta["reference_oid"] == reference
    expected_offsets = {
        oid: reference_median - statistics.median(good_mags(oid))
        for oid in G_OIDS
        if oid != reference
    }
    assert g.meta["zero_point_offsets"] == pytest.approx(expected_offsets, abs=1e-9)

    # Every point, matched back to its oid through its time, carries its oid's offset.
    for oid in G_OIDS:
        times = ztf_mjd_to_bjd_tdb(good_mjds(oid), TARGET.ra, TARGET.dec)
        index = np.searchsorted(g.time, times)
        np.testing.assert_allclose(g.time[index], times, rtol=0, atol=1e-9)
        shift = expected_offsets.get(oid, 0.0)
        np.testing.assert_allclose(g.value[index], np.add(good_mags(oid), shift), atol=1e-9)


def test_flagged_rows_are_dropped() -> None:
    flagged = [r for r in recorded_rows() if r["catflags"] != "0"]
    assert flagged, "the recording should hold rows with catflags set"

    curves = fetch_recorded()

    total = sum(len(lc.time) for lc in curves.values())
    assert total == len(recorded_rows()) - len(flagged)


def test_times_are_bjd_tdb_and_sorted() -> None:
    r = fetch_recorded()["r"]
    mjd = np.sort(np.concatenate([good_mjds(oid) for oid in R_OIDS]))

    assert np.all(np.diff(r.time) >= 0)
    # Barycentric correction from Palomar is at most ~8.3 light-minutes; UTC→TDB adds ~69 s.
    np.testing.assert_allclose(r.time, mjd + 2400000.5, rtol=0, atol=0.007)
    assert r.meta["time_system"] == "MJD UTC at Palomar"


def test_an_offset_above_the_threshold_sets_blend_suspect() -> None:
    curves = fetch_recorded()
    g_offsets = curves["g"].meta["zero_point_offsets"].values()
    r_offsets = curves["r"].meta["zero_point_offsets"].values()
    assert max(abs(o) for o in g_offsets) > BLEND_OFFSET_MAG
    assert max(abs(o) for o in r_offsets) <= BLEND_OFFSET_MAG

    assert curves["g"].meta["blend_suspect"] is True
    assert curves["r"].meta["blend_suspect"] is False


def test_a_single_oid_band_has_no_offsets() -> None:
    lines = RECORDED.read_text().splitlines(keepends=True)
    single = "".join([lines[0], *(line for line in lines[1:] if line.startswith(R_OIDS[1]))])

    curves = fetch_recorded(single)

    assert list(curves) == ["r"]
    assert curves["r"].source_key == R_OIDS[1]
    assert curves["r"].meta["zero_point_offsets"] == {}
    assert curves["r"].meta["blend_suspect"] is False


def test_a_header_only_response_means_no_ztf_data() -> None:
    header = RECORDED.read_text().splitlines(keepends=True)[0]

    assert fetch_recorded(header) == {}


@pytest.mark.parametrize(
    "body",
    [
        "",
        '<?xml version="1.0"?><VOTABLE><RESOURCE type="results">'
        '<INFO name="QUERY_STATUS" value="ERROR">UsageFault</INFO></RESOURCE></VOTABLE>',
    ],
    ids=["empty", "votable-error"],
)
def test_a_response_that_is_not_the_light_curve_csv_is_an_error(body: str) -> None:
    with pytest.raises(ValueError, match="not a ZTF light-curve CSV"):
        light_curves_from_irsa_csv(body, TARGET.ra, TARGET.dec, fetched_at="2026-10-19")


def test_query_irsa_asks_for_g_and_r_within_one_and_a_half_arcsec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_get(url: str, **kwargs: object) -> SimpleNamespace:
        calls.append({"url": url, **kwargs})
        return SimpleNamespace(text="csv", raise_for_status=lambda: None)

    monkeypatch.setattr(ztf.requests, "get", fake_get)

    assert query_irsa(264.78197, 55.01459) == "csv"
    (call,) = calls
    assert call["url"] == "https://irsa.ipac.caltech.edu/cgi-bin/ZTF/nph_light_curves"
    params = call["params"]
    assert isinstance(params, dict)
    shape, ra, dec, radius = params["POS"].split()
    assert (shape, float(ra), float(dec)) == ("CIRCLE", 264.78197, 55.01459)
    assert float(radius) * 3600.0 == pytest.approx(1.5)
    assert params["BANDNAME"] == "g,r"
    assert params["FORMAT"] == "csv"
    assert call["timeout"] is not None


@pytest.mark.live
def test_live_fetch_returns_g_and_r_in_bjd_tdb() -> None:
    fetcher = ZtfFetcher(ArchiveLimits({"irsa": 1}), max_attempts=3)

    curves = {lc.band: lc for lc in asyncio.run(fetcher.fetch(TARGET))}

    assert sorted(curves) == ["g", "r"]
    for lc in curves.values():
        assert len(lc.time) > 100
        # ZTF began in March 2018: JD 2458178.
        assert lc.time.min() > 2458178.0
        assert set(lc.meta["oids"]) == set(lc.source_key.split("+"))
        assert lc.meta["time_system"] == "MJD UTC at Palomar"
