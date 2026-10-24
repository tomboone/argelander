"""select-slice: VSX + Gaia into `slices`, `targets` and fetch Jobs (spec §3.1, §4).

The archive answers are the recordings the catalog tests replay: the Draco VSX rows of
`fixtures/vsx/draco.xml` (see test_catalog_vsx.py) and the six Draco sources of
`fixtures/gaia` (see test_catalog_gaia.py). The two were recorded for different stars, so
three of the four Gaia-named Draco entries are renamed for recorded Gaia sources:

- OID 4060983 → the RR Lyrae, with a Gaia class, period and 2MASS designation;
- OID 5667316 → the 23 d eclipsing binary, with a neighbour at 3.3″;
- OID 7146137 → the constant star, which has no Gaia class;
- OID 7832051 keeps its own name, a source the Gaia recording does not hold.

The `engine` fixture (conftest.py) migrates a throwaway database.
"""

import asyncio
import dataclasses
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from astropy.table import Table
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from argelander import selector
from argelander.catalog.gaia import (
    CATALOG_QUERY,
    CROSS_ID_QUERY,
    NEIGHBOUR_QUERY,
    TESS_FIELD_QUERY,
    GaiaRecord,
)
from argelander.catalog.vsx import entries_from_table
from argelander.cli import app
from argelander.db.models import Job, JobKind, JobStatus, Slice, Target, TargetStatus
from argelander.db.session import sync_session_factory
from argelander.selector import SliceSummary

FIXTURES = Path(__file__).parent / "fixtures"
GAIA_RECORDED = {
    CATALOG_QUERY: FIXTURES / "gaia" / "catalog.xml",
    CROSS_ID_QUERY: FIXTURES / "gaia" / "catalog_cross_ids.xml",
    NEIGHBOUR_QUERY: FIXTURES / "gaia" / "catalog_neighbours.xml",
    TESS_FIELD_QUERY: FIXTURES / "gaia" / "catalog_tess_field.xml",
}
RRAB = 1418010841782026880
ECL_23D = 1440307936865442176
CONSTANT = 1422733312942999424
NOT_IN_GAIA = 2298118050972873856
RENAMED = {4060983: RRAB, 5667316: ECL_23D, 7146137: CONSTANT}
DRACO_OIDS = [4060983, 5667316, 7146137, 7832051]
ASASSN_OID = 2266704  # inside Draco, named for no Gaia source

runner = CliRunner()


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    """Session factory on a database emptied of Jobs, Targets and Slices."""
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE jobs, targets, slices RESTART IDENTITY CASCADE"))
    return sync_session_factory(engine)


def recorded_vsx(renamed: dict[int, int] = RENAMED) -> Table:
    table = Table.read(FIXTURES / "vsx" / "draco.xml", format="votable")
    for oid, source_id in renamed.items():
        table["Name"][table["OID"] == oid] = f"Gaia DR3 {source_id}"
    return table


def gaia_answer(adql: str, ids: list[int]) -> Table:
    """The archive's answer from the recording: the rows for the uploaded sources."""
    table = Table.read(GAIA_RECORDED[adql], format="votable")
    return table[np.isin(table["source_id"], ids)]


def select_slice(sessions: sessionmaker[Session], vsx: Table | None = None) -> SliceSummary:
    """One select-slice run on Draco 13–16 against the recordings."""
    table = recorded_vsx() if vsx is None else vsx
    entries, records = asyncio.run(
        selector.gather(
            "Dra",
            13,
            16,
            max_attempts=1,
            vsx_query=lambda filters: table,
            gaia_query=gaia_answer,
        )
    )
    with sessions.begin() as session:
        return selector.store(
            session,
            name="Dra-13-16",
            constellation="Dra",
            mag_min=13,
            mag_max=16,
            entries=entries,
            records=records,
        )


def target(session: Session, oid: int) -> Target:
    return session.scalars(select(Target).where(Target.vsx_oid == oid)).one()


def count(session: Session, model: Any) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_a_slice_is_one_slice_row_its_targets_and_a_fetch_job_each(
    sessions: sessionmaker[Session],
) -> None:
    summary = select_slice(sessions)

    with sessions() as session:
        (slice_,) = session.scalars(select(Slice)).all()
        targets = session.scalars(select(Target).order_by(Target.vsx_oid)).all()
        jobs = session.scalars(select(Job)).all()

    assert (slice_.name, slice_.constellation, slice_.mag_min, slice_.mag_max) == (
        "Dra-13-16",
        "Dra",
        13,
        16,
    )
    assert slice_.n_targets == len(DRACO_OIDS)
    assert [t.vsx_oid for t in targets] == DRACO_OIDS
    assert {t.slice_id for t in targets} == {slice_.id}
    assert {t.status for t in targets} == {TargetStatus.SELECTED}
    assert sorted(job.target_id for job in jobs) == sorted(t.id for t in targets)
    assert {(job.kind, job.status) for job in jobs} == {(JobKind.FETCH, JobStatus.PENDING)}

    assert summary.slice_id == slice_.id
    assert summary.n_targets == summary.n_new == summary.n_enqueued == len(DRACO_OIDS)
    assert summary.n_with_gaia == 3
    assert summary.n_gaia_vari_class == 2  # the constant star has no Gaia class
    assert summary.n_gaia_period == 2
    assert sum(summary.mag_counts.values()) == len(DRACO_OIDS)
    assert set(summary.mag_counts) <= {13, 14, 15}


def test_a_target_carries_its_vsx_entry_and_gaia_record(
    sessions: sessionmaker[Session], recorded_gaia: dict[int, GaiaRecord]
) -> None:
    vsx_row = recorded_vsx()[recorded_vsx()["OID"] == 4060983][0]
    record = recorded_gaia[RRAB]

    select_slice(sessions)

    with sessions() as session:
        rrab = target(session, 4060983)
    assert rrab.vsx_name == f"Gaia DR3 {RRAB}"
    assert rrab.gaia_source_id == RRAB
    assert rrab.ra == pytest.approx(float(vsx_row["RAJ2000"]))
    assert rrab.vsx_type == "VAR"
    assert rrab.vsx_mag_max == pytest.approx(float(vsx_row["max"]))
    assert rrab.vsx_band == "G"
    assert not rrab.vsx_mag_min_is_amplitude
    assert rrab.gaia_g == pytest.approx(record.g)
    assert rrab.gaia_bp_rp == pytest.approx(record.bp_rp)
    assert rrab.parallax == pytest.approx(record.parallax)
    assert rrab.gaia_vari_class == "RR"
    assert rrab.gaia_vari_period == pytest.approx(record.vari_period)
    assert rrab.gaia_vari_detail is not None
    assert rrab.gaia_vari_detail["vari_subclass"] == "RRab"
    assert rrab.gaia_vari_detail["ruwe"] == pytest.approx(record.ruwe)
    assert rrab.gaia_vari_detail["cross_ids"] == record.cross_ids
    assert rrab.tess_field == {
        "n_sources": record.tess_field.n_sources,
        "brightest_g": pytest.approx(record.tess_field.brightest_g),
        "combined_g": pytest.approx(record.tess_field.combined_g),
    }


def test_neighbours_are_stored_nearest_first(sessions: sessionmaker[Session]) -> None:
    select_slice(sessions)

    with sessions() as session:
        neighbours = target(session, 5667316).neighbours

    assert neighbours, "the 23 d eclipsing binary has a neighbour at 3.3″"
    assert set(neighbours[0]) == {"source_id", "separation", "g"}
    assert neighbours[0]["separation"] == pytest.approx(3.3, abs=0.1)
    assert [n["separation"] for n in neighbours] == sorted(n["separation"] for n in neighbours)


def test_a_target_gaia_has_no_record_of_is_kept_without_gaia_columns(
    sessions: sessionmaker[Session],
) -> None:
    select_slice(sessions)

    with sessions() as session:
        missing = target(session, 7832051)
        # SQL NULL, which `IS NULL` matches, not JSON null.
        no_field = session.scalars(select(Target.gaia_source_id).where(Target.tess_field.is_(None)))
        assert list(no_field) == [NOT_IN_GAIA]

    assert missing.gaia_source_id == NOT_IN_GAIA
    assert missing.gaia_g is None
    assert missing.gaia_vari_detail is None
    assert missing.tess_field is None
    assert missing.neighbours == []


def test_gaia_record_rebuilds_the_stored_record_for_the_analyzer(
    sessions: sessionmaker[Session], recorded_gaia: dict[int, GaiaRecord]
) -> None:
    select_slice(sessions)

    with sessions() as session:
        stored = {t.gaia_source_id: t for t in session.scalars(select(Target))}

    rebuilt = {source_id: selector.gaia_record(found) for source_id, found in stored.items()}

    assert rebuilt.pop(NOT_IN_GAIA) is None
    assert rebuilt
    for source_id, record in rebuilt.items():
        found = stored[source_id]
        # Gaia's own position is not stored; the record carries the Target's.
        assert record == dataclasses.replace(recorded_gaia[source_id], ra=found.ra, dec=found.dec)


def test_running_twice_duplicates_no_targets_or_jobs(sessions: sessionmaker[Session]) -> None:
    select_slice(sessions)
    second = select_slice(sessions)

    with sessions() as session:
        assert count(session, Target) == len(DRACO_OIDS)
        assert count(session, Job) == len(DRACO_OIDS)
        # One Slice row per selection run (§4); the Targets stay with the first.
        assert count(session, Slice) == 2
        first_slice = session.scalars(select(Slice.id).order_by(Slice.id)).first()
        assert set(session.scalars(select(Target.slice_id))) == {first_slice}
    assert (second.n_targets, second.n_new, second.n_enqueued) == (len(DRACO_OIDS), 0, 0)


def test_a_rerun_refreshes_gaia_columns_but_never_status_and_queues_nothing_fetched(
    sessions: sessionmaker[Session], recorded_gaia: dict[int, GaiaRecord]
) -> None:
    select_slice(sessions)
    with sessions.begin() as session:
        rrab = target(session, 4060983)
        rrab.gaia_g = 99.0
        rrab.status = TargetStatus.FETCHED
        for job in session.scalars(select(Job).where(Job.target_id == rrab.id)):
            job.status = JobStatus.DONE

    second = select_slice(sessions)

    with sessions() as session:
        rrab = target(session, 4060983)
        assert rrab.gaia_g == pytest.approx(recorded_gaia[RRAB].g)
        assert rrab.status == TargetStatus.FETCHED
        assert count(session, Job) == len(DRACO_OIDS)
    assert second.n_enqueued == 0


def test_a_rerun_queues_a_target_whose_fetch_job_finished_while_it_stayed_selected(
    sessions: sessionmaker[Session],
) -> None:
    select_slice(sessions)
    with sessions.begin() as session:
        rrab = target(session, 4060983)
        for job in session.scalars(select(Job).where(Job.target_id == rrab.id)):
            job.status = JobStatus.FAILED

    assert select_slice(sessions).n_enqueued == 1


def test_two_vsx_entries_for_one_gaia_source_keep_the_lower_oid(
    sessions: sessionmaker[Session],
) -> None:
    vsx = recorded_vsx(RENAMED | {ASASSN_OID: ECL_23D})

    summary = select_slice(sessions, vsx)

    with sessions() as session:
        oids = set(session.scalars(select(Target.vsx_oid)))
    assert ASASSN_OID in oids
    assert 5667316 not in oids
    assert summary.n_targets == len(DRACO_OIDS)
    assert summary.duplicate_gaia_ids == 1


def test_a_later_run_bringing_a_stored_source_under_another_oid_keeps_the_stored_target(
    sessions: sessionmaker[Session],
) -> None:
    select_slice(sessions)

    # VSX has since added a second entry for the eclipsing binary's Gaia source.
    second = select_slice(sessions, recorded_vsx(RENAMED | {ASASSN_OID: ECL_23D}))

    with sessions() as session:
        holder = session.scalars(select(Target).where(Target.gaia_source_id == ECL_23D)).one()
        assert ASASSN_OID not in set(session.scalars(select(Target.vsx_oid)))
    assert holder.vsx_oid == 5667316
    assert second.duplicate_gaia_ids == 1
    assert (second.n_targets, second.n_new) == (len(DRACO_OIDS), 0)


def test_a_single_whole_magnitude_slice_bins_at_that_magnitude(
    sessions: sessionmaker[Session],
) -> None:
    entries = [
        dataclasses.replace(entry, mag_max=13.0)
        for entry in entries_from_table(recorded_vsx(), "Dra")
    ]

    with sessions.begin() as session:
        summary = selector.store(
            session,
            name="Dra-13-13",
            constellation="Dra",
            mag_min=13,
            mag_max=13,
            entries=entries,
            records={},
        )

    assert summary.mag_counts == {13: len(DRACO_OIDS)}


def test_an_amplitude_flag_is_stored(sessions: sessionmaker[Session]) -> None:
    vsx = recorded_vsx()
    vsx["f_min"][vsx["OID"] == 4060983] = "("

    select_slice(sessions, vsx)

    with sessions() as session:
        assert target(session, 4060983).vsx_mag_min_is_amplitude


def test_the_summary_prints_counts_and_the_magnitude_distribution() -> None:
    summary = SliceSummary(
        slice_id=3,
        name="Dra-13-16",
        n_targets=430,
        n_new=430,
        n_with_gaia=430,
        n_gaia_vari_class=0,
        n_gaia_period=430,
        n_enqueued=430,
        duplicate_gaia_ids=0,
        mag_counts={15: 200, 13: 80, 14: 150},
    )

    assert summary.lines() == [
        "Slice Dra-13-16 (id 3): 430 Targets, 430 new",
        "  with a Gaia DR3 record: 430; with a Gaia vari_* class: 0; with a Gaia period: 430",
        "  13–14 mag: 80",
        "  14–15 mag: 150",
        "  15–16 mag: 200",
        "  fetch Jobs queued: 430",
    ]


def test_the_default_name_drops_trailing_zeros() -> None:
    assert selector.default_name("Dra", 13.0, 16.0) == "Dra-13-16"
    assert selector.default_name("Dra", 13.5, 16) == "Dra-13.5-16"


def test_the_command_rejects_a_reversed_magnitude_range() -> None:
    result = runner.invoke(app, ["select-slice", "Dra", "16", "13"])

    assert result.exit_code == 2
    assert "fainter" in result.output


def test_the_command_rejects_an_unknown_constellation() -> None:
    result = runner.invoke(app, ["select-slice", "Xyz", "13", "16"])

    assert result.exit_code == 2
    assert "unknown constellation" in result.output
