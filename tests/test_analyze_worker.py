"""analyze-worker (spec §3, §4, §6): fixture Light Curves in a temp Cache, real Jobs through a
real Postgres.

The Target is V0379 Dra, a positive of the prototype set at 0.3576 d, with its TESS and
Gaia epoch Light Curves from `fixtures/lightcurves` and its Gaia record from the catalog join
recorded in `fixtures/catalog`. ZTF is left out, as a `failed` row, to keep the module fast:
BLS over ZTF's baseline is the pipeline's slow step. The pipeline is deterministic, so
`analyze` runs once per module and later calls replay its result.
"""

import dataclasses
import json
import shutil
from datetime import UTC, datetime
from itertools import count
from pathlib import Path

import numpy as np
import pytest
from astropy.table import Table
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from argelander.analysis import worker
from argelander.analysis.model import Analysis, Peak, PeriodogramResult
from argelander.analysis.pipeline import analyze
from argelander.analysis.worker import analysis_columns, run_once
from argelander.catalog.gaia import GaiaRecord, records_from_tables
from argelander.cli import app
from argelander.config import get_settings
from argelander.db import queue
from argelander.db.models import (
    AgreementRelation,
    Job,
    JobKind,
    JobStatus,
    LightCurveFile,
    LightCurveFileStatus,
    Slice,
    Survey,
    Target,
    TargetStatus,
)
from argelander.db.models import Analysis as AnalysisRow
from argelander.db.session import sync_session_factory
from argelander.lightcurve import LightCurve, cache_path
from argelander.selector import gaia_columns

FIXTURES = Path(__file__).parent / "fixtures"
GAIA_SOURCE_ID = 1439840098963073152  # V0379 Dra
PERIOD = 0.35762  # VSX's, which ZTF confirms
BANDS = {Survey.TESS: ["T"], Survey.GAIA: ["G", "BP", "RP"]}

_ids = count(1)
runner = CliRunner()
_analyses: dict[int, tuple[Analysis, list[LightCurve]]] = {}


@pytest.fixture(scope="module")
def record() -> GaiaRecord:
    names = ("catalog", "cross_ids", "neighbours", "tess_field")
    tables = (Table.read(FIXTURES / "catalog" / f"{name}.xml", format="votable") for name in names)
    return records_from_tables(*tables)[GAIA_SOURCE_ID]


@pytest.fixture
def sessions(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> sessionmaker[Session]:
    """Session factory on an emptied database, with `analyze` replaying its first result."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE analyses, jobs, light_curve_files, targets, slices "
                "RESTART IDENTITY CASCADE"
            )
        )

    def replayed(
        curves: list[LightCurve], record: GaiaRecord | None, *, target_id: int
    ) -> tuple[Analysis, list[LightCurve]]:
        assert record is not None
        if record.source_id not in _analyses:
            _analyses[record.source_id] = analyze(curves, record, target_id=target_id)
        found, cleaned = _analyses[record.source_id]
        return dataclasses.replace(found, target_id=target_id), cleaned

    monkeypatch.setattr(worker, "analyze", replayed)
    return sync_session_factory(engine)


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    """A Cache holding the Target's fixture Light Curves at their Cache paths."""
    for survey, bands in BANDS.items():
        for band in bands:
            destination = cache_path(tmp_path, survey, GAIA_SOURCE_ID, band)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(
                cache_path(FIXTURES / "lightcurves", survey, GAIA_SOURCE_ID, band), destination
            )
    return tmp_path


def _queued_target(
    sessions: sessionmaker[Session], record: GaiaRecord, *, ok: bool = True, max_attempts: int = 3
) -> int:
    """A `fetched` Target with its Light Curve files indexed and a pending analyze Job; its id.

    With `ok`, TESS and Gaia are `ok` and ZTF `failed`; without, ZTF is `failed` and the rest
    `empty`.
    """
    with sessions.begin() as session:
        slice_ = Slice(
            name="Dra-13-16",
            constellation="Dra",
            mag_min=13.0,
            mag_max=16.0,
            pipeline_version="0.1.0",
        )
        session.add(slice_)
        session.flush()
        n = next(_ids)
        target = Target(
            slice_id=slice_.id,
            vsx_oid=n,
            vsx_name="V0379 Dra",
            gaia_source_id=GAIA_SOURCE_ID,
            ra=record.ra,
            dec=record.dec,
            status=TargetStatus.FETCHED,
            **gaia_columns(record),
        )
        session.add(target)
        session.flush()
        session.add(_file(target.id, Survey.ZTF, LightCurveFileStatus.FAILED))
        session.add(_file(target.id, Survey.ASASSN, LightCurveFileStatus.EMPTY))
        for survey, bands in BANDS.items():
            status = LightCurveFileStatus.OK if ok else LightCurveFileStatus.EMPTY
            session.add(_file(target.id, survey, status, bands if ok else []))
        job = queue.enqueue(session, JobKind.ANALYZE, target.id)
        assert job is not None
        job.max_attempts = max_attempts
        return target.id


def _file(
    target_id: int, survey: Survey, status: LightCurveFileStatus, bands: list[str] | None = None
) -> LightCurveFile:
    ok = status == LightCurveFileStatus.OK
    return LightCurveFile(
        target_id=target_id,
        survey=survey,
        status=status,
        source_key=f"{survey}-key" if ok else None,
        path=f"{survey}/{GAIA_SOURCE_ID}" if ok else None,
        n_points=500 if ok else 0,
        t_min_bjd=2458600.0 if ok else None,
        t_max_bjd=2459700.0 if ok else None,
        bands=bands or [],
        fetched_at=datetime.now(UTC),
        fetcher_version="1",
        error="FetchError: ztf: HTTP 503" if status == LightCurveFileStatus.FAILED else None,
    )


def _rows(session: Session, target_id: int) -> list[AnalysisRow]:
    return list(session.scalars(select(AnalysisRow).where(AnalysisRow.target_id == target_id)))


def _job(session: Session, target_id: int) -> Job:
    return session.scalars(
        select(Job).where(Job.target_id == target_id, Job.kind == JobKind.ANALYZE)
    ).one()


def test_an_analyze_job_writes_one_analyses_row_and_moves_its_target_to_analyzed(
    sessions: sessionmaker[Session], cache_dir: Path, record: GaiaRecord
) -> None:
    target_id = _queued_target(sessions, record)

    assert run_once(sessions, cache_dir)

    with sessions() as session:
        (row,) = _rows(session, target_id)
        job = _job(session, target_id)
        target = session.get_one(Target, target_id)
        ids = {f.survey: f.id for f in session.scalars(select(LightCurveFile))}

    assert (job.status, target.status) == (JobStatus.DONE, TargetStatus.ANALYZED)
    assert row.pipeline_version == "0.1.0"
    assert row.started_at is not None and row.finished_at is not None
    assert row.started_at <= row.finished_at

    # Only the `ok` surveys went in, each traced to its Light Curve file.
    assert row.inputs == {
        survey.value: {
            "light_curve_file_id": ids[survey],
            "n_points": 500,
            "t_min_bjd": 2458600.0,
            "t_max_bjd": 2459700.0,
            "bands": bands,
        }
        for survey, bands in BANDS.items()
    }

    # Periods: per survey, band and method, five peaks strongest first, Gaia's period scored.
    assert set(row.periods) == {"tess", "gaia"}
    assert set(row.periods["gaia"]) == {"G", "BP", "RP"}
    tess_ls = row.periods["tess"]["T"]["lomb_scargle"]
    assert len(tess_ls["peaks"]) == 5
    assert set(tess_ls["peaks"][0]) == {"period", "power", "false_alarm_probability"}
    assert tess_ls["gaia"]["period"] == pytest.approx(record.vari_period)
    assert row.periods["tess"]["T"]["bls"]["peaks"][0]["false_alarm_probability"] is None

    # Alias ranking: every hypothesis, lowest score first.
    assert row.alias_tests is not None
    ranking = row.alias_tests["tess"]["T"]["ranking"]
    assert len(ranking) > 1
    assert [h["score"] for h in ranking] == sorted(h["score"] for h in ranking)
    assert row.alias_tests["tess"]["T"]["baseline"] > 0

    # TESS and Gaia's published period agree on the Target's period.
    assert row.agreement
    assert row.agreed_period == pytest.approx(PERIOD, rel=1e-3)
    assert row.agreed_sources == ["tess", "gaia_published"]
    assert row.agreement_relation == AgreementRelation.SAME
    assert row.agreement_tolerance is not None and row.agreement_tolerance >= 0.001

    assert row.blend is not None and set(row.blend) == {"contaminations", "zero_points"}
    assert row.cmd is not None
    assert row.cmd["region"] in {"main_sequence", "giant_branch", "instability_strip"}
    assert row.cmd["bp_rp"] == pytest.approx(record.bp_rp)
    assert row.plots is None  # drawn by the hand-off once NEU-1721 fills it
    with sessions() as session:  # SQL NULL, which `IS NULL` matches, not JSON null
        assert session.scalar(select(AnalysisRow.id).where(AnalysisRow.plots.is_(None)))


def test_a_rerun_at_the_same_pipeline_version_overwrites_the_row(
    sessions: sessionmaker[Session],
    cache_dir: Path,
    record: GaiaRecord,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_id = _queued_target(sessions, record)
    run_once(sessions, cache_dir)
    with sessions.begin() as session:
        (first,) = _rows(session, target_id)
        first.agreed_period = 99.0  # stale: the rerun must replace it
        queue.enqueue(session, JobKind.ANALYZE, target_id)

    assert run_once(sessions, cache_dir)

    with sessions() as session:
        (again,) = _rows(session, target_id)
    assert again.id == first.id
    assert again.agreed_period == pytest.approx(PERIOD, rel=1e-3)
    assert first.finished_at is not None and again.finished_at is not None
    assert again.finished_at > first.finished_at

    # A new Pipeline Version adds its own row beside the old one.
    replayed = worker.analyze

    def bumped(
        curves: list[LightCurve], record: GaiaRecord | None, *, target_id: int
    ) -> tuple[Analysis, list[LightCurve]]:
        found, cleaned = replayed(curves, record, target_id=target_id)
        return dataclasses.replace(found, pipeline_version="0.2.0"), cleaned

    monkeypatch.setattr(worker, "analyze", bumped)
    with sessions.begin() as session:
        queue.enqueue(session, JobKind.ANALYZE, target_id)
    run_once(sessions, cache_dir)

    with sessions() as session:
        versions = sorted(row.pipeline_version for row in _rows(session, target_id))
    assert versions == ["0.1.0", "0.2.0"]


def test_a_target_without_ok_light_curves_fails_its_job(
    sessions: sessionmaker[Session], cache_dir: Path, record: GaiaRecord
) -> None:
    retried = _queued_target(sessions, record, ok=False)

    assert run_once(sessions, cache_dir)

    with sessions() as session:
        job = _job(session, retried)
        assert _rows(session, retried) == []
        status = session.get_one(Target, retried).status
    assert (job.status, job.attempts) == (JobStatus.PENDING, 1)
    assert job.last_error == "NoLightCurves: V0379 Dra has no ok Light Curves to analyze"
    assert status == TargetStatus.FETCHED


def test_a_target_without_ok_light_curves_fails_for_good_once_its_attempts_are_spent(
    sessions: sessionmaker[Session], cache_dir: Path, record: GaiaRecord
) -> None:
    spent = _queued_target(sessions, record, ok=False, max_attempts=1)

    run_once(sessions, cache_dir)

    with sessions() as session:
        job = _job(session, spent)
        status = session.get_one(Target, spent).status
    assert job.status == JobStatus.FAILED
    assert "has no ok Light Curves" in (job.last_error or "")
    assert status == TargetStatus.FAILED


def test_run_once_is_false_on_an_empty_queue(
    sessions: sessionmaker[Session], cache_dir: Path
) -> None:
    assert not run_once(sessions, cache_dir)


def test_analysis_columns_hold_only_json_values() -> None:
    """NaN has no JSON token, and Postgres' JSONB rejects the one Python writes."""
    analysis = Analysis(target_id=1, pipeline_version="0.1.0")
    analysis.periods.append(
        PeriodogramResult(
            survey="ztf",
            band="g",
            method="lomb_scargle",
            n_points=40,
            frequency=np.array([1.0]),
            power=np.array([0.5]),
            peaks=(Peak(np.float64(0.5), 0.25, float("nan")),),
            gaia=None,
            insufficient=False,
        )
    )

    columns = analysis_columns(analysis, [])

    json.dumps(columns, allow_nan=False)
    (peak,) = columns["periods"]["ztf"]["g"]["lomb_scargle"]["peaks"]
    assert peak == {"period": 0.5, "power": 0.25, "false_alarm_probability": None}
    assert columns["agreement"] is False
    assert columns["agreed_period"] is None


def test_analyze_worker_refuses_to_start_on_an_unmounted_cache_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@nowhere:5432/argelander")
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("ALLOW_UNMOUNTED_CACHE", "0")
    get_settings.cache_clear()
    try:
        result = runner.invoke(app, ["analyze-worker"])
    finally:
        get_settings.cache_clear()

    assert result.exit_code == 1
    assert "is not a mount point" in result.output
