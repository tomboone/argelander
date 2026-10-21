"""The job queue (ADR-0002) against a real Postgres: claims, outcomes, stale claims and
idempotent enqueue. The `engine` fixture (conftest.py) migrates a throwaway database."""

from datetime import UTC, datetime, timedelta
from itertools import count

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from argelander.db import queue
from argelander.db.models import Job, JobKind, JobStatus, Slice, Target
from argelander.db.session import sync_session_factory

_oids = count(1)


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    """Session factory on a database emptied of Jobs, Targets and Slices."""
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE jobs, targets, slices RESTART IDENTITY CASCADE"))
    return sync_session_factory(engine)


def _target(session: Session) -> int:
    """A new Target (in a new Slice) and its id."""
    slice_ = Slice(
        name="Dra-13-16", constellation="Dra", mag_min=13.0, mag_max=16.0, pipeline_version="0.1.0"
    )
    session.add(slice_)
    session.flush()
    oid = next(_oids)
    target = Target(
        slice_id=slice_.id,
        vsx_oid=oid,
        vsx_name=f"V{oid} Dra",
        gaia_source_id=oid,
        ra=270.0,
        dec=60.0,
    )
    session.add(target)
    session.flush()
    return target.id


def test_concurrent_claims_from_a_pool_of_one_give_it_to_exactly_one(
    sessions: sessionmaker[Session],
) -> None:
    with sessions.begin() as session:
        queue.enqueue(session, JobKind.FETCH, _target(session))

    # The first claim's transaction is still open, holding the row lock, when the second runs.
    with sessions() as first, sessions() as second:
        # Without SKIP LOCKED the second claim would wait on the lock: fail, don't hang.
        second.execute(text("SET LOCAL lock_timeout = '2s'"))
        claimed = queue.claim(first, JobKind.FETCH)
        assert queue.claim(second, JobKind.FETCH) is None
        first.commit()
        second.rollback()
        # Committed, the Job is no longer pending, so a later claim still finds nothing.
        assert queue.claim(second, JobKind.FETCH) is None

    assert claimed is not None
    assert claimed.status == JobStatus.CLAIMED
    assert claimed.claimed_at is not None


def test_claim_takes_the_oldest_pending_job_of_its_kind(sessions: sessionmaker[Session]) -> None:
    with sessions.begin() as session:
        oldest, newest = _target(session), _target(session)
        queue.enqueue(session, JobKind.ANALYZE, _target(session))
        queue.enqueue(session, JobKind.FETCH, newest)
        oldest_job = queue.enqueue(session, JobKind.FETCH, oldest)
        assert oldest_job is not None
        # The oldest Job has the higher id, so the claim has to go by `created_at`.
        oldest_job.created_at -= timedelta(minutes=1)

    with sessions.begin() as session:
        claimed = queue.claim(session, JobKind.FETCH)
        assert claimed is not None
        assert claimed.target_id == oldest


def test_complete_marks_the_job_done(sessions: sessionmaker[Session]) -> None:
    with sessions.begin() as session:
        queue.enqueue(session, JobKind.FETCH, _target(session))
    with sessions.begin() as session:
        job = queue.claim(session, JobKind.FETCH)
        assert job is not None
        queue.complete(job)

    with sessions() as session:
        stored = session.scalars(select(Job)).one()
        assert stored.status == JobStatus.DONE
        assert stored.finished_at is not None


def test_three_failures_move_a_job_to_failed_with_last_error(
    sessions: sessionmaker[Session],
) -> None:
    with sessions.begin() as session:
        queue.enqueue(session, JobKind.ANALYZE, _target(session))

    for attempt in range(1, 4):
        with sessions.begin() as session:
            job = queue.claim(session, JobKind.ANALYZE)
            assert job is not None, f"attempt {attempt}: the Job should be pending again"
            queue.fail(job, f"attempt {attempt} failed")

    with sessions() as session:
        stored = session.scalars(select(Job)).one()
        assert stored.status == JobStatus.FAILED
        assert stored.attempts == 3
        assert stored.last_error == "attempt 3 failed"
        assert stored.finished_at is not None
        assert queue.claim(session, JobKind.ANALYZE) is None


def test_release_stale_releases_claims_older_than_thirty_minutes(
    sessions: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    with sessions.begin() as session:
        stale, fresh = (
            Job(
                kind=JobKind.FETCH,
                target_id=_target(session),
                status=JobStatus.CLAIMED,
                claimed_at=now - timedelta(minutes=minutes),
            )
            for minutes in (31, 29)
        )
        session.add_all([stale, fresh])

    with sessions.begin() as session:
        assert queue.release_stale(session) == 1

    with sessions() as session:
        released, kept = session.get_one(Job, stale.id), session.get_one(Job, fresh.id)
        assert released.status == JobStatus.PENDING
        assert released.claimed_at is None
        assert kept.status == JobStatus.CLAIMED


def test_enqueue_twice_for_the_same_target_creates_one_job(
    sessions: sessionmaker[Session],
) -> None:
    with sessions.begin() as session:
        target_id = _target(session)
        assert queue.enqueue(session, JobKind.FETCH, target_id, {"surveys": ["ztf"]}) is not None
        assert queue.enqueue(session, JobKind.FETCH, target_id) is None
        # Idempotency is per kind: the Target can have an analyze Job beside its fetch Job.
        assert queue.enqueue(session, JobKind.ANALYZE, target_id) is not None

    with sessions() as session:
        jobs = session.scalars(select(Job).where(Job.kind == JobKind.FETCH)).all()
        assert [job.payload for job in jobs] == [{"surveys": ["ztf"]}]


def test_enqueue_skips_a_claimed_job_but_not_a_finished_one(
    sessions: sessionmaker[Session],
) -> None:
    with sessions.begin() as session:
        target_id = _target(session)
        queue.enqueue(session, JobKind.FETCH, target_id)
    with sessions.begin() as session:
        job = queue.claim(session, JobKind.FETCH)
        assert job is not None
        assert queue.enqueue(session, JobKind.FETCH, target_id) is None
        queue.complete(job)

    with sessions.begin() as session:
        assert queue.enqueue(session, JobKind.FETCH, target_id) is not None
