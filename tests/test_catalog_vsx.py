"""The VizieR VSX slice query: VAR entries with Gaia DR3 IDs in one constellation (spec §3.1).

The offline tests replay nine rows of the Draco bounding-box query recorded on 2026-10-21
(`B/vsx/vsx` version 2026-10-18; Type `VAR*`, max 13..16):

- four `Gaia DR3 <id>` VAR entries inside Draco, which are the Slice;
- ZTF J111001.01+785023.3, ASASSN-V J110908.83+742826.9 and NSV 20187 (VAR:), inside Draco
  but named for no Gaia source;
- NSV 18294 (VAR:, an amplitude for its minimum), outside Draco;
- Gaia DR3 1666776962473690112, in Ursa Major 0.15° south of Draco's boundary.

Expected values come from the votable read with astropy, not from the code under test.
"""

import asyncio
from pathlib import Path
from typing import Any

import pytest
import requests
from astropy.table import Table

from argelander.catalog import vsx
from argelander.catalog.constellation import bounding_box
from argelander.catalog.vsx import COLUMNS, VsxEntry, entries_from_table, fetch_vsx_slice
from argelander.fetch import base

RECORDED = Path(__file__).parent / "fixtures" / "vsx" / "draco.xml"
DRACO_GAIA_OIDS = [4060983, 5667316, 7146137, 7832051]
JUST_OUTSIDE_OID = 5312652
NSV_20187 = 58622  # VAR:, inside Draco


def recorded() -> Table:
    return Table.read(RECORDED, format="votable")


def row(table: Table, oid: int) -> Any:
    (match,) = table[table["OID"] == oid]
    return match


def test_only_gaia_named_entries_inside_the_constellation_are_kept() -> None:
    entries = entries_from_table(recorded(), "Dra")

    assert sorted(entry.oid for entry in entries) == DRACO_GAIA_OIDS
    assert all(isinstance(entry, VsxEntry) for entry in entries)
    assert JUST_OUTSIDE_OID not in {entry.oid for entry in entries}


def test_an_entry_carries_vsxs_fields_and_the_gaia_id_from_its_name() -> None:
    expected = row(recorded(), 4060983)

    (entry,) = [e for e in entries_from_table(recorded(), "Dra") if e.oid == 4060983]

    assert entry.name == "Gaia DR3 1144599167953902464"
    assert entry.gaia_source_id == 1144599167953902464
    assert entry.ra == pytest.approx(float(expected["RAJ2000"]))
    assert entry.dec == pytest.approx(float(expected["DEJ2000"]))
    assert entry.type == "VAR"
    assert entry.period == pytest.approx(float(expected["Period"]))
    assert entry.mag_max == pytest.approx(15.86)
    assert entry.mag_min == pytest.approx(16.52)
    assert not entry.mag_min_is_amplitude
    assert entry.band == "G"


def test_a_var_colon_entry_without_a_period_is_kept_when_it_has_a_gaia_id() -> None:
    table = recorded()
    table["Name"][table["OID"] == NSV_20187] = "Gaia DR3 123"

    (entry,) = [e for e in entries_from_table(table, "Dra") if e.oid == NSV_20187]

    assert entry.type == "VAR:"
    assert entry.gaia_source_id == 123
    assert entry.period is None
    assert entry.band == "P"


def test_an_amplitude_flag_marks_mag_min_as_an_amplitude() -> None:
    table = recorded()
    table["f_min"][table["OID"] == 4060983] = "Y"

    (entry,) = [e for e in entries_from_table(table, "Dra") if e.oid == 4060983]

    assert entry.mag_min_is_amplitude


def test_types_other_than_var_are_dropped() -> None:
    # `VAR*` would also match a type such as "VAR+E", should VSX ever use one.
    table = recorded()
    table["Type"][table["OID"] == 4060983] = "VAR+E"

    oids = {entry.oid for entry in entries_from_table(table, "Dra")}

    assert 4060983 not in oids
    assert len(oids) == len(DRACO_GAIA_OIDS) - 1


def test_a_name_that_only_starts_with_a_gaia_id_is_not_one() -> None:
    table = recorded()
    table["Name"][table["OID"] == 4060983] = "Gaia DR3 1144599167953902464 B"

    assert 4060983 not in {entry.oid for entry in entries_from_table(table, "Dra")}


def test_the_query_asks_for_var_types_in_the_magnitude_range_and_the_box() -> None:
    sent: list[dict[str, str]] = []

    def query(filters: dict[str, str]) -> Table:
        sent.append(filters)
        return recorded()

    entries = asyncio.run(fetch_vsx_slice("Dra", 13, 16, max_attempts=1, query=query))

    box = bounding_box("Dra")
    assert sent == [
        {
            "Type": "VAR*",
            "max": "13..16",
            "RAJ2000": f"{box.ra_min}..{box.ra_max}",
            "DEJ2000": f"{box.dec_min}..{box.dec_max}",
        }
    ]
    assert [entry.oid for entry in entries] == DRACO_GAIA_OIDS


def test_a_box_across_ra_zero_is_queried_in_two_parts() -> None:
    sent: list[dict[str, str]] = []

    def query(filters: dict[str, str]) -> Table:
        sent.append(filters)
        return Table(names=COLUMNS)

    entries = asyncio.run(fetch_vsx_slice("Peg", 13, 16, max_attempts=1, query=query))

    box = bounding_box("Peg")
    assert [f["RAJ2000"] for f in sent] == [f"{box.ra_min}..360.0", f"0.0..{box.ra_max}"]
    assert entries == []


def test_a_transient_failure_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base.random, "uniform", lambda a, b: 0.0)
    calls = 0

    def query(filters: dict[str, str]) -> Table:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise requests.exceptions.ConnectionError("reset")
        return recorded()

    entries = asyncio.run(fetch_vsx_slice("Dra", 13, 16, max_attempts=2, query=query))

    assert calls == 2
    assert len(entries) == len(DRACO_GAIA_OIDS)


def fake_vizier(monkeypatch: pytest.MonkeyPatch, status: int, content: bytes) -> list[dict]:
    """Replace astroquery's Vizier with one answering `content`; returns its constructor args."""
    created: list[dict] = []

    class FakeVizier:
        def __init__(self, **kwargs: Any) -> None:
            created.append(kwargs)

        def query_constraints_async(self, *, catalog: str) -> requests.Response:
            assert catalog == "B/vsx/vsx"
            response = requests.Response()
            response.status_code = status
            response._content = content
            return response

    monkeypatch.setattr("astroquery.vizier.Vizier", FakeVizier)
    return created


def test_run_vizier_query_reads_the_votable(monkeypatch: pytest.MonkeyPatch) -> None:
    created = fake_vizier(monkeypatch, 200, RECORDED.read_bytes())
    filters = {"Type": "VAR*"}

    table = vsx.run_vizier_query(filters)

    assert sorted(table["OID"]) == sorted(recorded()["OID"])
    assert created == [{"columns": list(COLUMNS), "column_filters": filters, "row_limit": -1}]


def test_run_vizier_query_returns_no_rows_for_an_empty_votable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<VOTABLE version="1.4" xmlns="http://www.ivoa.net/xml/VOTable/v1.3"><RESOURCE/>'
        b"</VOTABLE>"
    )
    fake_vizier(monkeypatch, 200, empty)

    table = vsx.run_vizier_query({})

    assert len(table) == 0
    assert entries_from_table(table, "Dra") == []


def test_run_vizier_query_raises_an_http_error_so_it_can_be_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_vizier(monkeypatch, 503, b"<html>Service Unavailable</html>")

    with pytest.raises(requests.HTTPError) as caught:
        vsx.run_vizier_query({})

    assert base.is_transient(caught.value)


@pytest.mark.live
def test_live_draco_13_16_slice() -> None:
    entries = asyncio.run(fetch_vsx_slice("Dra", 13, 16, max_attempts=3))

    # Recorded in spec §14: 430 on 2026-10-21 (table version 2026-10-18).
    print(f"Draco 13–16 mag: {len(entries)} VAR entries with Gaia DR3 IDs")
    assert len(entries) > 0
    assert all(entry.type in {"VAR", "VAR:"} for entry in entries)
    assert all(entry.mag_max is not None and 13 <= entry.mag_max <= 16 for entry in entries)
    assert len({entry.gaia_source_id for entry in entries}) == len(entries)
    assert not [e for e in entries if e.oid == JUST_OUTSIDE_OID]
