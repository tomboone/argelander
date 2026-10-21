"""SQLAlchemy 2 declarative models for the results database and the job queue (spec §4).

Every table carries `created_at`; IDs are bigint identity. JSONB holds per-survey detail
whose shape varies; anything queried or sorted is a scalar column. Each fixed vocabulary is
a `StrEnum` stored as varchar with a CHECK constraint rather than a Postgres enum type, so
adding a value is a constraint swap in a migration instead of `ALTER TYPE`.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    MetaData,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministic constraint names, so a later migration can drop or alter a constraint by
# name without reading it back from the database.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Survey(StrEnum):
    ZTF = "ztf"
    TESS = "tess"
    GAIA = "gaia"
    ASASSN = "asassn"


class TargetStatus(StrEnum):
    """Where the Target stands, stored for display and kept up to date by the workers
    (spec §4, Target status)."""

    SELECTED = "selected"
    FETCHING = "fetching"
    FETCHED = "fetched"
    ANALYZED = "analyzed"
    PROPOSED = "proposed"
    REVIEWED = "reviewed"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FAILED = "failed"


class LightCurveFileStatus(StrEnum):
    OK = "ok"
    EMPTY = "empty"
    FAILED = "failed"


class JobKind(StrEnum):
    FETCH = "fetch"
    ANALYZE = "analyze"


class JobStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    DONE = "done"
    FAILED = "failed"


class AgreementRelation(StrEnum):
    """How the agreed period relates to the published one; null without Agreement."""

    SAME = "1:1"
    DOUBLE = "2:1"


class ProposalStatus(StrEnum):
    QUEUED = "queued"
    SUPERSEDED = "superseded"
    APPROVED = "approved"
    REJECTED = "rejected"


class ReviewDecision(StrEnum):
    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"


class SubmissionStatus(StrEnum):
    """`needs_changes` sits between `submitted` and a Verdict when a moderator asks for
    changes (spec §9.2)."""

    PREPARED = "prepared"
    SUBMITTED = "submitted"
    NEEDS_CHANGES = "needs_changes"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


def _vocabulary[E: StrEnum](enum: type[E], name: str) -> Enum:
    """Varchar plus a CHECK constraint holding the enum's values (not its member names)."""
    return Enum(
        enum,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=max(len(member.value) for member in enum),
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        int: BigInteger,
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
        list[str]: ARRAY(Text),
    }


class _Row:
    """The columns every table shares."""

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


def _target_fk() -> Mapped[int]:
    return mapped_column(ForeignKey("targets.id"), index=True)


class Slice(_Row, Base):
    """One selection run: a sky region and a magnitude range."""

    __tablename__ = "slices"

    name: Mapped[str]  # e.g. "Dra-13-16"
    constellation: Mapped[str]
    mag_min: Mapped[float]
    mag_max: Mapped[float]
    pipeline_version: Mapped[str]
    n_targets: Mapped[int] = mapped_column(server_default="0")


class Target(_Row, Base):
    """One VSX entry under study, with what VSX and Gaia DR3 say about it."""

    __tablename__ = "targets"

    slice_id: Mapped[int] = mapped_column(ForeignKey("slices.id"), index=True)
    vsx_oid: Mapped[int] = mapped_column(unique=True)
    vsx_name: Mapped[str]
    gaia_source_id: Mapped[int] = mapped_column(unique=True)
    ra: Mapped[float]
    dec: Mapped[float]
    vsx_type: Mapped[str | None]
    vsx_period: Mapped[float | None]
    vsx_mag_max: Mapped[float | None]
    vsx_mag_min: Mapped[float | None]
    vsx_band: Mapped[str | None]
    gaia_g: Mapped[float | None]
    gaia_bp_rp: Mapped[float | None]
    parallax: Mapped[float | None]
    parallax_error: Mapped[float | None]
    gaia_vari_class: Mapped[str | None]
    gaia_vari_class_score: Mapped[float | None]
    gaia_vari_period: Mapped[float | None]
    gaia_vari_detail: Mapped[dict[str, Any] | None]
    # Sources within 20″, nearest first: id, sep, G (spec §5.1).
    neighbours: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
    # Within 120″: the count, the brightest G and the combined G (spec §5.1).
    tess_field: Mapped[dict[str, Any] | None]
    status: Mapped[TargetStatus] = mapped_column(
        _vocabulary(TargetStatus, "status"), server_default=TargetStatus.SELECTED.value
    )


class LightCurveFile(_Row, Base):
    """The Cache index: one row per fetched survey Light Curve set for a Target."""

    __tablename__ = "light_curve_files"

    target_id: Mapped[int] = _target_fk()
    survey: Mapped[Survey] = mapped_column(_vocabulary(Survey, "survey"))
    # Survey-native id(s), e.g. ZTF oids joined by "+". With `path`, null on an `empty` or
    # `failed` row: the survey had nothing for the Target, so there is no id and no file.
    source_key: Mapped[str | None]
    path: Mapped[str | None]
    n_points: Mapped[int] = mapped_column(server_default="0")
    t_min_bjd: Mapped[float | None]
    t_max_bjd: Mapped[float | None]
    bands: Mapped[list[str]] = mapped_column(server_default="{}")
    fetched_at: Mapped[datetime]
    fetcher_version: Mapped[str]
    status: Mapped[LightCurveFileStatus] = mapped_column(
        _vocabulary(LightCurveFileStatus, "status")
    )
    error: Mapped[str | None] = mapped_column(Text)


JOB_ACTIVE = text("status IN ('pending', 'claimed')")
"""The predicate of the partial unique index that keeps one active Job per (kind, Target)."""


class Job(_Row, Base):
    """One unit of Stage work on one Target, claimed with FOR UPDATE SKIP LOCKED (ADR-0002)."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_kind_status_created_at", "kind", "status", "created_at"),
        # At most one pending or claimed Job per kind and Target, so enqueue is idempotent
        # even when two processes enqueue the same Target at once.
        Index(
            "uq_jobs_kind_target_id_active",
            "kind",
            "target_id",
            unique=True,
            postgresql_where=JOB_ACTIVE,
        ),
    )

    kind: Mapped[JobKind] = mapped_column(_vocabulary(JobKind, "kind"))
    target_id: Mapped[int] = _target_fk()
    status: Mapped[JobStatus] = mapped_column(
        _vocabulary(JobStatus, "status"), server_default=JobStatus.PENDING.value
    )
    claimed_at: Mapped[datetime | None]
    attempts: Mapped[int] = mapped_column(server_default="0")
    max_attempts: Mapped[int] = mapped_column(server_default="3")
    last_error: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(server_default="{}")
    finished_at: Mapped[datetime | None]


class Analysis(_Row, Base):
    """One Analysis per Target per Pipeline Version; a same-version rerun overwrites it."""

    __tablename__ = "analyses"
    __table_args__ = (UniqueConstraint("target_id", "pipeline_version"),)

    target_id: Mapped[int] = _target_fk()
    pipeline_version: Mapped[str]
    # Per survey: n points, date range, bands, light_curve_file ids.
    inputs: Mapped[dict[str, Any]]
    # Per band: best period, power, FAP, top-5 peaks.
    periods: Mapped[dict[str, Any]]
    alias_tests: Mapped[dict[str, Any] | None]
    agreement: Mapped[bool]
    agreed_period: Mapped[float | None]
    agreed_sources: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    agreement_relation: Mapped[AgreementRelation | None] = mapped_column(
        _vocabulary(AgreementRelation, "agreement_relation")
    )
    blend: Mapped[dict[str, Any] | None]
    cmd: Mapped[dict[str, Any] | None]  # abs G, BP−RP, distance
    plots: Mapped[dict[str, Any] | None]  # kind → path
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]


class Proposal(_Row, Base):
    """The classifier's candidate VSX revision for one Analysis."""

    __tablename__ = "proposals"

    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id"), index=True)
    target_id: Mapped[int] = _target_fk()
    pipeline_version: Mapped[str]
    proposed_type: Mapped[str]  # a code from the VSX type dropdown
    uncertain: Mapped[bool] = mapped_column(server_default="false")
    period: Mapped[float | None]
    epoch_bjd: Mapped[float | None]
    eclipse_duration_pct: Mapped[Decimal | None] = mapped_column(Numeric)
    mag_max: Mapped[float | None]
    mag_min: Mapped[float | None]
    min_is_amplitude: Mapped[bool] = mapped_column(server_default="false")
    band: Mapped[str | None]  # a VSX passband code
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    # A list of {rule, verdict, weight, explanation}.
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    status: Mapped[ProposalStatus] = mapped_column(
        _vocabulary(ProposalStatus, "status"), server_default=ProposalStatus.QUEUED.value
    )
    superseded_by: Mapped[int | None] = mapped_column(ForeignKey("proposals.id"))


class Review(_Row, Base):
    """Tom's decision on a Proposal."""

    __tablename__ = "reviews"

    proposal_id: Mapped[int] = mapped_column(ForeignKey("proposals.id"), index=True)
    decision: Mapped[ReviewDecision] = mapped_column(_vocabulary(ReviewDecision, "decision"))
    edited: Mapped[dict[str, Any] | None]  # field → new value, when decision is edit
    notes: Mapped[str] = mapped_column(Text)
    reviewed_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Submission(_Row, Base):
    """An approved Proposal prepared for the VSX revision form, and its Verdict."""

    __tablename__ = "submissions"

    proposal_id: Mapped[int] = mapped_column(ForeignKey("proposals.id"), index=True)
    review_id: Mapped[int] = mapped_column(ForeignKey("reviews.id"))
    form_fields: Mapped[dict[str, Any]]
    reference_text: Mapped[str] = mapped_column(Text)
    plot_path: Mapped[str]
    status: Mapped[SubmissionStatus] = mapped_column(
        _vocabulary(SubmissionStatus, "status"), server_default=SubmissionStatus.PREPARED.value
    )
    submitted_at: Mapped[datetime | None]
    verdict_at: Mapped[datetime | None]
    verdict_reason: Mapped[str | None] = mapped_column(Text)
    verdict_tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
