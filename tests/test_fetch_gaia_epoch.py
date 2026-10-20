"""The Gaia DR3 epoch-photometry fetcher: per-band rejection, BJD_TDB, batching (spec §5.1).

The offline tests replay a DataLink response recorded on 2026-10-19 with the fetcher's own
request (`DATAMODEL_GAIA`, votable) for two sources:

- 1418010841782026880, CSS_J173907.6+550053, an RRAB in Draco at G 14.7. 45 transits. G
  transit 31 was rejected by photometry (its flux is null) and G transits 12 and 25 by
  variability; BP transit 15 was rejected by photometry but still carries a magnitude; RP
  transit 25 was rejected by variability.
- 4111834567779557376, 51 transits, whose BP and RP transit 22 were rejected by both.

Expected magnitudes, errors and dropped transits come from the votable read with astropy,
not from the code under test. Expected times go through `analysis/time.py`, which has its own
reference tests; the TCB − TDB shift is also checked independently.
"""

import asyncio
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
import requests
from astropy.io.votable import parse_single_table
from astropy.table import Table

from argelander.analysis.time import GAIA_TIME_ZERO, gaia_tcb_to_bjd_tdb
from argelander.fetch import base
from argelander.fetch.base import ArchiveLimits, Fetcher, FetchError, TargetRef
from argelander.fetch.gaia_epoch import (
    BATCH_SIZE,
    GaiaArchiveError,
    GaiaEpochFetcher,
    gaia_http_errors,
    light_curves_from_epoch_tables,
    query_epoch_photometry,
)
from argelander.lightcurve import LightCurve

RECORDED = Path(__file__).parent / "fixtures" / "gaia" / "epoch_photometry_datamodel_gaia.xml"
RRAB = TargetRef(
    gaia_source_id=1418010841782026880,
    ra=264.78197,
    dec=55.01459,
    vsx_name="CSS_J173907.6+550053",
)
OTHER_ID = 4111834567779557376
# Transit indices each band drops, per source, read off the recorded flags.
DROPPED = {
    RRAB.gaia_source_id: {"G": {12, 25, 31}, "BP": {15}, "RP": {25}},
    OTHER_ID: {"G": set(), "BP": {22}, "RP": {22}},
}
TIME_COLUMNS = {"G": "g_transit_time", "BP": "bp_obs_time", "RP": "rp_obs_time"}
MAG_COLUMNS = {"G": "g_transit_mag", "BP": "bp_mag", "RP": "rp_mag"}


def recorded_table() -> Table:
    return parse_single_table(RECORDED).to_table()


def recorded_row(source_id: int) -> dict[str, np.ndarray]:
    table = recorded_table()
    (row,) = table[table["source_id"] == source_id]
    cells = {name: np.ma.asarray(row[name]) for name in table.colnames}
    return {
        name: np.ma.filled(cell, np.nan) if cell.dtype.kind == "f" else np.ma.getdata(cell)
        for name, cell in cells.items()
    }


def target(source_id: int) -> TargetRef:
    return TargetRef(gaia_source_id=source_id, ra=0.0, dec=0.0, vsx_name=str(source_id))


def recorded_query(ids: list[int]) -> list[Table]:
    """The archive's answer from the recording: the requested sources it has epochs for."""
    table = recorded_table()
    return [table[np.isin(table["source_id"], ids)]]


def fetch_recorded(
    targets: list[TargetRef], query: Callable[[list[int]], list[Table]] = recorded_query
) -> dict[int, list[LightCurve]]:
    fetcher = GaiaEpochFetcher(ArchiveLimits({"gaia": 1}), max_attempts=1, query=query)
    return asyncio.run(fetcher.fetch_many(targets))


def by_band(curves: list[LightCurve]) -> dict[str, LightCurve]:
    return {lc.band: lc for lc in curves}


def test_gaia_epoch_fetcher_satisfies_the_fetcher_protocol() -> None:
    fetcher: Fetcher = GaiaEpochFetcher(ArchiveLimits({"gaia": 1}), max_attempts=1)

    assert fetcher.survey == "gaia"


def test_each_source_gets_g_bp_and_rp_magnitudes() -> None:
    curves = fetch_recorded([RRAB, target(OTHER_ID)])

    assert sorted(curves) == sorted([RRAB.gaia_source_id, OTHER_ID])
    for source_id, lcs in curves.items():
        bands = by_band(lcs)
        assert sorted(bands) == ["BP", "G", "RP"]
        for lc in lcs:
            assert lc.survey == "gaia"
            assert lc.source_key == str(source_id)
            assert lc.value_kind == "mag"
            assert np.all(lc.quality == 0)


def test_rejected_transits_are_dropped_per_band() -> None:
    curves = fetch_recorded([RRAB, target(OTHER_ID)])

    for source_id, dropped in DROPPED.items():
        row = recorded_row(source_id)
        bands = by_band(curves[source_id])
        for band, indices in dropped.items():
            kept = np.array([i for i in range(len(row["transit_id"])) if i not in indices])
            np.testing.assert_array_equal(bands[band].value, row[MAG_COLUMNS[band]][kept])
            assert bands[band].meta["n_rejected"] == len(indices)


def test_a_photometry_rejected_bp_transit_is_dropped_even_though_it_has_a_magnitude() -> None:
    row = recorded_row(RRAB.gaia_source_id)
    assert row["photometry_flag_bp_reject"][15]
    assert np.isfinite(row["bp_mag"][15])

    bp = by_band(fetch_recorded([RRAB])[RRAB.gaia_source_id])["BP"]

    rejected_time = gaia_tcb_to_bjd_tdb(row["bp_obs_time"][15])
    assert not np.any(np.isclose(bp.time, rejected_time, rtol=0.0, atol=1e-9))


def test_times_are_converted_from_tcb_to_bjd_tdb() -> None:
    row = recorded_row(RRAB.gaia_source_id)
    bands = by_band(fetch_recorded([RRAB])[RRAB.gaia_source_id])

    for band, dropped in DROPPED[RRAB.gaia_source_id].items():
        kept = [i for i in range(len(row["transit_id"])) if i not in dropped]
        raw = row[TIME_COLUMNS[band]][kept]
        np.testing.assert_allclose(bands[band].time, gaia_tcb_to_bjd_tdb(raw), rtol=0, atol=1e-9)
        # BJD_TDB, not the raw offset JD in TCB: TDB runs about 20 s behind TCB by DR3's epochs.
        shift_s = (bands[band].time - (raw + GAIA_TIME_ZERO)) * 86400.0
        assert np.all((shift_s > -25.0) & (shift_s < -15.0))
        assert bands[band].meta["time_system"] == "BJD TCB - 2455197.5"


def test_magnitude_errors_come_from_the_flux_signal_to_noise() -> None:
    row = recorded_row(OTHER_ID)
    g = by_band(fetch_recorded([target(OTHER_ID)])[OTHER_ID])["G"]

    expected = 2.5 / np.log(10.0) / row["g_transit_flux_over_error"]
    np.testing.assert_allclose(g.error, expected, rtol=1e-6)


def test_a_source_without_epoch_photometry_gets_an_empty_list() -> None:
    curves = fetch_recorded([RRAB, target(42)])

    assert curves[42] == []
    assert len(curves[RRAB.gaia_source_id]) == 3


def test_a_row_for_a_source_that_was_not_requested_is_an_error() -> None:
    with pytest.raises(ValueError, match=str(OTHER_ID)):
        fetch_recorded([RRAB], query=lambda ids: [recorded_table()])


def test_fetch_is_a_batch_of_one() -> None:
    calls: list[list[int]] = []

    def query(ids: list[int]) -> list[Table]:
        calls.append(ids)
        return recorded_query(ids)

    fetcher = GaiaEpochFetcher(ArchiveLimits({"gaia": 1}), max_attempts=1, query=query)

    curves = asyncio.run(fetcher.fetch(RRAB))

    assert calls == [[RRAB.gaia_source_id]]
    assert sorted(lc.band for lc in curves) == ["BP", "G", "RP"]


def test_250_ids_are_requested_in_three_batches() -> None:
    calls: list[list[int]] = []

    def query(ids: list[int]) -> list[Table]:
        calls.append(ids)
        return []

    fetcher = GaiaEpochFetcher(ArchiveLimits({"gaia": 1}), max_attempts=1, query=query)
    targets = [target(source_id) for source_id in range(1, 251)]

    curves = asyncio.run(fetcher.fetch_many(targets))

    assert BATCH_SIZE == 100
    assert [len(batch) for batch in calls] == [100, 100, 50]
    assert [i for batch in calls for i in batch] == list(range(1, 251))
    assert curves == {source_id: [] for source_id in range(1, 251)}


def test_batches_run_under_the_gaia_semaphore() -> None:
    fetcher = GaiaEpochFetcher(ArchiveLimits({"irsa": 1}), max_attempts=1, query=lambda ids: [])

    with pytest.raises(ValueError, match="'gaia'"):
        asyncio.run(fetcher.fetch(RRAB))


def failing_query(message: str, calls: list[list[int]]) -> Callable[[list[int]], list[Table]]:
    """A query that fails the way astroquery does: a bare HTTPError, status in the message."""

    def query(ids: list[int]) -> list[Table]:
        calls.append(ids)
        with gaia_http_errors():
            raise requests.exceptions.HTTPError(message)

    return query


def test_an_archive_5xx_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base.random, "uniform", lambda a, b: 0.0)
    calls: list[list[int]] = []
    query = failing_query("Error 503:\nService Unavailable", calls)
    fetcher = GaiaEpochFetcher(ArchiveLimits({"gaia": 1}), max_attempts=2, query=query)

    with pytest.raises(FetchError) as excinfo:
        asyncio.run(fetcher.fetch(RRAB))

    assert len(calls) == 2
    assert excinfo.value.status == 503


def test_an_archive_400_is_not_retried() -> None:
    calls: list[list[int]] = []
    query = failing_query("Error 400:\nCannot parse query", calls)
    fetcher = GaiaEpochFetcher(ArchiveLimits({"gaia": 1}), max_attempts=3, query=query)

    with pytest.raises(GaiaArchiveError) as excinfo:
        asyncio.run(fetcher.fetch(RRAB))

    assert len(calls) == 1
    assert excinfo.value.code == 400


def test_an_http_error_without_a_status_in_its_message_is_left_alone() -> None:
    with pytest.raises(requests.exceptions.HTTPError) as excinfo:
        with gaia_http_errors():
            raise requests.exceptions.HTTPError("something else")

    assert not isinstance(excinfo.value, GaiaArchiveError)


def test_light_curves_from_epoch_tables_accepts_several_tables() -> None:
    table = recorded_table()
    first = table[table["source_id"] == RRAB.gaia_source_id]
    second = table[table["source_id"] == OTHER_ID]

    curves = light_curves_from_epoch_tables([first, second], fetched_at="2026-10-19T00:00:00+00:00")

    assert sorted(curves) == sorted([RRAB.gaia_source_id, OTHER_ID])


def test_a_source_returned_twice_is_an_error() -> None:
    table = recorded_table()
    rrab = table[table["source_id"] == RRAB.gaia_source_id]

    with pytest.raises(ValueError, match="more than once"):
        light_curves_from_epoch_tables([rrab, rrab], fetched_at="2026-10-19T00:00:00+00:00")


@pytest.mark.live
def test_live_fetch_returns_three_bands_in_bjd_tdb() -> None:
    tables: list[Table] = []

    def recording_query(ids: list[int]) -> list[Table]:
        tables.extend(query_epoch_photometry(ids))
        return tables

    fetcher = GaiaEpochFetcher(ArchiveLimits({"gaia": 1}), max_attempts=3, query=recording_query)

    curves = by_band(asyncio.run(fetcher.fetch(RRAB)))

    assert sorted(curves) == ["BP", "G", "RP"]
    (table,) = tables
    (row,) = table[table["source_id"] == RRAB.gaia_source_id]
    raw_g = np.ma.filled(np.ma.asarray(row["g_transit_time"]), np.nan)
    assert 30 < len(curves["G"].time) <= len(raw_g)
    # Barycentric JD around DR3's span (2014-07 to 2017-05), not the raw TCB offset.
    assert np.all((curves["G"].time > 2456850.0) & (curves["G"].time < 2457900.0))
    assert 14.0 < np.median(curves["G"].value) < 15.5
