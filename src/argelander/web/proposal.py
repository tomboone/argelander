"""The evidence page (spec §9 `GET /proposals/{id}`): one Proposal with everything behind it.

The page sets the Proposal's fields beside VSX's current values and lays out its Analysis:
every plot in `analyses.plots`, the Evidence grouped by type, each survey's top peaks and
alias ranking, the blend checks, the CMD position and Gaia's own class and period. The epoch
is shown in HJD as well as BJD_TDB, since VSX's revision form takes HJD.

When the Target has an earlier Proposal, superseded, approved or rejected, the page shows the
newest of them field by field beside this one, the changed fields marked, and an earlier
Proposal links to the Target's newest one (spec §4, Rerun semantics). Without Agreement the
Agreement rule's explanation for the best-scoring type leads the page: for a periodic type it
is why the Proposal became `VAR`. Whether there was Agreement is read off the Proposal's own
Evidence, not its Analysis row, which a same-version rerun overwrites beneath a reviewed
Proposal (spec §4, NEU-1721).

Previous and next follow the queue's order (Confidence descending, then id) over `queued`
Proposals, so they step through the queue as `GET /` lists it, from wherever this one sorts.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from argelander.analysis.time import bjd_tdb_to_hjd
from argelander.classify.confidence import best_type, scores
from argelander.classify.rules.agreement import AgreementRule
from argelander.classify.rules.base import Evidence
from argelander.classify.rules.gaia import target_gaia_types
from argelander.classify.types import PERIODIC_TYPES
from argelander.db.models import Analysis, Proposal, ProposalStatus, Target

# The plots after the phase plots, in the order the page shows them (spec §8); a kind not
# named here follows them, by name.
PLOT_ORDER = ("periodogram", "cmd", "combined", "submission")


@dataclass(frozen=True)
class EvidenceGroup:
    type_code: str
    confidence: float  # this type's score over its Evidence (`confidence.scores`)
    items: tuple[Evidence, ...]


@dataclass(frozen=True)
class FieldChange:
    label: str
    earlier: str
    current: str

    @property
    def changed(self) -> bool:
        return self.earlier != self.current


@dataclass(frozen=True)
class ProposalPage:
    proposal: Proposal
    target: Target
    analysis: Analysis
    epoch_hjd: float | None  # the Proposal's epoch as VSX takes it; None without an epoch
    plots: tuple[tuple[str, str], ...]  # (kind, path relative to PLOTS_DIR), in page order
    evidence: tuple[EvidenceGroup, ...]  # best-scoring type first
    no_agreement: str | None  # the Agreement rule's explanation, when there is no Agreement
    earlier: Proposal | None  # the newest earlier Proposal for the Target
    newest_id: int | None  # the Target's newest Proposal, when it is a later one
    diff: tuple[FieldChange, ...]  # empty without an earlier Proposal
    previous_id: int | None  # the queued Proposal before this one in the queue's order
    next_id: int | None


def _number(digits: str) -> Callable[[Any], str]:
    return lambda value: "—" if value is None else format(value, digits)


def _type(proposal: Proposal) -> str:
    # VSX marks an uncertain type with a trailing colon.
    return proposal.proposed_type + (":" if proposal.uncertain else "")


def _minimum(proposal: Proposal) -> str:
    shown = _number(".2f")(proposal.mag_min)
    amplitude = proposal.min_is_amplitude and proposal.mag_min is not None
    return f"{shown} (amplitude)" if amplitude else shown


# The Proposal fields the diff compares, each as the page displays it.
DIFF_FIELDS: tuple[tuple[str, Callable[[Proposal], str]], ...] = (
    ("Type", _type),
    ("Period (d)", lambda p: _number(".7g")(p.period)),
    ("Epoch (BJD)", lambda p: _number(".5f")(p.epoch_bjd)),
    ("Eclipse or rise (% of period)", lambda p: _number(".1f")(p.eclipse_duration_pct)),
    ("Maximum", lambda p: _number(".2f")(p.mag_max)),
    ("Minimum", _minimum),
    ("Band", lambda p: p.band or "—"),
    ("Confidence", lambda p: _number(".3f")(p.confidence)),
    ("Pipeline Version", lambda p: p.pipeline_version),
)


def diff(earlier: Proposal, current: Proposal) -> tuple[FieldChange, ...]:
    """Every compared field of the two Proposals, as the page shows them."""
    return tuple(FieldChange(label, show(earlier), show(current)) for label, show in DIFF_FIELDS)


def plot_order(plots: dict[str, Any] | None) -> tuple[tuple[str, str], ...]:
    """`analyses.plots` as the page lays it out: phase plots by survey and band, then
    `PLOT_ORDER`, then any other kind by name."""

    def key(kind: str) -> tuple[int, str]:
        if kind.startswith("phase-"):
            return 0, kind
        if kind in PLOT_ORDER:
            return 1 + PLOT_ORDER.index(kind), kind
        return 1 + len(PLOT_ORDER), kind

    return tuple((kind, (plots or {})[kind]) for kind in sorted(plots or {}, key=key))


def evidence_groups(evidence: list[Evidence], best: str | None) -> tuple[EvidenceGroup, ...]:
    """The Evidence by the type it bears on, each in its rules' order: `best` first, the
    type the Proposal's Confidence belongs to, then by score, a tie in first-seen order."""
    scored = scores(evidence)
    ranked = sorted(scored, key=lambda code: (code != best, -scored[code]))
    return tuple(
        EvidenceGroup(code, scored[code], tuple(e for e in evidence if e.type_code == code))
        for code in ranked
    )


def no_agreement_explanation(evidence: list[Evidence], best: str | None) -> str | None:
    """The Agreement rule's explanation for `best`, the type the Proposal's Confidence belongs
    to (`classify.propose.elements`), when the Evidence says there was no Agreement; else None.

    The rule opposes every periodic type without Agreement and none with it.
    """
    agreement = [e for e in evidence if e.rule == AgreementRule.name]
    if not any(e.verdict == "against" and e.type_code in PERIODIC_TYPES for e in agreement):
        return None
    found = next((e for e in agreement if e.type_code == best), agreement[0])
    return found.explanation


async def queue_neighbour(session: AsyncSession, proposal: Proposal, before: bool) -> int | None:
    """The queued Proposal just before (or after) `proposal` in the queue's order."""
    confidence, id_ = Proposal.confidence, Proposal.id
    if before:
        position = or_(
            confidence > proposal.confidence,
            and_(confidence == proposal.confidence, id_ < proposal.id),
        )
        order = (confidence.asc(), id_.desc())
    else:
        position = or_(
            confidence < proposal.confidence,
            and_(confidence == proposal.confidence, id_ > proposal.id),
        )
        order = (confidence.desc(), id_.asc())
    query = (
        select(id_)
        .where(Proposal.status == ProposalStatus.QUEUED, position)
        .order_by(*order)
        .limit(1)
    )
    return await session.scalar(query)


async def proposal_page(session: AsyncSession, proposal_id: int) -> ProposalPage | None:
    """The evidence page for one Proposal; None when there is no such Proposal."""
    proposal = await session.get(Proposal, proposal_id)
    if proposal is None:
        return None
    target = await session.get_one(Target, proposal.target_id)
    analysis = await session.get_one(Analysis, proposal.analysis_id)
    earlier = await session.scalar(
        select(Proposal)
        .where(Proposal.target_id == proposal.target_id, Proposal.id < proposal.id)
        .order_by(Proposal.id.desc())
        .limit(1)
    )
    newest_id = await session.scalar(
        select(func.max(Proposal.id)).where(Proposal.target_id == proposal.target_id)
    )
    evidence = [Evidence(**item) for item in proposal.evidence]
    best = best_type(scores(evidence), target_gaia_types(target))
    epoch = proposal.epoch_bjd
    return ProposalPage(
        proposal=proposal,
        target=target,
        analysis=analysis,
        epoch_hjd=None if epoch is None else float(bjd_tdb_to_hjd(epoch, target.ra, target.dec)),
        plots=plot_order(analysis.plots),
        evidence=evidence_groups(evidence, best),
        no_agreement=no_agreement_explanation(evidence, best),
        earlier=earlier,
        newest_id=newest_id if newest_id != proposal.id else None,
        diff=diff(earlier, proposal) if earlier else (),
        previous_id=await queue_neighbour(session, proposal, before=True),
        next_id=await queue_neighbour(session, proposal, before=False),
    )
