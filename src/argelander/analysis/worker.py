"""analyze-worker: claims analyze Jobs, runs the analysis chain, writes the `analyses` row.

Spec §3, §4, §6. One long-running process polls the `jobs` table and takes one Job a pass:
the work is CPU-bound and runs inline. Each pass releases stale claims, then claims an
analyze Job and reads its Target and `ok` Light Curve files in the claim's transaction, so the
claim is durable before the work starts. The Light Curves are loaded from the Cache and
analysed outside any transaction (`pipeline.analyze`, steps 3–8), with the Gaia record
`select-slice` stored on the Target.

The result is written in one transaction (step 9): the `analyses` row upserted on
`(target_id, pipeline_version)`, so a rerun at the same Pipeline Version overwrites the row
and a new version adds one; the Target moved to `analyzed`; the hand-off to the classifier
and the plots; the Job completed. A Target without an `ok` Light Curve, or any error on the
way, fails the Job through `queue.fail`, which returns it to `pending` until it has used its
`max_attempts`, and then fails the Target too.
"""

import logging
import math
import time
from collections.abc import Sequence
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

import numpy as np
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from argelander.analysis.model import AliasTest, Analysis, PeriodogramResult
from argelander.analysis.pipeline import analyze
from argelander.config import Settings
from argelander.db import queue
from argelander.db.models import (
    AgreementRelation,
    Job,
    JobKind,
    LightCurveFile,
    LightCurveFileStatus,
    Target,
    TargetStatus,
)
from argelander.db.models import Analysis as AnalysisRow
from argelander.fetch.worker import check_cache_mount, fail
from argelander.lightcurve import LightCurve, cache_path
from argelander.selector import gaia_record

logger = logging.getLogger(__name__)


class NoLightCurves(Exception):
    """The Target has no `ok` Light Curve file to analyse."""


def load_curves(
    cache_dir: Path, target: Target, files: Sequence[LightCurveFile]
) -> list[LightCurve]:
    """Every band of the Target's `ok` Light Curve files, read from the Cache (step 1)."""
    if not files:
        raise NoLightCurves(f"{target.vsx_name} has no ok Light Curves to analyze")
    return [
        LightCurve.from_parquet(cache_path(cache_dir, file.survey, target.gaia_source_id, band))
        for file in files
        for band in file.bands
    ]


def record(
    session: Session,
    job: Job,
    target: Target,
    analysis: Analysis,
    cleaned: list[LightCurve],
    files: Sequence[LightCurveFile],
    started_at: datetime,
) -> None:
    """Write one Analysis, move its Target to `analyzed`, hand off and complete the Job.

    Works inside the caller's transaction and does not commit.
    """
    columns = analysis_columns(analysis, files) | {
        "started_at": started_at,
        "finished_at": datetime.now(UTC),
        "plots": None,  # a same-version rerun redraws them in the hand-off
    }
    statement = insert(AnalysisRow).values(
        target_id=analysis.target_id, pipeline_version=analysis.pipeline_version, **columns
    )
    row = session.scalars(
        statement.on_conflict_do_update(
            index_elements=[AnalysisRow.target_id, AnalysisRow.pipeline_version],
            set_={column: statement.excluded[column] for column in columns},
        ).returning(AnalysisRow)
    ).one()
    target.status = TargetStatus.ANALYZED
    hand_off(session, row, analysis, cleaned)
    queue.complete(job)


def hand_off(
    session: Session, row: AnalysisRow, analysis: Analysis, cleaned: list[LightCurve]
) -> None:
    """Spec §6 step 9's hand-off, in the Analysis's transaction: `classify.propose()` makes
    the Proposal and `plots.render_all()` draws its evidence into `row.plots`.

    NEU-1721 fills this in; until then an analysed Target waits at `analyzed`.
    """


def analysis_columns(analysis: Analysis, files: Sequence[LightCurveFile]) -> dict[str, Any]:
    """The `analyses` columns an Analysis fills, bar its key and timestamps (spec §4).

    The JSONB columns nest by survey and then band. Periodogram arrays are left out: a
    periodogram is redrawn from the Light Curves, not stored.
    """
    agreement = analysis.agreement
    return {
        "inputs": {
            file.survey.value: {
                "light_curve_file_id": file.id,
                "n_points": file.n_points,
                "t_min_bjd": file.t_min_bjd,
                "t_max_bjd": file.t_max_bjd,
                "bands": file.bands,
            }
            for file in files
        },
        "periods": _periods(analysis.periods),
        "alias_tests": _alias_tests(analysis.alias_tests),
        "agreement": agreement is not None,
        "agreed_period": agreement.agreed_period if agreement else None,
        "agreed_sources": list(agreement.agreed_sources) if agreement else None,
        "agreement_relation": AgreementRelation(agreement.relation) if agreement else None,
        "agreement_tolerance": agreement.tolerance if agreement else None,
        "blend": _json(analysis.blend),
        "cmd": _json(analysis.cmd),
    }


def run_once(sessions: sessionmaker[Session], cache_dir: Path) -> bool:
    """One pass: release stale claims, claim one analyze Job, analyse its Target and record
    it. False when the queue had none."""
    with sessions.begin() as session:
        released = queue.release_stale(session)
        if released:
            logger.warning("released %d stale claims", released)
        job = queue.claim(session, JobKind.ANALYZE)
        if job is None:
            return False
        job_id = job.id
        target = session.get_one(Target, job.target_id)
        files = session.scalars(
            select(LightCurveFile)
            .where(
                LightCurveFile.target_id == target.id,
                LightCurveFile.status == LightCurveFileStatus.OK,
            )
            .order_by(LightCurveFile.survey)
        ).all()
    # Committed: the claim is durable, so a crash from here on leaves it for release_stale.

    try:
        started_at = datetime.now(UTC)
        analysis, cleaned = analyze(
            load_curves(cache_dir, target, files), gaia_record(target), target_id=target.id
        )
        with sessions.begin() as session:
            record(
                session,
                session.get_one(Job, job_id),
                session.get_one(Target, target.id),
                analysis,
                cleaned,
                files,
                started_at,
            )
    except Exception as error:
        logger.exception("analyze Job %d for %s failed", job_id, target.vsx_name)
        with sessions.begin() as session:
            fail(
                session.get_one(Job, job_id),
                session.get_one(Target, target.id),
                f"{type(error).__name__}: {error}",
            )
        return True
    logger.info("analyzed %s", target.vsx_name)
    return True


def run(settings: Settings, sessions: sessionmaker[Session]) -> NoReturn:
    """Poll for analyze Jobs until stopped, sleeping `analyze_poll_interval` whenever none
    waits."""
    while True:
        try:
            claimed = run_once(sessions, settings.cache_dir)
        except Exception:
            # Postgres restarting, say. A claim already made waits for release_stale; the
            # service outlives the blip instead of exiting on it.
            logger.exception("analyze pass failed; retrying after the poll interval")
            claimed = False
        if not claimed:
            time.sleep(settings.analyze_poll_interval)


def main(settings: Settings, sessions: sessionmaker[Session]) -> None:
    """Check the Cache mount, then run the worker; `CacheNotMounted` before anything starts.

    On an unmounted Cache every Target would find its files missing and fail.
    """
    check_cache_mount(settings.cache_dir, allow_unmounted=settings.allow_unmounted_cache)
    run(settings, sessions)


def _periods(results: Sequence[PeriodogramResult]) -> dict[str, Any]:
    """survey → band → method → that search's five peaks, strongest (the best) first."""
    nested: dict[str, Any] = {}
    for result in results:
        nested.setdefault(result.survey, {}).setdefault(result.band, {})[result.method] = {
            "n_points": result.n_points,
            "insufficient": result.insufficient,
            "peaks": _json(result.peaks),
            "gaia": _json(result.gaia),
        }
    return nested


def _alias_tests(tests: Sequence[AliasTest]) -> dict[str, Any]:
    """survey → band → the band's baseline and every hypothesis it folded, best first."""
    nested: dict[str, Any] = {}
    for test in tests:
        nested.setdefault(test.survey, {})[test.band] = {
            "baseline": test.baseline,
            "ranking": _json(test.ranking),
        }
    return nested


def _json(value: Any) -> Any:
    """`value` as JSONB holds it: dataclasses as dicts, tuples as lists, NumPy scalars as
    Python ones, and NaN or infinity, which JSON has no token for, as null."""
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json(item) for item in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value
