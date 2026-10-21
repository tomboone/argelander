"""The Postgres job queue (ADR-0002): enqueue, claim, complete, fail and release_stale.

Every function works inside the caller's transaction and none commits. A worker commits
straight after `claim`, so the claim is durable and its row lock is released before the work
starts; if the worker then dies, `release_stale` returns the Job to `pending`. `complete` and
`fail` change the Job in place for the caller's next commit.

Timestamps come from the Python clock, not Postgres' `now()`, so `release_stale` compares
`claimed_at` against the same clock that wrote it.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from argelander.db.models import JOB_ACTIVE, Job, JobKind, JobStatus

STALE_AFTER = timedelta(minutes=30)


def enqueue(
    session: Session, kind: JobKind, target_id: int, payload: dict[str, Any] | None = None
) -> Job | None:
    """A new pending Job, or None when the Target already has a pending or claimed Job of
    this kind. The partial unique index `uq_jobs_kind_target_id_active` decides, so two
    processes enqueueing the same Target at once still make one Job."""
    statement = (
        insert(Job)
        .values(kind=kind, target_id=target_id, payload=payload or {})
        .on_conflict_do_nothing(index_elements=[Job.kind, Job.target_id], index_where=JOB_ACTIVE)
        .returning(Job)
    )
    return session.scalars(statement).one_or_none()


def claim(session: Session, kind: JobKind) -> Job | None:
    """The oldest pending Job of `kind`, now claimed, or None when there is none to take.

    SKIP LOCKED passes over a Job another transaction is claiming, so two workers never
    take the same one. populate_existing reloads a Job this session already holds, since a
    session that does not expire on commit would otherwise hand back its stale attributes.
    """
    job = session.scalars(
        select(Job)
        .where(Job.kind == kind, Job.status == JobStatus.PENDING)
        .order_by(Job.created_at, Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    ).first()
    if job is not None:
        job.status = JobStatus.CLAIMED
        job.claimed_at = _now()
    return job


def complete(job: Job) -> None:
    job.status = JobStatus.DONE
    job.finished_at = _now()


def fail(job: Job, error: str) -> None:
    """Record a failed attempt: back to `pending` for another try, or `failed` for good once
    the Job has used `max_attempts`."""
    job.attempts += 1
    job.last_error = error
    if job.attempts >= job.max_attempts:
        job.status = JobStatus.FAILED
        job.finished_at = _now()
    else:
        job.status = JobStatus.PENDING
        job.claimed_at = None


def release_stale(session: Session, older_than: timedelta = STALE_AFTER) -> int:
    """Return to `pending` every Job claimed longer ago than `older_than`, whose worker is
    taken to have died; the number released."""
    released = session.scalars(
        update(Job)
        .where(Job.status == JobStatus.CLAIMED, Job.claimed_at < _now() - older_than)
        .values(status=JobStatus.PENDING, claimed_at=None)
        .returning(Job.id)
    ).all()
    return len(released)


def _now() -> datetime:
    return datetime.now(UTC)
