"""fetch-worker (spec §3, §5.1, §10): fake fetchers drive real Jobs through a real Postgres.

Every fetcher here is a fake answering from memory; nothing touches an archive. The `engine`
fixture (conftest.py) migrates a throwaway database. The mount-guard tests need no database.
"""

import asyncio
from collections.abc import Callable, Sequence
from itertools import count
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from argelander.cli import app
from argelander.config import Settings, get_settings
from argelander.db import queue
from argelander.db.models import (
    Job,
    JobKind,
    JobStatus,
    LightCurveFile,
    LightCurveFileStatus,
    Slice,
    Target,
    TargetStatus,
)
from argelander.db.session import sync_session_factory
from argelander.fetch import worker
from argelander.fetch.base import FetchError, TargetRef, TessField
from argelander.fetch.worker import (
    CACHE_MARKER,
    FETCHER_VERSION,
    CacheNotMounted,
    Fetchers,
    check_cache_mount,
    run_once,
)
from argelander.lightcurve import LightCurve, cache_path

_ids = count(1)
runner = CliRunner()

type Answer = Callable[[TargetRef], list[LightCurve]]


def _curve(survey: str, band: str, source_key: str, times: Sequence[float]) -> LightCurve:
    n = len(times)
    return LightCurve(
        survey=survey,
        source_key=source_key,
        band=band,
        time=np.asarray(times, dtype=np.float64),
        value=np.full(n, 14.0),
        error=np.full(n, 0.01),
        quality=np.zeros(n, dtype=np.int32),
        value_kind="mag",
        meta={},
    )


def _bands(survey: str, *bands: str, times: Sequence[float] = (1.0, 2.0, 3.0)) -> Answer:
    """An answer of one Light Curve per band, keyed by the Target's source ID."""
    return lambda target: [
        _curve(survey, band, f"{survey}-{target.gaia_source_id}-{band}", times) for band in bands
    ]


def _fails(survey: str) -> Answer:
    def answer(target: TargetRef) -> list[LightCurve]:
        raise FetchError(survey, 3, 503)

    return answer


class FakeFetcher:
    def __init__(self, survey: str, answer: Answer) -> None:
        self.survey = survey
        self.answer = answer
        self.asked: list[TargetRef] = []

    async def fetch(self, target: TargetRef) -> list[LightCurve]:
        self.asked.append(target)
        return self.answer(target)


class FakeBatchFetcher(FakeFetcher):
    def __init__(self, survey: str, answer: Answer) -> None:
        super().__init__(survey, answer)
        self.batches: list[list[int]] = []

    async def fetch_many(self, targets: Sequence[TargetRef]) -> dict[int, list[LightCurve]]:
        self.batches.append([target.gaia_source_id for target in targets])
        return {target.gaia_source_id: self.answer(target) for target in targets}


_ZTF = _bands("ztf", "g", "r")
_TESS = _bands("tess", "T")
_GAIA = _bands("gaia", "G", "BP", "RP")


def _nothing(target: TargetRef) -> list[LightCurve]:
    return []


def _fetchers(
    *, ztf: Answer = _ZTF, tess: Answer = _TESS, asassn: Answer = _nothing, gaia: Answer = _GAIA
) -> Fetchers:
    return Fetchers(
        per_target=[
            FakeFetcher("ztf", ztf),
            FakeFetcher("tess", tess),
            FakeFetcher("asassn", asassn),
        ],
        batched=[FakeBatchFetcher("gaia", gaia)],
    )


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    """Session factory on a database emptied of Jobs, Light Curve files, Targets and Slices."""
    with engine.begin() as connection:
        connection.execute(
            text("TRUNCATE jobs, light_curve_files, targets, slices RESTART IDENTITY CASCADE")
        )
    return sync_session_factory(engine)


def _queued_target(sessions: sessionmaker[Session], *, max_attempts: int = 3) -> int:
    """A new `selected` Target with a pending fetch Job; the Target's id."""
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
            vsx_name=f"V{n} Dra",
            gaia_source_id=1_000_000 + n,
            ra=270.0,
            dec=60.0,
            tess_field={"n_sources": 2, "brightest_g": 15.5, "combined_g": 15.1},
        )
        session.add(target)
        session.flush()
        job = queue.enqueue(session, JobKind.FETCH, target.id)
        assert job is not None
        job.max_attempts = max_attempts
        return target.id


def _run(sessions: sessionmaker[Session], fetchers: Fetchers, cache_dir: Path) -> int:
    return asyncio.run(run_once(sessions, fetchers, cache_dir, batch_size=10))


def _rows(session: Session, target_id: int) -> dict[str, LightCurveFile]:
    rows = session.scalars(select(LightCurveFile).where(LightCurveFile.target_id == target_id))
    return {row.survey.value: row for row in rows}


def _jobs(session: Session, target_id: int, kind: JobKind) -> list[Job]:
    return list(session.scalars(select(Job).where(Job.target_id == target_id, Job.kind == kind)))


def test_a_claimed_job_fills_the_cache_indexes_it_and_queues_one_analyze_job(
    sessions: sessionmaker[Session], tmp_path: Path
) -> None:
    target_id = _queued_target(sessions)
    fetchers = _fetchers(ztf=_bands("ztf", "g", "r", times=(2.0, 5.0)))

    assert _run(sessions, fetchers, tmp_path) == 1

    with sessions() as session:
        target = session.get_one(Target, target_id)
        source = target.gaia_source_id
        rows = _rows(session, target_id)
        (fetch_job,) = _jobs(session, target_id, JobKind.FETCH)
        analyze_jobs = _jobs(session, target_id, JobKind.ANALYZE)

    assert target.status == TargetStatus.FETCHED
    assert fetch_job.status == JobStatus.DONE
    assert [job.status for job in analyze_jobs] == [JobStatus.PENDING]

    for survey, bands in {"ztf": ["g", "r"], "tess": ["T"], "gaia": ["G", "BP", "RP"]}.items():
        for band in bands:
            stored = LightCurve.from_parquet(cache_path(tmp_path, survey, source, band))
            assert stored.source_key == f"{survey}-{source}-{band}"
        row = rows[survey]
        assert row.status == LightCurveFileStatus.OK
        assert row.bands == bands
        assert row.path == f"{survey}/{source}"
        assert row.error is None
        assert row.fetcher_version == FETCHER_VERSION
    assert rows["ztf"].source_key == f"ztf-{source}-g+ztf-{source}-r"
    assert (rows["ztf"].n_points, rows["ztf"].t_min_bjd, rows["ztf"].t_max_bjd) == (4, 2.0, 5.0)
    assert rows["gaia"].n_points == 9

    assert rows["asassn"].status == LightCurveFileStatus.EMPTY
    assert (rows["asassn"].path, rows["asassn"].source_key, rows["asassn"].n_points) == (
        None,
        None,
        0,
    )
    assert not (tmp_path / "asassn").exists()

    # The fetchers saw the Target as select-slice stored it, TESS field included.
    tess = fetchers.per_target[1]
    assert isinstance(tess, FakeFetcher)
    (asked,) = tess.asked
    assert asked.tess_field == TessField(n_sources=2, brightest_g=15.5, combined_g=15.1)


def test_one_survey_failing_leaves_the_job_done_with_that_surveys_row_failed(
    sessions: sessionmaker[Session], tmp_path: Path
) -> None:
    target_id = _queued_target(sessions)

    _run(sessions, _fetchers(tess=_fails("tess")), tmp_path)

    with sessions() as session:
        rows = _rows(session, target_id)
        (fetch_job,) = _jobs(session, target_id, JobKind.FETCH)
        analyze_jobs = _jobs(session, target_id, JobKind.ANALYZE)
        status = session.get_one(Target, target_id).status

    assert fetch_job.status == JobStatus.DONE
    assert status == TargetStatus.FETCHED
    assert len(analyze_jobs) == 1
    assert rows["tess"].status == LightCurveFileStatus.FAILED
    assert rows["tess"].error == (
        "FetchError: tess: gave up after 3 attempts; last failure: HTTP 503"
    )
    assert rows["tess"].path is None
    assert rows["ztf"].status == LightCurveFileStatus.OK


def test_every_survey_failing_fails_the_job_and_queues_no_analysis(
    sessions: sessionmaker[Session], tmp_path: Path
) -> None:
    retried = _queued_target(sessions)
    spent = _queued_target(sessions, max_attempts=1)
    fetchers = _fetchers(
        ztf=_fails("ztf"), tess=_fails("tess"), asassn=_fails("asassn"), gaia=_fails("gaia")
    )

    assert _run(sessions, fetchers, tmp_path) == 2

    with sessions() as session:
        (retried_job,) = _jobs(session, retried, JobKind.FETCH)
        (spent_job,) = _jobs(session, spent, JobKind.FETCH)
        assert _jobs(session, retried, JobKind.ANALYZE) == []
        assert {row.status for row in _rows(session, retried).values()} == {
            LightCurveFileStatus.FAILED
        }
        retried_status = session.get_one(Target, retried).status
        spent_status = session.get_one(Target, spent).status

    # Attempts left: back in the queue. None left: failed for good, and so is the Target.
    assert (retried_job.status, retried_job.attempts) == (JobStatus.PENDING, 1)
    assert retried_status == TargetStatus.FETCHING
    assert "ztf: FetchError" in (retried_job.last_error or "")
    assert spent_job.status == JobStatus.FAILED
    assert spent_status == TargetStatus.FAILED


def test_jobs_claimed_together_share_one_gaia_batch(
    sessions: sessionmaker[Session], tmp_path: Path
) -> None:
    target_ids = [_queued_target(sessions) for _ in range(3)]
    fetchers = _fetchers()

    assert _run(sessions, fetchers, tmp_path) == 3

    with sessions() as session:
        sources = [session.get_one(Target, id_).gaia_source_id for id_ in target_ids]
        statuses = {session.get_one(Target, id_).status for id_ in target_ids}
    (gaia,) = fetchers.batched
    assert isinstance(gaia, FakeBatchFetcher)
    assert gaia.batches == [sources]
    assert statuses == {TargetStatus.FETCHED}
    assert _run(sessions, fetchers, tmp_path) == 0  # the queue is empty now


def test_a_refetch_replaces_the_row_and_the_band_files_it_no_longer_has(
    sessions: sessionmaker[Session], tmp_path: Path
) -> None:
    target_id = _queued_target(sessions)
    _run(sessions, _fetchers(), tmp_path)
    with sessions.begin() as session:
        queue.enqueue(session, JobKind.FETCH, target_id)

    _run(sessions, _fetchers(ztf=_bands("ztf", "r"), tess=_nothing), tmp_path)

    with sessions() as session:
        source = session.get_one(Target, target_id).gaia_source_id
        rows = _rows(session, target_id)
        n_rows = len(list(session.scalars(select(LightCurveFile))))
    assert n_rows == 4
    assert rows["ztf"].bands == ["r"]
    assert not cache_path(tmp_path, "ztf", source, "g").exists()
    assert cache_path(tmp_path, "ztf", source, "r").exists()
    assert rows["tess"].status == LightCurveFileStatus.EMPTY
    assert not (tmp_path / "tess" / str(source)).exists()


def test_a_light_curve_the_cache_cannot_take_fails_only_its_survey(
    sessions: sessionmaker[Session], tmp_path: Path
) -> None:
    target_id = _queued_target(sessions)
    two_g = _bands("ztf", "g", "g")

    _run(sessions, _fetchers(ztf=two_g), tmp_path)

    with sessions() as session:
        rows = _rows(session, target_id)
        (fetch_job,) = _jobs(session, target_id, JobKind.FETCH)
    assert rows["ztf"].status == LightCurveFileStatus.FAILED
    assert "two Light Curves for one band" in (rows["ztf"].error or "")
    assert fetch_job.status == JobStatus.DONE


def test_a_job_that_breaks_outside_its_surveys_fails_and_fails_its_spent_target(
    sessions: sessionmaker[Session], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target_id = _queued_target(sessions, max_attempts=1)

    def broken(*args: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(worker, "record", broken)
    _run(sessions, _fetchers(), tmp_path)

    with sessions() as session:
        (fetch_job,) = _jobs(session, target_id, JobKind.FETCH)
        status = session.get_one(Target, target_id).status
    assert (fetch_job.status, fetch_job.last_error) == (JobStatus.FAILED, "RuntimeError: disk full")
    assert status == TargetStatus.FAILED


class _Stop(BaseException):
    """Ends `worker.run`'s endless loop from inside a test."""


def test_run_outlives_a_failed_pass_and_sleeps_before_the_next(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    passes: list[int] = []
    sleeps: list[float] = []

    async def run_once(*args: object, **kwargs: object) -> int:
        passes.append(len(passes))
        if len(passes) == 1:
            raise RuntimeError("Postgres is restarting")
        raise _Stop

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(worker, "run_once", run_once)
    monkeypatch.setattr(worker.asyncio, "sleep", sleep)
    settings = Settings(
        _env_file=None, database_url="postgresql+psycopg://u:p@nowhere/x", fetch_poll_interval=7
    )

    with pytest.raises(_Stop):
        asyncio.run(worker.run(settings, sync_session_factory(), _fetchers()))

    assert passes == [0, 1]
    assert sleeps == [7]


def test_check_cache_mount_refuses_a_directory_without_the_cache_marker(tmp_path: Path) -> None:
    with pytest.raises(CacheNotMounted, match="ALLOW_UNMOUNTED_CACHE"):
        check_cache_mount(tmp_path, allow_unmounted=False)
    # A mount point is not enough: inside a container every bind-mount target is one.
    with pytest.raises(CacheNotMounted):
        check_cache_mount(Path("/"), allow_unmounted=False)

    check_cache_mount(tmp_path, allow_unmounted=True)
    (tmp_path / CACHE_MARKER).touch()
    check_cache_mount(tmp_path, allow_unmounted=False)


def test_fetch_worker_refuses_to_start_without_the_cache_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@nowhere:5432/argelander")
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("ALLOW_UNMOUNTED_CACHE", "0")
    get_settings.cache_clear()
    try:
        result = runner.invoke(app, ["fetch-worker"])
    finally:
        get_settings.cache_clear()

    assert result.exit_code == 1
    assert "has no .argelander-cache marker" in result.output
