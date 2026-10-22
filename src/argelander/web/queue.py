"""The review queue (spec §9 `GET /`): `queued` Proposals, highest Confidence first.

Superseded, approved and rejected Proposals are counted in the page header but never listed.
The filters narrow by proposed type, minimum Confidence and the blend flag; a page holds
`PAGE_SIZE` rows, ordered by Confidence and then id, so paging is stable across ties.

A Proposal is blended when its Analysis has any contaminated survey (spec §6 step 7): one
whose aperture holds a bright enough neighbour, or whose ZTF Zero-point offsets are too large. The
surveys with data are the ones its Analysis read, the `ok` rows the worker gave it.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from argelander.db.models import Analysis, Proposal, ProposalStatus, Target

PAGE_SIZE = 50

# The statuses the header counts, in the order it shows them.
COUNTED = (
    ProposalStatus.QUEUED,
    ProposalStatus.APPROVED,
    ProposalStatus.REJECTED,
    ProposalStatus.SUPERSEDED,
)


class QueueFilters(BaseModel):
    """The queue's query parameters. The filter form sends a blank field as an empty string,
    which means no filter."""

    type: str | None = None
    min_confidence: Decimal | None = Field(None, ge=0, le=1)
    blend: Literal["yes", "no"] | None = None
    page: int = Field(1, ge=1)

    @field_validator("type", "min_confidence", "blend", mode="before")
    @classmethod
    def _blank_is_none(cls, value: Any) -> Any:
        return None if value == "" else value


@dataclass(frozen=True)
class QueueRow:
    proposal_id: int
    target_id: int
    vsx_name: str
    vsx_type: str | None
    vsx_period: float | None
    proposed_type: str
    uncertain: bool
    period: float | None
    confidence: Decimal
    blend_flags: tuple[str, ...]  # the contaminated surveys' flags, deduplicated; empty if none
    surveys: tuple[str, ...]  # the surveys with data, sorted


@dataclass(frozen=True)
class QueuePage:
    rows: tuple[QueueRow, ...]
    total: int  # queued Proposals matching the filters, on every page
    page: int

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // PAGE_SIZE))


def _blended() -> ColumnElement[bool]:
    contaminations = func.jsonb_array_length(Analysis.blend["contaminations"])
    return func.coalesce(contaminations, 0) > 0


def _matching(filters: QueueFilters) -> list[ColumnElement[bool]]:
    """The conditions on Proposal and its Analysis that `filters` sets, queued always."""
    conditions = [Proposal.status == ProposalStatus.QUEUED]
    if filters.type is not None:
        conditions.append(Proposal.proposed_type == filters.type)
    if filters.min_confidence is not None:
        conditions.append(Proposal.confidence >= filters.min_confidence)
    if filters.blend is not None:
        conditions.append(_blended() if filters.blend == "yes" else ~_blended())
    return conditions


def _row(row: Any) -> QueueRow:
    contaminations = (row.blend or {}).get("contaminations", [])
    return QueueRow(
        proposal_id=row.id,
        target_id=row.target_id,
        vsx_name=row.vsx_name,
        vsx_type=row.vsx_type,
        vsx_period=row.vsx_period,
        proposed_type=row.proposed_type,
        uncertain=row.uncertain,
        period=row.period,
        confidence=row.confidence,
        blend_flags=tuple(dict.fromkeys(c["flag"] for c in contaminations)),
        surveys=tuple(sorted(row.inputs)),
    )


async def queue_page(session: AsyncSession, filters: QueueFilters) -> QueuePage:
    """The page of queued Proposals `filters` asks for, and how many match in all."""
    matching = _matching(filters)
    total = await session.scalar(
        select(func.count())
        .select_from(Proposal)
        .join(Analysis, Proposal.analysis_id == Analysis.id)
        .where(*matching)
    )
    query = (
        select(
            Proposal.id,
            Proposal.target_id,
            Target.vsx_name,
            Target.vsx_type,
            Target.vsx_period,
            Proposal.proposed_type,
            Proposal.uncertain,
            Proposal.period,
            Proposal.confidence,
            Analysis.blend,
            Analysis.inputs,
        )
        .join(Target, Proposal.target_id == Target.id)
        .join(Analysis, Proposal.analysis_id == Analysis.id)
        .where(*matching)
        .order_by(Proposal.confidence.desc(), Proposal.id)
        .limit(PAGE_SIZE)
        .offset((filters.page - 1) * PAGE_SIZE)
    )
    rows = (await session.execute(query)).all()
    return QueuePage(tuple(_row(row) for row in rows), total or 0, filters.page)


async def status_counts(session: AsyncSession) -> dict[ProposalStatus, int]:
    """Proposals per status, for the header; a status with none counts 0."""
    rows = await session.execute(select(Proposal.status, func.count()).group_by(Proposal.status))
    counts: dict[ProposalStatus, int] = {status: n for status, n in rows}
    return {status: counts.get(status, 0) for status in COUNTED}


async def proposed_types(session: AsyncSession) -> list[str]:
    """The types queued Proposals propose, for the type filter's choices."""
    query = (
        select(Proposal.proposed_type)
        .where(Proposal.status == ProposalStatus.QUEUED)
        .distinct()
        .order_by(Proposal.proposed_type)
    )
    return list(await session.scalars(query))
