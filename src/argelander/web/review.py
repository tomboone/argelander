"""The review form (spec §9 `POST /proposals/{id}/review`): Tom's decision on one Proposal.

A Review approves a Proposal as it stands, approves it with edits, or rejects it, and always
carries notes: they are the record of why, and the training data for the rules, so a Review
without them is refused. Only a `queued` Proposal can be reviewed; a reviewed or superseded
one is never modified (spec §4, Rerun semantics).

An edit names the fields it changes in `reviews.edited`, keyed by the Proposal's column names
(`proposed_type`, `uncertain`, `period`, `epoch_bjd`, `mag_max`, `mag_min`, `band`) plus
`plot`, the kind of the plot attached to the Submission. The form arrives pre-filled with the
Proposal's values, and only the values that differ from them are kept. The Proposal itself
keeps the classifier's values; the form renderer (M7) reads the edits through the
Submission's Review.

Approving, with or without edits, prepares a Submission: `prepared`, attached to the chosen
plot (`submission.png` unless the edit picks another), its form fields and reference text
left for the renderer to fill.
"""

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Self

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from argelander.classify.types import PASSBAND_VOCABULARY, TYPE_VOCABULARY
from argelander.db.models import (
    Analysis,
    Proposal,
    ProposalStatus,
    Review,
    ReviewDecision,
    Submission,
)
from argelander.status import recompute
from argelander.submission.form import PASSBANDS
from argelander.web.proposal import plot_order, queue_neighbour

# The plot a Submission attaches unless an edit picks another (spec §9.1).
DEFAULT_PLOT = "submission"

# Any band a Proposal can carry: the dropdown's codes and the custom TESS band.
BANDS = PASSBAND_VOCABULARY | set(PASSBANDS.values())

# A full BJD, not a reduced one: anything earlier is 2400000 short (or a typo).
MIN_BJD = 2_400_000.0

# The editable fields, each with its label, in the form's order.
FIELD_LABELS = {
    "proposed_type": "Type",
    "uncertain": "Uncertain",
    "period": "Period (d)",
    "epoch_bjd": "Epoch (BJD)",
    "mag_max": "Maximum",
    "mag_min": "Minimum",
    "band": "Band",
    "plot": "Submission plot",
}


def _shown(value: float | None) -> str:
    # repr is the shortest string that reads back as the same float, so an untouched field
    # parses to exactly the Proposal's value and does not count as an edit.
    return "" if value is None else repr(value)


@dataclass
class ReviewForm:
    """The form as filled in, by input name, and what was wrong with it."""

    decision: str
    notes: str
    values: dict[str, str]  # the editable fields as typed; the type with VSX's `:` suffix
    errors: dict[str, str] = field(default_factory=dict)

    @classmethod
    def initial(cls, proposal: Proposal) -> Self:
        """The empty form, its fields pre-filled with the Proposal's values."""
        return cls(
            decision="",
            notes="",
            values={
                "proposed_type": proposal.proposed_type + (":" if proposal.uncertain else ""),
                "period": _shown(proposal.period),
                "epoch_bjd": _shown(proposal.epoch_bjd),
                "mag_max": _shown(proposal.mag_max),
                "mag_min": _shown(proposal.mag_min),
                "band": proposal.band or "",
                "plot": DEFAULT_PLOT,
            },
        )

    @classmethod
    def submitted(cls, data: Mapping[str, Any]) -> Self:
        """The form as posted; a field left out is blank."""

        def text(name: str) -> str:
            value = data.get(name)
            return value if isinstance(value, str) else ""

        names = (name for name in FIELD_LABELS if name != "uncertain")
        return cls(
            decision=text("decision"),
            notes=text("notes"),
            values={name: text(name).strip() for name in names},
        )


@dataclass(frozen=True)
class PostedReview:
    """A valid Review, ready to record."""

    decision: ReviewDecision
    notes: str
    edited: dict[str, Any] | None  # field → new value, on an edit
    plot: str | None  # the kind of plot to attach; None on a rejection


def default_plot(plots: Mapping[str, str]) -> str | None:
    """`submission.png` when the Analysis drew it; else None, and only an edit picks one.

    VSX takes one attachment, so the default is the composite (spec §9.1), never whichever
    plot happens to come first.
    """
    return DEFAULT_PLOT if DEFAULT_PLOT in plots else None


def _number(raw: str, check: Callable[[float], str | None] = lambda _: None) -> float | None:
    """A finite float, or None for a blank field; ValueError with the reason otherwise."""
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value):
        raise ValueError(f"{raw!r} is not a number.")
    if problem := check(value):
        raise ValueError(problem)
    return value


def _edits(form: ReviewForm, proposal: Proposal, plots: Mapping[str, str]) -> dict[str, Any]:
    """The fields the form changes from the Proposal's values, parsed; any that fail to parse
    are recorded in `form.errors` instead."""
    values = form.values
    parsed: dict[str, Any] = {}

    def parse(name: str, read: Callable[[str], Any]) -> None:
        try:
            parsed[name] = read(values.get(name, ""))
        except ValueError as error:
            form.errors[name] = str(error)

    def type_code(raw: str) -> str:
        code = raw.removesuffix(":").strip()
        if code not in TYPE_VOCABULARY:
            raise ValueError(f"{code!r} is not a VSX type code." if code else "Type is required.")
        return code

    def band(raw: str) -> str | None:
        if raw and raw not in BANDS:
            raise ValueError(f"{raw!r} is not a VSX passband.")
        return raw or None

    def plot(raw: str) -> str:
        if raw not in plots:
            raise ValueError(f"{raw!r} is not a plot of this Analysis.")
        return raw

    parse("proposed_type", type_code)
    parsed["uncertain"] = values.get("proposed_type", "").endswith(":")
    parse("period", lambda raw: _number(raw, lambda v: None if v > 0 else "must be positive."))
    full_bjd = f"must be a full BJD, at least {MIN_BJD:.0f}."
    parse("epoch_bjd", lambda raw: _number(raw, lambda v: None if v >= MIN_BJD else full_bjd))
    parse("mag_max", _number)
    parse("mag_min", _number)
    parse("band", band)
    if plots:
        parse("plot", plot)

    brightest, faintest = parsed.get("mag_max"), parsed.get("mag_min")
    amplitude = proposal.min_is_amplitude
    if brightest is not None and faintest is not None and not amplitude and brightest > faintest:
        form.errors["mag_max"] = (
            "Maximum is the brighter, smaller number: it cannot exceed the minimum."
        )

    current = {name: getattr(proposal, name) for name in FIELD_LABELS if name != "plot"}
    current["plot"] = default_plot(plots)
    return {
        name: value
        for name, value in parsed.items()
        if name not in form.errors and value != current[name]
    }


def validate(form: ReviewForm, proposal: Proposal, plots: Mapping[str, str]) -> PostedReview | None:
    """The Review the form describes, or None with `form.errors` saying what is wrong."""
    notes = form.notes.strip()
    if not notes:
        form.errors["notes"] = "Notes are required: they are the record of why."
    try:
        decision = ReviewDecision(form.decision)
    except ValueError:
        form.errors["decision"] = "Choose approve, edit or reject."
        return None

    edited: dict[str, Any] | None = None
    if decision == ReviewDecision.EDIT:
        edited = _edits(form, proposal, plots)
        if not edited and not form.errors:
            form.errors["decision"] = "Nothing was changed: approve it as it stands instead."
    plot = None
    if decision != ReviewDecision.REJECT:
        plot = (edited or {}).get("plot", default_plot(plots))
        if plot is None and not plots:
            form.errors["decision"] = "The Analysis has no plot to attach to a Submission."
        elif plot is None:
            form.errors["decision"] = (
                "The Analysis drew no submission plot: choose one to attach with an edit."
            )
    if form.errors:
        return None
    return PostedReview(decision, notes, edited, plot)


def _display(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    return "—" if value is None else str(value)


@dataclass(frozen=True)
class ReviewPanel:
    """The review region of the evidence page: the form while the Proposal is queued, the
    Review once it has one."""

    proposal: Proposal
    plots: tuple[tuple[str, str], ...]  # the plots a Submission can attach, in page order
    form: ReviewForm | None  # while the Proposal is queued
    review: Review | None
    edits: tuple[tuple[str, str], ...]  # the Review's edits as (label, value)
    submission: Submission | None
    next_id: int | None  # the queued Proposal to review next, once this one is reviewed


async def next_queued(session: AsyncSession, proposal: Proposal) -> int | None:
    """The queued Proposal after `proposal` in the queue's order, or the queue's top when it
    sorts last; None when nothing is queued."""
    after = await queue_neighbour(session, proposal, before=False)
    if after is not None:
        return after
    return await session.scalar(
        select(Proposal.id)
        .where(Proposal.status == ProposalStatus.QUEUED, Proposal.id != proposal.id)
        .order_by(Proposal.confidence.desc(), Proposal.id)
        .limit(1)
    )


async def review_panel(
    session: AsyncSession, proposal: Proposal, form: ReviewForm | None = None
) -> ReviewPanel:
    """The panel for `proposal`; `form` is the one just posted, when it failed."""
    analysis = await session.get_one(Analysis, proposal.analysis_id)
    review = await session.scalar(
        select(Review).where(Review.proposal_id == proposal.id).order_by(Review.id.desc())
    )
    submission = await session.scalar(
        select(Submission)
        .where(Submission.proposal_id == proposal.id)
        .order_by(Submission.id.desc())
    )
    queued = proposal.status == ProposalStatus.QUEUED
    edited = (review.edited if review else None) or {}
    edits = tuple((FIELD_LABELS.get(name, name), _display(value)) for name, value in edited.items())
    return ReviewPanel(
        proposal=proposal,
        plots=plot_order(analysis.plots),
        form=(form or ReviewForm.initial(proposal)) if queued else None,
        review=review,
        edits=edits,
        submission=submission,
        next_id=None if queued else await next_queued(session, proposal),
    )


@dataclass(frozen=True)
class Outcome:
    panel: ReviewPanel
    status_code: int  # 200 recorded, 409 not open to review, 422 invalid
    recorded: bool


async def submit_review(
    session: AsyncSession, proposal_id: int, data: Mapping[str, Any]
) -> Outcome | None:
    """Record the posted Review of `proposal_id` and commit; None when there is no such
    Proposal. An invalid form, or a Proposal no longer queued, writes nothing."""
    # The row lock makes a second Review of the same Proposal, posted at once, wait and then
    # find it reviewed.
    proposal = await session.scalar(
        select(Proposal).where(Proposal.id == proposal_id).with_for_update()
    )
    if proposal is None:
        return None
    # Nothing is written before a refusal, and the request's session ends with the request,
    # so there is nothing to roll back (and a rollback would expire the Proposal mid-render).
    if proposal.status != ProposalStatus.QUEUED:
        return Outcome(await review_panel(session, proposal), 409, recorded=False)

    analysis = await session.get_one(Analysis, proposal.analysis_id)
    plots: dict[str, str] = analysis.plots or {}
    form = ReviewForm.submitted(data)
    decision = validate(form, proposal, plots)
    if decision is None:
        return Outcome(await review_panel(session, proposal, form), 422, recorded=False)

    review = Review(
        proposal_id=proposal.id,
        decision=decision.decision,
        edited=decision.edited,
        notes=decision.notes,
    )
    session.add(review)
    await session.flush()
    if decision.plot is None:
        proposal.status = ProposalStatus.REJECTED
    else:
        proposal.status = ProposalStatus.APPROVED
        session.add(
            Submission(proposal_id=proposal.id, review_id=review.id, plot_path=plots[decision.plot])
        )
    await session.flush()
    target_id = proposal.target_id
    await session.run_sync(lambda sync: recompute(sync, [target_id]))
    await session.commit()
    return Outcome(await review_panel(session, proposal), 200, recorded=True)
