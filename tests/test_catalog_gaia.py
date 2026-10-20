"""The Gaia catalog join: colour, parallax, vari_*, neighbours, TESS field, 2MASS (spec §5.1).

The offline tests replay the four query results recorded on 2026-10-20 with the module's own
ADQL for six Draco sources between G 13 and 15:

- 1418010841782026880, CSS_J173907.6+550053, an RRAB: Gaia class RR, an RR Lyrae period.
- 1440307936865442176, class ECL with a 23 d eclipsing-binary period; a G 16.2 neighbour at
  3.3″.
- 1440250422959420800, class ECL, in both the eclipsing-binary and short-timescale tables.
- 1435149135683337216, class ECL, no 2MASS match, and a G 14.9 neighbour at 1.8″.
- 1422733312942999424, not variable in Gaia; a G 10.7 source 62″ away, inside TESS's
  aperture but outside the 20″ neighbour radius.
- 1438034941388614528, class DSCT|GDOR|SXPHE, a short-timescale period only.

Expected values come from the votables read with astropy, not from the code under test.
"""

import asyncio
import math
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import requests
from astropy.table import Table, vstack

from argelander.catalog import gaia
from argelander.catalog.gaia import (
    CATALOG_QUERY,
    CHUNK_SIZE,
    CROSS_ID_QUERY,
    NEIGHBOUR_QUERY,
    ROW_CAP,
    TESS_FIELD_QUERY,
    GaiaQueryError,
    GaiaRecord,
    fetch_catalog,
    records_from_tables,
    run_upload_query,
)
from argelander.fetch import base
from argelander.fetch.base import FetchError, TessField
from argelander.fetch.gaia_epoch import gaia_http_errors

FIXTURES = Path(__file__).parent / "fixtures" / "gaia"
RECORDED = {
    CATALOG_QUERY: FIXTURES / "catalog.xml",
    CROSS_ID_QUERY: FIXTURES / "catalog_cross_ids.xml",
    NEIGHBOUR_QUERY: FIXTURES / "catalog_neighbours.xml",
    TESS_FIELD_QUERY: FIXTURES / "catalog_tess_field.xml",
}
RRAB = 1418010841782026880
ECL_23D = 1440307936865442176
ECL_AND_SHORT = 1440250422959420800
ECL_NO_2MASS = 1435149135683337216
CONSTANT_BRIGHT_FIELD = 1422733312942999424
DSCT = 1438034941388614528
SOURCES = [RRAB, ECL_23D, ECL_AND_SHORT, ECL_NO_2MASS, CONSTANT_BRIGHT_FIELD, DSCT]


def recorded(adql: str) -> Table:
    return Table.read(RECORDED[adql], format="votable")


def recorded_query(adql: str, ids: list[int]) -> Table:
    """The archive's answer from the recording: the rows for the uploaded sources."""
    table = recorded(adql)
    return table[np.isin(table["source_id"], ids)]


def fetch_recorded(ids: list[int], query: Any = recorded_query, **kwargs: Any) -> dict:
    return asyncio.run(fetch_catalog(ids, max_attempts=1, query=query, **kwargs))


def catalog_row(source_id: int) -> Any:
    table = recorded(CATALOG_QUERY)
    (row,) = table[table["source_id"] == source_id]
    return row


def test_every_recorded_source_becomes_a_gaia_record() -> None:
    records = fetch_recorded(SOURCES)

    assert sorted(records) == sorted(SOURCES)
    assert all(isinstance(record, GaiaRecord) for record in records.values())


def test_astrometry_and_photometry_come_from_gaia_source() -> None:
    row = catalog_row(RRAB)

    record = fetch_recorded([RRAB])[RRAB]

    assert record.ra == pytest.approx(float(row["ra"]))
    assert record.dec == pytest.approx(float(row["dec"]))
    assert record.g == pytest.approx(float(row["phot_g_mean_mag"]))
    assert record.bp_rp == pytest.approx(float(row["bp_rp"]))
    assert record.parallax == pytest.approx(float(row["parallax"]))
    assert record.parallax_error == pytest.approx(float(row["parallax_error"]))
    assert record.ruwe == pytest.approx(float(row["ruwe"]))


def test_an_rr_lyrae_carries_its_class_subclass_and_fundamental_period() -> None:
    row = catalog_row(RRAB)

    record = fetch_recorded([RRAB])[RRAB]

    assert record.in_vari_summary
    assert record.vari_g_trimmed_range == pytest.approx(float(row["trimmed_range_mag_g_fov"]))
    assert record.vari_class == "RR"
    assert record.vari_class_score == pytest.approx(float(row["best_class_score"]))
    assert record.vari_subclass == "RRab"
    assert record.vari_periods == {"rrlyrae": pytest.approx(float(row["rr_pf"]))}
    assert record.vari_period == pytest.approx(0.48586, abs=1e-5)


def test_an_eclipsing_binary_period_is_one_over_its_frequency() -> None:
    row = catalog_row(ECL_23D)

    record = fetch_recorded([ECL_23D])[ECL_23D]

    assert record.vari_class == "ECL"
    assert record.vari_subclass is None
    assert record.vari_period == pytest.approx(1.0 / float(row["eb_frequency"]))
    assert 23.0 < record.vari_period < 23.1


def test_the_most_trusted_table_supplies_the_period() -> None:
    row = catalog_row(ECL_AND_SHORT)

    record = fetch_recorded([ECL_AND_SHORT])[ECL_AND_SHORT]

    assert record.vari_periods == {
        "eclipsing_binary": pytest.approx(1.0 / float(row["eb_frequency"])),
        "short_timescale": pytest.approx(1.0 / float(row["st_frequency"])),
    }
    assert record.vari_period == record.vari_periods["eclipsing_binary"]


def test_a_short_timescale_period_is_used_when_it_is_the_only_one() -> None:
    record = fetch_recorded([DSCT])[DSCT]

    assert record.vari_class == "DSCT|GDOR|SXPHE"
    assert list(record.vari_periods) == ["short_timescale"]
    assert record.vari_period == record.vari_periods["short_timescale"]


def test_a_source_gaia_does_not_call_variable_has_no_vari_fields() -> None:
    record = fetch_recorded([CONSTANT_BRIGHT_FIELD])[CONSTANT_BRIGHT_FIELD]

    assert not record.in_vari_summary
    assert record.vari_g_trimmed_range is None
    assert record.vari_class is None
    assert record.vari_class_score is None
    assert record.vari_subclass is None
    assert record.vari_periods == {}
    assert record.vari_period is None


def test_neighbours_within_20_arcsec_are_listed_nearest_first() -> None:
    table = recorded(NEIGHBOUR_QUERY)
    expected = table[table["source_id"] == ECL_NO_2MASS]
    expected.sort("separation")

    record = fetch_recorded([ECL_NO_2MASS])[ECL_NO_2MASS]

    assert [n.source_id for n in record.neighbours] == list(expected["neighbour_id"])
    np.testing.assert_allclose([n.separation for n in record.neighbours], expected["separation"])
    np.testing.assert_allclose([n.g for n in record.neighbours], expected["phot_g_mean_mag"])
    # The blend case: a source two magnitudes from the Target's G at 1.8″.
    closest = record.neighbours[0]
    assert closest.separation == pytest.approx(1.778, abs=1e-3)
    assert closest.g == pytest.approx(14.904, abs=1e-3)
    assert all(n.separation <= 20.0 for n in record.neighbours)


def test_a_bright_source_at_60_arcsec_is_in_the_tess_field_but_not_a_neighbour() -> None:
    table = recorded(TESS_FIELD_QUERY)
    (row,) = table[table["source_id"] == CONSTANT_BRIGHT_FIELD]

    record = fetch_recorded([CONSTANT_BRIGHT_FIELD])[CONSTANT_BRIGHT_FIELD]

    assert record.tess_field.n_sources == int(row["n_sources"])
    assert record.tess_field.brightest_g == pytest.approx(10.683, abs=1e-3)
    assert record.tess_field.combined_g == pytest.approx(float(row["combined_g"]))
    assert record.g is not None and record.tess_field.brightest_g is not None
    assert record.tess_field.brightest_g < record.g - 2.0
    assert [n.g for n in record.neighbours] == [pytest.approx(20.839, abs=1e-3)]


def test_combined_g_sums_the_flux_of_the_whole_tess_field() -> None:
    # The field's combined magnitude is brighter than its brightest source, never fainter.
    for record in fetch_recorded(SOURCES).values():
        field = record.tess_field
        assert field.n_sources > 1
        assert field.brightest_g is not None and field.combined_g is not None
        assert field.combined_g <= field.brightest_g
        assert field.combined_g > field.brightest_g - 2.5 * math.log10(field.n_sources) - 1e-9


def test_a_target_with_no_source_within_120_arcsec_gets_an_empty_tess_field() -> None:
    def query(adql: str, ids: list[int]) -> Table:
        table = recorded_query(adql, ids)
        return table[:0] if adql == TESS_FIELD_QUERY else table

    record = fetch_recorded([RRAB], query=query)[RRAB]

    assert record.tess_field == TessField(n_sources=0, brightest_g=None, combined_g=None)


def test_the_2mass_designation_is_a_cross_id() -> None:
    records = fetch_recorded(SOURCES)

    assert records[RRAB].cross_ids == {"2MASS": "2MASS J17390768+5500525"}
    assert records[ECL_NO_2MASS].cross_ids == {}
    with_2mass = [s for s in SOURCES if records[s].cross_ids]
    assert sorted(with_2mass) == sorted(set(SOURCES) - {ECL_NO_2MASS})


def test_2000_ids_are_uploaded_in_chunks_under_the_limit() -> None:
    calls: dict[str, list[list[int]]] = {}

    def query(adql: str, ids: list[int]) -> Table:
        calls.setdefault(adql, []).append(ids)
        return recorded_query(adql, ids)

    ids = list(range(1, 2001))

    records = fetch_recorded(ids, query=query)

    # The archive accepted 50,000 uploaded IDs; what binds is the 60 s job limit and the
    # 50,000-row result cap, which 1,000 IDs per upload stays well inside (module docstring).
    assert CHUNK_SIZE == 1_000
    assert set(calls) == set(RECORDED)
    for chunks in calls.values():
        assert [len(chunk) for chunk in chunks] == [1_000, 1_000]
        assert [i for chunk in chunks for i in chunk] == ids
    assert records == {}


def test_duplicate_ids_are_uploaded_once() -> None:
    calls: list[list[int]] = []

    def query(adql: str, ids: list[int]) -> Table:
        calls.append(ids)
        return recorded_query(adql, ids)

    records = fetch_recorded([RRAB, RRAB, DSCT], query=query)

    assert all(ids == [RRAB, DSCT] for ids in calls)
    assert sorted(records) == sorted([RRAB, DSCT])


def test_a_result_at_the_archive_row_cap_is_an_error() -> None:
    def query(adql: str, ids: list[int]) -> Table:
        if adql == NEIGHBOUR_QUERY:
            return Table({"source_id": np.full(ROW_CAP, RRAB)})
        return recorded_query(adql, ids)

    with pytest.raises(ValueError, match="cap"):
        fetch_recorded([RRAB], query=query)


def test_a_row_for_a_source_that_was_not_requested_is_an_error() -> None:
    with pytest.raises(ValueError, match=str(DSCT)):
        fetch_recorded([RRAB], query=lambda adql, ids: recorded(adql))


def test_a_source_returned_twice_is_an_error() -> None:
    catalog = recorded(CATALOG_QUERY)
    rrab = catalog[catalog["source_id"] == RRAB]
    empty = [recorded(q)[:0] for q in (CROSS_ID_QUERY, NEIGHBOUR_QUERY, TESS_FIELD_QUERY)]

    with pytest.raises(ValueError, match="more than once"):
        records_from_tables(vstack([rrab, rrab]), *empty)


def test_an_archive_5xx_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base.random, "uniform", lambda a, b: 0.0)
    calls: list[str] = []

    def query(adql: str, ids: list[int]) -> Table:
        calls.append(adql)
        with gaia_http_errors():
            raise requests.exceptions.HTTPError("Error 503:\nService Unavailable")

    with pytest.raises(FetchError) as excinfo:
        asyncio.run(fetch_catalog([RRAB], max_attempts=2, query=query))

    assert calls == [CATALOG_QUERY, CATALOG_QUERY]
    assert excinfo.value.status == 503


class FakeJob:
    def __init__(self, phase: str) -> None:
        self.jobid = "1700000000000O"
        self.phase = phase

    def wait_for_job_end(self) -> tuple[None, str]:
        return None, self.phase

    def get_error(self) -> str:
        return "Maximum execution time (60 s) reached. Job aborted."

    def get_results(self) -> Table:
        return Table({"source_id": [RRAB]})


def fake_astroquery_gaia(monkeypatch: pytest.MonkeyPatch, phase: str) -> list[dict[str, Any]]:
    """Stand in for `astroquery.gaia`, whose import would reach the archive."""
    launches: list[dict[str, Any]] = []

    class FakeGaiaClass:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def launch_job_async(self, query: str, **kwargs: Any) -> FakeJob:
            launches.append({"query": query, **kwargs})
            return FakeJob(phase)

    monkeypatch.setitem(sys.modules, "astroquery.gaia", SimpleNamespace(GaiaClass=FakeGaiaClass))
    return launches


def test_run_upload_query_uploads_the_ids_as_the_targets_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launches = fake_astroquery_gaia(monkeypatch, "COMPLETED")

    result = run_upload_query(CATALOG_QUERY, [RRAB, DSCT])

    (launch,) = launches
    assert launch["query"] == CATALOG_QUERY
    assert launch["upload_table_name"] == "targets"
    assert list(launch["upload_resource"]["source_id"]) == [RRAB, DSCT]
    assert "tap_upload.targets" in CATALOG_QUERY
    assert list(result["source_id"]) == [RRAB]


def test_an_aborted_job_is_an_error_with_the_archive_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_astroquery_gaia(monkeypatch, "ABORTED")

    with pytest.raises(GaiaQueryError, match=r"ABORTED: Maximum execution time \(60 s\)"):
        run_upload_query(NEIGHBOUR_QUERY, [RRAB])


def test_cone_joins_put_the_circle_on_the_archive_side() -> None:
    # The other way round, the archive scans gaia_source and aborts at 60 s.
    for adql in (NEIGHBOUR_QUERY, TESS_FIELD_QUERY):
        assert "POINT('ICRS', s.ra, s.dec), CIRCLE('ICRS', n.ra, n.dec" in adql
    assert f"{gaia.NEIGHBOUR_RADIUS_ARCSEC} / 3600.0" in NEIGHBOUR_QUERY
    assert f"{gaia.TESS_FIELD_RADIUS_ARCSEC} / 3600.0" in TESS_FIELD_QUERY


@pytest.mark.live
def test_live_join_returns_the_expected_gaia_classes() -> None:
    records = asyncio.run(fetch_catalog([RRAB, ECL_23D, DSCT], max_attempts=3))

    assert sorted(records) == sorted([RRAB, ECL_23D, DSCT])
    assert records[RRAB].vari_class == "RR"
    assert records[RRAB].vari_subclass == "RRab"
    assert records[ECL_23D].vari_class == "ECL"
    assert records[DSCT].vari_class == "DSCT|GDOR|SXPHE"
    assert records[RRAB].vari_period == pytest.approx(0.48586, abs=1e-5)
    assert records[RRAB].cross_ids == {"2MASS": "2MASS J17390768+5500525"}
    assert records[ECL_23D].neighbours[0].separation == pytest.approx(3.33, abs=0.01)
    assert all(record.tess_field.n_sources > 0 for record in records.values())
