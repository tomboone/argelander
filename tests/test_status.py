"""Target status (spec §4): `derive`'s rule, `recompute`, `enqueue`'s Target choice and the
`status`, `enqueue` and `recompute-status` commands, against a real Postgres. The `engine`
fixture (conftest.py) migrates a throwaway database."""

from collections.abc import Iterator
from decimal import Decimal
from itertools import count

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from argelander import status
from argelander.cli import app
from argelander.config import get_settings
from argelander.db import queue
from argelander.db.models import (
    Analysis,
    Job,
    JobKind,
    JobStatus,
    Proposal,
    ProposalStatus,
    Review,
    ReviewDecision,
    Slice,
    Submission,
    SubmissionStatus,
    Target,
    TargetStatus,
)
from argelander.db.session import sync_session_factory
from argelander.status import JobState, ProposalState, derive

runner = CliRunner()
_oids = count(1)

FETCH, ANALYZE = JobKind.FETCH, JobKind.ANALYZE
PENDING, CLAIMED, DONE, FAILED = JobStatus


@pytest.mark.parametrize(
    ("job", "expected"),
    [
        # select-slice: a fresh Target and its fetch Job.
        (None, TargetStatus.SELECTED),
        (JobState(FETCH, PENDING, 0), TargetStatus.SELECTED),
        # The fetch worker's claim, and a failed attempt waiting to be claimed again.
        (JobState(FETCH, CLAIMED, 0), TargetStatus.FETCHING),
        (JobState(FETCH, PENDING, 1), TargetStatus.FETCHING),
        # Fetched, with analysis queued, claimed, or waiting after a failed attempt.
        (JobState(ANALYZE, PENDING, 0), TargetStatus.FETCHED),
        (JobState(ANALYZE, CLAIMED, 0), TargetStatus.FETCHED),
        (JobState(ANALYZE, PENDING, 2), TargetStatus.FETCHED),
        (JobState(FETCH, DONE, 1), TargetStatus.FETCHED),
        # Out of attempts, either kind.
        (JobState(FETCH, FAILED, 3), TargetStatus.FAILED),
        (JobState(ANALYZE, FAILED, 3), TargetStatus.FAILED),
        # Analysed, but no Proposal: the hand-off never leaves this, but the rule covers it.
        (JobState(ANALYZE, DONE, 1), TargetStatus.ANALYZED),
    ],
)
def test_derive_follows_the_deciding_job(job: JobState | None, expected: TargetStatus) -> None:
    assert derive(job, None) == expected


@pytest.mark.parametrize(
    ("proposal", "expected"),
    [
        (ProposalState(ProposalStatus.QUEUED, False, None), TargetStatus.PROPOSED),
        (ProposalState(ProposalStatus.QUEUED, True, None), TargetStatus.REVIEWED),
        (ProposalState(ProposalStatus.APPROVED, True, None), TargetStatus.REVIEWED),
        (ProposalState(ProposalStatus.REJECTED, False, None), TargetStatus.REVIEWED),
        (
            ProposalState(ProposalStatus.APPROVED, True, SubmissionStatus.PREPARED),
            TargetStatus.REVIEWED,
        ),
        (
            ProposalState(ProposalStatus.APPROVED, True, SubmissionStatus.SUBMITTED),
            TargetStatus.SUBMITTED,
        ),
        (
            ProposalState(ProposalStatus.APPROVED, True, SubmissionStatus.NEEDS_CHANGES),
            TargetStatus.SUBMITTED,
        ),
        (
            ProposalState(ProposalStatus.APPROVED, True, SubmissionStatus.ACCEPTED),
            TargetStatus.ACCEPTED,
        ),
        (
            ProposalState(ProposalStatus.APPROVED, True, SubmissionStatus.REJECTED),
            TargetStatus.REJECTED,
        ),
    ],
)
def test_derive_follows_the_newest_proposal_after_analysis(
    proposal: ProposalState, expected: TargetStatus
) -> None:
    assert derive(JobState(ANALYZE, DONE, 1), proposal) == expected


def test_a_waiting_job_outranks_the_proposal() -> None:
    proposed = ProposalState(ProposalStatus.QUEUED, False, None)
    assert derive(JobState(FETCH, PENDING, 0), proposed) == TargetStatus.SELECTED
    assert derive(JobState(ANALYZE, CLAIMED, 0), proposed) == TargetStatus.FETCHED


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    """Session factory on a database emptied of everything `status` reads, its Targets
    numbered from V0001 Dra again."""
    global _oids
    _oids = count(1)
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE submissions, reviews, proposals, analyses, jobs, targets, slices "
                "RESTART IDENTITY CASCADE"
            )
        )
    return sync_session_factory(engine)


def _slice(session: Session, name: str = "Dra-13-16") -> int:
    slice_ = Slice(
        name=name, constellation="Dra", mag_min=13.0, mag_max=16.0, pipeline_version="0.1.0"
    )
    session.add(slice_)
    session.flush()
    return slice_.id


def _target(session: Session, slice_id: int, status_: TargetStatus = TargetStatus.SELECTED) -> int:
    oid = next(_oids)
    target = Target(
        slice_id=slice_id,
        vsx_oid=oid,
        vsx_name=f"V{oid:04d} Dra",
        gaia_source_id=oid,
        ra=270.0,
        dec=60.0,
        status=status_,
    )
    session.add(target)
    session.flush()
    return target.id


def _job(
    session: Session, kind: JobKind, target_id: int, status_: JobStatus, attempts: int = 0
) -> None:
    session.add(Job(kind=kind, target_id=target_id, status=status_, attempts=attempts))
    session.flush()


def _proposal(session: Session, target_id: int) -> int:
    """A done fetch and analyze Job, an Analysis and its queued Proposal: a proposed Target."""
    _job(session, FETCH, target_id, DONE, 1)
    _job(session, ANALYZE, target_id, DONE, 1)
    analysis = Analysis(
        target_id=target_id, pipeline_version="0.1.0", inputs={}, periods={}, agreement=False
    )
    session.add(analysis)
    session.flush()
    proposal = Proposal(
        analysis_id=analysis.id,
        target_id=target_id,
        pipeline_version="0.1.0",
        proposed_type="VAR",
        confidence=Decimal("0.2"),
        evidence=[],
    )
    session.add(proposal)
    session.flush()
    return proposal.id


def _statuses(sessions: sessionmaker[Session]) -> dict[str, TargetStatus]:
    with sessions() as session:
        return dict(session.execute(select(Target.vsx_name, Target.status)).all())


def test_recompute_repairs_stale_statuses_and_reports_them(
    sessions: sessionmaker[Session],
) -> None:
    with sessions.begin() as session:
        slice_id = _slice(session)
        # Right already: left alone.
        fresh = _target(session, slice_id)
        _job(session, FETCH, fresh, PENDING)
        # The fetch worker died mid-claim and the claim was released: back to `selected`.
        released = _target(session, slice_id, TargetStatus.FETCHING)
        _job(session, FETCH, released, PENDING)
        # Proposed, but the column was never moved on.
        proposed = _target(session, slice_id, TargetStatus.FETCHED)
        _proposal(session, proposed)
        # Reviewed and submitted, with a newer queued Proposal beside the reviewed one.
        submitted = _target(session, slice_id, TargetStatus.PROPOSED)
        reviewed = _proposal(session, submitted)
        review = Review(proposal_id=reviewed, decision=ReviewDecision.APPROVE, notes="ok")
        session.add(review)
        session.flush()
        session.add(
            Submission(
                proposal_id=reviewed,
                review_id=review.id,
                form_fields={},
                reference_text="",
                plot_path="submission.png",
                status=SubmissionStatus.SUBMITTED,
            )
        )
        session.get_one(Proposal, reviewed).status = ProposalStatus.APPROVED

    with sessions.begin() as session:
        changes = status.recompute(session)

    assert changes == [
        status.Change("V0002 Dra", TargetStatus.FETCHING, TargetStatus.SELECTED),
        status.Change("V0003 Dra", TargetStatus.FETCHED, TargetStatus.PROPOSED),
        status.Change("V0004 Dra", TargetStatus.PROPOSED, TargetStatus.SUBMITTED),
    ]
    assert _statuses(sessions) == {
        "V0001 Dra": TargetStatus.SELECTED,
        "V0002 Dra": TargetStatus.SELECTED,
        "V0003 Dra": TargetStatus.PROPOSED,
        "V0004 Dra": TargetStatus.SUBMITTED,
    }
    with sessions.begin() as session:
        assert status.recompute(session) == []


def test_an_active_job_decides_even_when_a_newer_one_has_finished(
    sessions: sessionmaker[Session],
) -> None:
    """A waiting analyze Job decides over a newer finished fetch: `fetched`, not after it."""
    with sessions.begin() as session:
        target_id = _target(session, _slice(session), TargetStatus.FETCHED)
        _job(session, ANALYZE, target_id, PENDING)
        _job(session, FETCH, target_id, DONE, 1)

    with sessions.begin() as session:
        assert status.recompute(session) == []


def test_target_ids_by_name_slice_and_failure(sessions: sessionmaker[Session]) -> None:
    with sessions.begin() as session:
        draco = _slice(session)
        _slice(session)  # a second run of the same Slice
        lyra = _slice(session, "Lyr-13-16")
        retried = _target(session, draco)
        _job(session, FETCH, retried, FAILED, 3)
        _job(session, FETCH, retried, DONE, 1)
        gave_up = _target(session, draco, TargetStatus.FAILED)
        _job(session, FETCH, gave_up, FAILED, 3)
        analyze_failed = _target(session, lyra, TargetStatus.FAILED)
        _job(session, FETCH, analyze_failed, DONE, 1)
        _job(session, ANALYZE, analyze_failed, FAILED, 3)

    with sessions() as session:
        assert status.target_ids(session, FETCH, slice_name="Dra-13-16") == [retried, gave_up]
        assert status.target_ids(session, FETCH, vsx_name="V0002 Dra") == [gave_up]
        assert status.target_ids(session, FETCH, failed=True) == [gave_up]
        assert status.target_ids(session, ANALYZE, failed=True) == [analyze_failed]
        assert status.target_ids(session, ANALYZE, slice_name="Dra-13-16", failed=True) == []
        with pytest.raises(status.UnknownTarget, match="no Target is named 'V9999 Dra'"):
            status.target_ids(session, FETCH, vsx_name="V9999 Dra", failed=True)
        with pytest.raises(status.UnknownSlice, match="no Slice is named 'Cyg-13-16'"):
            status.target_ids(session, FETCH, slice_name="Cyg-13-16")


def test_requeue_skips_active_jobs_and_resets_the_status(sessions: sessionmaker[Session]) -> None:
    with sessions.begin() as session:
        slice_id = _slice(session)
        failed = _target(session, slice_id, TargetStatus.FAILED)
        _job(session, FETCH, failed, FAILED, 3)
        waiting = _target(session, slice_id)
        queue.enqueue(session, FETCH, waiting)
        # A fetch queued beside its waiting analyze Job would never get an analysis of its own.
        analysing = _target(session, slice_id, TargetStatus.FETCHED)
        _job(session, FETCH, analysing, DONE, 1)
        queue.enqueue(session, ANALYZE, analysing)

    with sessions.begin() as session:
        assert status.requeue(session, FETCH, [failed, waiting, analysing]) == 1

    assert _statuses(sessions) == {
        "V0001 Dra": TargetStatus.SELECTED,
        "V0002 Dra": TargetStatus.SELECTED,
        "V0003 Dra": TargetStatus.FETCHED,
    }
    with sessions() as session:
        jobs = session.execute(select(Job.target_id, Job.kind, Job.status).order_by(Job.id)).all()
    assert jobs == [
        (failed, FETCH, FAILED),
        (waiting, FETCH, PENDING),
        (analysing, FETCH, DONE),
        (analysing, ANALYZE, PENDING),
        (failed, FETCH, PENDING),
    ]


def test_counts_by_target_and_job_status(sessions: sessionmaker[Session]) -> None:
    with sessions.begin() as session:
        draco, lyra = _slice(session), _slice(session, "Lyr-13-16")
        _proposal(session, _target(session, draco, TargetStatus.PROPOSED))
        queue.enqueue(session, FETCH, _target(session, draco))
        queue.enqueue(session, FETCH, _target(session, lyra))

    with sessions() as session:
        draco_counts = status.counts(session, "Dra-13-16")
        every = status.counts(session)

    assert draco_counts.targets == {TargetStatus.PROPOSED: 1, TargetStatus.SELECTED: 1}
    assert draco_counts.jobs == {(FETCH, DONE): 1, (ANALYZE, DONE): 1, (FETCH, PENDING): 1}
    assert every.jobs[(FETCH, PENDING)] == 2
    lines = draco_counts.lines()
    assert lines[0] == "Targets (Dra-13-16): 2"
    assert "  proposed        1" in lines
    assert lines[-2:] == [
        "  fetch            1        0        1        0",
        "  analyze          0        0        1        0",
    ]


@pytest.fixture
def cli(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[CliRunner]:
    """The runner, with the commands' Settings pointed at the test database."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    yield runner
    get_settings.cache_clear()


def test_the_commands(sessions: sessionmaker[Session], cli: CliRunner) -> None:
    with sessions.begin() as session:
        slice_id = _slice(session)
        failed = _target(session, slice_id, TargetStatus.FAILED)
        _job(session, FETCH, failed, FAILED, 3)
        stale = _target(session, slice_id, TargetStatus.ANALYZED)
        _proposal(session, stale)

    recomputed = cli.invoke(app, ["recompute-status"])
    assert recomputed.exit_code == 0, recomputed.output
    assert recomputed.output == "V0002 Dra: analyzed -> proposed\nchanged 1 Targets\n"

    queued = cli.invoke(app, ["enqueue", "fetch", "--failed"])
    assert queued.exit_code == 0, queued.output
    assert queued.output == (
        "queued 1 fetch Jobs for 1 Targets (0 already had a Job pending or claimed)\n"
    )
    again = cli.invoke(app, ["enqueue", "fetch", "--slice", "Dra-13-16"])
    assert "queued 1 fetch Jobs for 2 Targets (1 already had a Job" in again.output

    counted = cli.invoke(app, ["status", "--slice", "Dra-13-16"])
    assert counted.exit_code == 0, counted.output
    assert counted.output.startswith("Targets (Dra-13-16): 2\n  selected        2\n")

    assert cli.invoke(app, ["recompute-status"]).output == "changed 0 Targets\n"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["enqueue", "plot", "--failed"], "'plot' is not one of fetch, analyze"),
        (["enqueue", "fetch"], "give --target, --slice or --failed"),
        (["enqueue", "fetch", "--target", "V1 Dra", "--slice", "Dra-13-16"], "not both"),
        (["enqueue", "fetch", "--target", "V9999 Dra"], "no Target is named"),
        (["enqueue", "fetch", "--slice", "Cyg-13-16"], "no Slice is named"),
        (["status", "--slice", "Cyg-13-16"], "no Slice is named"),
    ],
)
def test_bad_arguments_are_refused(
    sessions: sessionmaker[Session], cli: CliRunner, args: list[str], message: str
) -> None:
    refused = cli.invoke(app, args)
    assert refused.exit_code == 2
    # Rich wraps and colours the error panel, so match a phrase, not the whole line.
    assert message in refused.output
