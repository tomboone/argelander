"""Target status: derived from the Jobs and Proposals, counted, repaired and re-queued (spec §4).

`targets.status` is a cache the workers keep up to date as they go. `derive` is the rule it
caches, read off the other tables, so `recompute` can repair the column after a crash or a
hand edit, and `requeue` can leave a re-queued Target showing what happens to it next:

- the Target's Job that decides is its newest pending or claimed one, or else its newest;
- a `failed` Job makes the Target `failed`;
- a pending or claimed analyze Job leaves it `fetched`, as the fetch worker did on queueing it;
- a claimed fetch Job, or a pending one already tried, is `fetching`; one never tried is
  `selected`, which is also where a Target with no Job at all stands;
- a `done` fetch Job, with no analyze Job after it, is `fetched`;
- after a `done` analyze Job, the Target's newest Proposal decides: `proposed` until it has a
  Review or an approved or rejected status, then `reviewed`, then whatever its newest
  Submission says (`submitted`, `accepted`, `rejected`; a `prepared` one is still `reviewed`).
  An analyze Job always leaves a Proposal behind, so `analyzed` only shows without one.

The workers write the same answers as they go, so a clean run recomputes to no change. The
one difference is what a crash leaves behind: a fetch claim released as stale goes back to
`selected`, where the worker had left `fetching`. `requeue` keeps it that way by never giving
a Target a second Job while one of either kind is pending or claimed.
"""

from collections.abc import Collection
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from argelander.db import queue
from argelander.db.models import (
    Job,
    JobKind,
    JobStatus,
    Proposal,
    ProposalStatus,
    Review,
    Slice,
    Submission,
    SubmissionStatus,
    Target,
    TargetStatus,
)

_ACTIVE = (JobStatus.PENDING, JobStatus.CLAIMED)

_BY_SUBMISSION = {
    SubmissionStatus.PREPARED: TargetStatus.REVIEWED,
    SubmissionStatus.SUBMITTED: TargetStatus.SUBMITTED,
    SubmissionStatus.NEEDS_CHANGES: TargetStatus.SUBMITTED,
    SubmissionStatus.ACCEPTED: TargetStatus.ACCEPTED,
    SubmissionStatus.REJECTED: TargetStatus.REJECTED,
}


class UnknownTarget(Exception):
    """No Target has the VSX name `--target` gave."""


class UnknownSlice(Exception):
    """No Slice has the name `--slice` gave."""


@dataclass(frozen=True)
class JobState:
    """What `derive` needs of the Job that decides a Target's status."""

    kind: JobKind
    status: JobStatus
    attempts: int


@dataclass(frozen=True)
class ProposalState:
    """What `derive` needs of a Target's newest Proposal."""

    status: ProposalStatus
    reviewed: bool  # it has a Review
    submission: SubmissionStatus | None  # its newest Submission's status


@dataclass(frozen=True)
class Change:
    vsx_name: str
    was: TargetStatus
    now: TargetStatus


def derive(job: JobState | None, proposal: ProposalState | None) -> TargetStatus:
    """The status the module docstring's rule gives a Target."""
    if job is not None:
        if job.status == JobStatus.FAILED:
            return TargetStatus.FAILED
        if job.status in _ACTIVE:
            if job.kind == JobKind.ANALYZE:
                return TargetStatus.FETCHED
            if job.status == JobStatus.CLAIMED or job.attempts:
                return TargetStatus.FETCHING
            return TargetStatus.SELECTED
        if job.kind == JobKind.FETCH:
            return TargetStatus.FETCHED
    if proposal is None:
        return TargetStatus.SELECTED if job is None else TargetStatus.ANALYZED
    if proposal.submission is not None:
        return _BY_SUBMISSION[proposal.submission]
    if proposal.reviewed or proposal.status in (ProposalStatus.APPROVED, ProposalStatus.REJECTED):
        return TargetStatus.REVIEWED
    return TargetStatus.PROPOSED


def recompute(session: Session, target_ids: Collection[int] | None = None) -> list[Change]:
    """Set `targets.status` to what `derive` gives, for every Target or only `target_ids`;
    the Targets it changed. Works inside the caller's transaction and does not commit."""
    jobs = _deciding_jobs(session)
    proposals = _newest_proposals(session)
    query = select(Target).order_by(Target.id)
    if target_ids is not None:
        query = query.where(Target.id.in_(target_ids))
    changes = []
    for target in session.scalars(query):
        status = derive(jobs.get(target.id), proposals.get(target.id))
        if status != target.status:
            changes.append(Change(target.vsx_name, target.status, status))
            target.status = status
    return changes


def target_ids(
    session: Session,
    kind: JobKind,
    *,
    vsx_name: str | None = None,
    slice_name: str | None = None,
    failed: bool = False,
) -> list[int]:
    """The Targets `enqueue` names: the one called `vsx_name`, or a Slice's, or every one;
    with `failed`, only those whose newest Job of `kind` failed for good."""
    query = select(Target.id).order_by(Target.id)
    if vsx_name is not None:
        if session.scalar(select(Target.id).where(Target.vsx_name == vsx_name).limit(1)) is None:
            raise UnknownTarget(f"no Target is named {vsx_name!r}")
        query = query.where(Target.vsx_name == vsx_name)
    if slice_name is not None:
        query = query.where(Target.slice_id.in_(_slice_ids(session, slice_name)))
    if failed:
        newest = (
            select(Job.target_id, Job.status)
            .where(Job.kind == kind)
            .ext(distinct_on(Job.target_id))
            .order_by(Job.target_id, Job.id.desc())
            .subquery()
        )
        query = query.join(newest, newest.c.target_id == Target.id).where(
            newest.c.status == JobStatus.FAILED
        )
    return list(session.scalars(query))


def requeue(session: Session, kind: JobKind, ids: Collection[int]) -> int:
    """Queue a `kind` Job for each Target in `ids` and recompute their statuses; the number
    of Jobs queued.

    A Target with a Job of either kind pending or claimed is skipped, not only one of `kind`:
    a fetch queued while an analyze Job waits would finish without queueing its own analysis
    (`queue.enqueue` keeps one active Job per kind), and an analysis queued while a fetch Job
    waits would read the files that fetch is about to replace.
    """
    busy = set(
        session.scalars(
            select(Job.target_id).where(Job.target_id.in_(ids), Job.status.in_(_ACTIVE))
        )
    )
    queued = sum(
        queue.enqueue(session, kind, target_id) is not None
        for target_id in ids
        if target_id not in busy
    )
    recompute(session, ids)
    return queued


@dataclass(frozen=True)
class StatusCounts:
    """How many Targets stand at each status, and how many Jobs of each kind at each status."""

    scope: str  # "all Slices" or the Slice's name
    targets: dict[TargetStatus, int]
    jobs: dict[tuple[JobKind, JobStatus], int]

    def lines(self) -> list[str]:
        width = max(len(status) for status in TargetStatus)
        lines = [f"Targets ({self.scope}): {sum(self.targets.values())}"]
        lines += [
            f"  {status:<{width}}  {self.targets.get(status, 0):>6}" for status in TargetStatus
        ]
        lines.append(f"Jobs{'':<{width - 2}}" + "".join(f"  {s:>7}" for s in JobStatus))
        for kind in JobKind:
            counts = "".join(f"  {self.jobs.get((kind, s), 0):>7}" for s in JobStatus)
            lines.append(f"  {kind:<{width}}{counts}")
        return lines


def counts(session: Session, slice_name: str | None = None) -> StatusCounts:
    """Target and Job counts by status, for every Target or only one Slice's."""
    targets = select(Target.id)
    if slice_name is not None:
        targets = targets.where(Target.slice_id.in_(_slice_ids(session, slice_name)))
    # A Result has keys(), so dict() would take it for a mapping: build from the rows.
    by_status = {
        status: n
        for status, n in session.execute(
            select(Target.status, func.count(Target.id))
            .where(Target.id.in_(targets))
            .group_by(Target.status)
        )
    }
    by_job = {
        (kind, status): n
        for kind, status, n in session.execute(
            select(Job.kind, Job.status, func.count(Job.id))
            .where(Job.target_id.in_(targets))
            .group_by(Job.kind, Job.status)
        )
    }
    return StatusCounts(slice_name or "all Slices", by_status, by_job)


def _slice_ids(session: Session, name: str) -> list[int]:
    """Every `slices` row of that name: each `select-slice` run writes one."""
    ids = list(session.scalars(select(Slice.id).where(Slice.name == name)))
    if not ids:
        raise UnknownSlice(f"no Slice is named {name!r}")
    return ids


def _deciding_jobs(session: Session) -> dict[int, JobState]:
    """Each Target's newest pending or claimed Job, or else its newest, by Target id."""
    rows = session.execute(
        select(Job.target_id, Job.kind, Job.status, Job.attempts)
        .ext(distinct_on(Job.target_id))
        .order_by(Job.target_id, Job.status.in_(_ACTIVE).desc(), Job.id.desc())
    )
    return {
        target_id: JobState(kind, status, attempts) for target_id, kind, status, attempts in rows
    }


def _newest_proposals(session: Session) -> dict[int, ProposalState]:
    """Each Target's newest Proposal, whether it has a Review, and its newest Submission."""
    proposals = session.execute(
        select(Proposal.target_id, Proposal.id, Proposal.status)
        .ext(distinct_on(Proposal.target_id))
        .order_by(Proposal.target_id, Proposal.id.desc())
    )
    reviewed = set(session.scalars(select(Review.proposal_id).distinct()))
    submissions = {
        proposal_id: status
        for proposal_id, status in session.execute(
            select(Submission.proposal_id, Submission.status)
            .ext(distinct_on(Submission.proposal_id))
            .order_by(Submission.proposal_id, Submission.id.desc())
        )
    }
    return {
        target_id: ProposalState(status, proposal_id in reviewed, submissions.get(proposal_id))
        for target_id, proposal_id, status in proposals
    }
