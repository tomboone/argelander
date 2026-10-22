"""The Target history page (spec §9 `GET /targets/{id}`): everything that has happened to one star.

The page shows the Target's VSX and Gaia data, its Light Curve files for every survey, a
survey with no row shown as no data, and one timeline, oldest first, of its Analyses,
Proposals, Reviews and Submissions. A Submission enters the timeline when it is prepared, and
again when it is marked submitted and when its Verdict comes in, so a Verdict sits where it
happened rather than at the Submission's creation. Nothing on the page edits anything.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from argelander.db.models import (
    Analysis,
    LightCurveFile,
    Proposal,
    ProposalStatus,
    Review,
    Submission,
    SubmissionStatus,
    Survey,
    Target,
)

type EventKind = Literal["analysis", "proposal", "review", "prepared", "submitted", "verdict"]

# Events at one instant, as rows written in one transaction are, follow the pipeline's order.
EVENT_ORDER: tuple[EventKind, ...] = (
    "analysis",
    "proposal",
    "review",
    "prepared",
    "submitted",
    "verdict",
)

# The Submission statuses a Verdict gives it.
VERDICTS = (SubmissionStatus.ACCEPTED, SubmissionStatus.REJECTED)


@dataclass(frozen=True)
class SurveyFiles:
    survey: Survey
    file: LightCurveFile | None  # None when the survey was never fetched for the Target


@dataclass(frozen=True)
class Event:
    at: datetime
    kind: EventKind
    row: Analysis | Proposal | Review | Submission


@dataclass(frozen=True)
class TargetPage:
    target: Target
    surveys: tuple[SurveyFiles, ...]  # every survey, in `Survey`'s order
    timeline: tuple[Event, ...]  # oldest first


def timeline(
    analyses: list[Analysis],
    proposals: list[Proposal],
    reviews: list[Review],
    submissions: list[Submission],
) -> tuple[Event, ...]:
    """The rows' events, oldest first; a tie in the pipeline's order, then by id.

    A same-version rerun rewrites its Analysis row and its `queued` Proposal in place, and the
    Proposal keeps its `created_at` (spec §4, NEU-1721), so a queued Proposal is placed no
    earlier than its Analysis. A reviewed one stays where it was made, before its Review.
    """
    analysed = {a.id: a.finished_at or a.created_at for a in analyses}
    events = [Event(analysed[a.id], "analysis", a) for a in analyses]
    for p in proposals:
        at = p.created_at
        if p.status == ProposalStatus.QUEUED and p.analysis_id in analysed:
            at = max(at, analysed[p.analysis_id])
        events.append(Event(at, "proposal", p))
    events += [Event(r.reviewed_at, "review", r) for r in reviews]
    for s in submissions:
        events.append(Event(s.created_at, "prepared", s))
        if s.submitted_at is not None:
            events.append(Event(s.submitted_at, "submitted", s))
        # `needs_changes` sits before a Verdict (spec §9.2), whatever the row's times say.
        if s.verdict_at is not None and s.status in VERDICTS:
            events.append(Event(s.verdict_at, "verdict", s))
    return tuple(sorted(events, key=lambda e: (e.at, EVENT_ORDER.index(e.kind), e.row.id)))


async def target_page(session: AsyncSession, target_id: int) -> TargetPage | None:
    """The history page for one Target; None when there is no such Target."""
    target = await session.get(Target, target_id)
    if target is None:
        return None
    files = await session.scalars(
        select(LightCurveFile).where(LightCurveFile.target_id == target_id)
    )
    by_survey = {file.survey: file for file in files}
    analyses = await session.scalars(select(Analysis).where(Analysis.target_id == target_id))
    proposals = await session.scalars(select(Proposal).where(Proposal.target_id == target_id))
    reviews = await session.scalars(
        select(Review)
        .join(Proposal, Review.proposal_id == Proposal.id)
        .where(Proposal.target_id == target_id)
    )
    submissions = await session.scalars(
        select(Submission)
        .join(Proposal, Submission.proposal_id == Proposal.id)
        .where(Proposal.target_id == target_id)
    )
    return TargetPage(
        target=target,
        surveys=tuple(SurveyFiles(survey, by_survey.get(survey)) for survey in Survey),
        timeline=timeline(list(analyses), list(proposals), list(reviews), list(submissions)),
    )
