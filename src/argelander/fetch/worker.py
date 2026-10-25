"""fetch-worker: claims fetch Jobs, runs the four fetchers, indexes the Cache, queues analysis.

Spec §3, §5, §5.1, §10. One long-running process polls the `jobs` table. Each pass releases
stale claims, claims up to `fetch_batch_size` fetch Jobs together and runs every survey for
every claimed Target at once; the per-archive semaphores in `ArchiveLimits` keep each archive
to its own limit. ZTF, TESS and ASAS-SN are asked once per Target. Gaia's epoch photometry is
asked once for the whole batch through `GaiaEpochFetcher.fetch_many`, so a single claimed
Job is a batch of one.

Each Target is then recorded in its own transaction: its Light Curves are written to the
Cache, one `light_curve_files` row per survey is upserted (`ok`, `empty`, or `failed` with
the error), and the Target moves to `fetched` with an `analyze` Job queued. A survey that
failed after its retries marks only its own row `failed`. The Job fails, to be claimed again
up to its `max_attempts`, only when every survey failed.

The Cache directory of a (survey, Target) pair is cleared before it is written, whatever the
outcome, and cleared again if a write fails partway, so a `failed` or `empty` row never sits
beside band files. The files are written before the transaction commits, so a commit that
fails leaves them ahead of their rows until the Job's next attempt rewrites both.
"""

import asyncio
import logging
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn, Protocol, Self

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from argelander.config import Settings
from argelander.db import queue
from argelander.db.migrations import wait_for_schema_head
from argelander.db.models import (
    Job,
    JobKind,
    JobStatus,
    LightCurveFile,
    LightCurveFileStatus,
    Survey,
    Target,
    TargetStatus,
)
from argelander.fetch.base import ArchiveLimits, Fetcher, TargetRef, TessField
from argelander.lightcurve import LightCurve, cache_path

logger = logging.getLogger(__name__)

# Recorded on every `light_curve_files` row. Bump it whenever a fetcher changes what it writes
# to the Cache, so the files an older fetcher wrote can be found and fetched again.
FETCHER_VERSION = "1"

# What one survey gave for one Target: its Light Curves (none when it has no data), or the
# error it failed with after its retries.
type Outcome = list[LightCurve] | Exception


class BatchFetcher(Fetcher, Protocol):
    """A `Fetcher` that also answers for many Targets in one request (Gaia, spec §5.1)."""

    async def fetch_many(self, targets: Sequence[TargetRef]) -> dict[int, list[LightCurve]]:
        """Every Target's Light Curves by Gaia source ID."""
        ...


@dataclass(frozen=True)
class Fetchers:
    """The surveys the worker runs: those asked once per Target, and those asked once per batch."""

    per_target: Sequence[Fetcher]
    batched: Sequence[BatchFetcher]

    @classmethod
    def from_settings(cls, settings: Settings, limits: ArchiveLimits) -> Self:
        # Imported here: the TESS fetcher loads lightkurve, which the tests' fakes never need.
        from argelander.fetch.asassn import AsassnFetcher
        from argelander.fetch.gaia_epoch import GaiaEpochFetcher
        from argelander.fetch.tess import TessFetcher
        from argelander.fetch.ztf import ZtfFetcher

        attempts = settings.fetch_max_attempts
        return cls(
            per_target=[
                ZtfFetcher(limits, max_attempts=attempts),
                TessFetcher(limits, max_attempts=attempts),
                AsassnFetcher(limits, max_attempts=attempts),
            ],
            batched=[GaiaEpochFetcher(limits, max_attempts=attempts)],
        )


# Created once in CACHE_DIR on the USB SSD (docs/runbook.md); only the SSD carries it.
CACHE_MARKER = ".argelander-cache"


class CacheNotMounted(Exception):
    """CACHE_DIR has no Cache marker, so the USB SSD is not there, and the dev override is off."""


def check_cache_mount(cache_dir: Path, *, allow_unmounted: bool) -> None:
    """Refuse a CACHE_DIR without the Cache marker file (spec §10).

    In prod the Cache is the USB SSD, mounted with `nofail`; if the mount is missing, the
    directory is an empty folder on the boot disk, and the fetcher would fill it. A mount-point
    test cannot see that from inside a container, where every bind-mount target is a mount
    point, so the guard looks for a marker that only the SSD carries. The dev stack sets
    ALLOW_UNMOUNTED_CACHE=1, where the Cache is an ordinary directory.
    """
    if allow_unmounted or (cache_dir / CACHE_MARKER).is_file():
        return
    raise CacheNotMounted(
        f"CACHE_DIR {cache_dir} has no {CACHE_MARKER} marker, so the USB SSD is not mounted; "
        "mount it, or set ALLOW_UNMOUNTED_CACHE=1 in dev"
    )


async def fetch_targets(
    fetchers: Fetchers, targets: Sequence[TargetRef]
) -> dict[int, dict[str, Outcome]]:
    """Every survey's Outcome for every Target, by Gaia source ID and then survey.

    Every call runs at once; a failure is caught and becomes that survey's Outcome, so one
    survey or Target failing never cancels the others.
    """

    async def one(fetcher: Fetcher, target: TargetRef) -> Outcome:
        try:
            return await fetcher.fetch(target)
        except Exception as error:
            _log_failure(fetcher.survey, [target], error)
            return error

    async def batch(fetcher: BatchFetcher) -> dict[int, Outcome]:
        try:
            found = await fetcher.fetch_many(targets)
        except Exception as error:
            _log_failure(fetcher.survey, targets, error)
            return {target.gaia_source_id: error for target in targets}
        return {target.gaia_source_id: found.get(target.gaia_source_id, []) for target in targets}

    pairs = [(fetcher, target) for fetcher in fetchers.per_target for target in targets]
    singles = asyncio.gather(*(one(fetcher, target) for fetcher, target in pairs))
    batches = asyncio.gather(*(batch(fetcher) for fetcher in fetchers.batched))
    single_outcomes, batch_outcomes = await asyncio.gather(singles, batches)

    outcomes: dict[int, dict[str, Outcome]] = {target.gaia_source_id: {} for target in targets}
    for (fetcher, target), outcome in zip(pairs, single_outcomes, strict=True):
        outcomes[target.gaia_source_id][fetcher.survey] = outcome
    for fetcher, by_source in zip(fetchers.batched, batch_outcomes, strict=True):
        for source_id, outcome in by_source.items():
            outcomes[source_id][fetcher.survey] = outcome
    return outcomes


def target_ref(target: Target) -> TargetRef:
    """What the fetchers need of a stored Target; `tess_field` as `select-slice` stored it."""
    return TargetRef(
        gaia_source_id=target.gaia_source_id,
        ra=target.ra,
        dec=target.dec,
        vsx_name=target.vsx_name,
        tess_field=TessField(**target.tess_field) if target.tess_field is not None else None,
    )


def record(
    session: Session,
    cache_dir: Path,
    job: Job,
    target: Target,
    outcomes: dict[str, Outcome],
) -> None:
    """Write one Target's Outcomes to the Cache and the database, and settle its Job.

    Works inside the caller's transaction and does not commit.
    """
    errors: dict[str, str] = {}
    for survey, outcome in outcomes.items():
        try:
            row = _store(cache_dir, target.gaia_source_id, Survey(survey), outcome)
        except Exception as error:  # a Light Curve the Cache cannot take fails its survey
            _log_failure(survey, [target_ref(target)], error)
            row = _failed_row(Survey(survey), error)
        if row["status"] == LightCurveFileStatus.FAILED:
            errors[survey] = row["error"]
        _upsert(session, target.id, row)

    if outcomes and len(errors) == len(outcomes):
        fail(job, target, "; ".join(f"{survey}: {error}" for survey, error in errors.items()))
        return
    target.status = TargetStatus.FETCHED
    queue.enqueue(session, JobKind.ANALYZE, target.id)
    queue.complete(job)


def fail(job: Job, target: Target, error: str) -> None:
    """`queue.fail` the Job, and fail its Target too once the Job has no attempts left."""
    queue.fail(job, error)
    if job.status == JobStatus.FAILED:
        target.status = TargetStatus.FAILED


async def run_once(
    sessions: sessionmaker[Session], fetchers: Fetchers, cache_dir: Path, *, batch_size: int
) -> int:
    """One pass: release stale claims, claim up to `batch_size` fetch Jobs, fetch and record
    them. The number of Jobs claimed; 0 when the queue had none."""
    with sessions.begin() as session:
        released = queue.release_stale(session)
        if released:
            logger.warning("released %d stale claims", released)
        claimed: list[tuple[int, Target]] = []
        for _ in range(batch_size):
            job = queue.claim(session, JobKind.FETCH)
            if job is None:
                break
            target = session.get_one(Target, job.target_id)
            target.status = TargetStatus.FETCHING
            claimed.append((job.id, target))
    # Committed: the claims are durable, so a crash from here on leaves them for release_stale.
    if not claimed:
        return 0

    outcomes = await fetch_targets(fetchers, [target_ref(target) for _, target in claimed])
    for job_id, target in claimed:
        try:
            with sessions.begin() as session:
                record(
                    session,
                    cache_dir,
                    session.get_one(Job, job_id),
                    session.get_one(Target, target.id),
                    outcomes[target.gaia_source_id],
                )
        except Exception as error:
            # Something outside any one survey broke; the Job is tried again, the others go on.
            logger.exception("fetch Job %d for %s failed", job_id, target.vsx_name)
            with sessions.begin() as session:
                fail(
                    session.get_one(Job, job_id),
                    session.get_one(Target, target.id),
                    _describe(error),
                )
    logger.info("fetched %d Targets", len(claimed))
    return len(claimed)


async def run(
    settings: Settings, sessions: sessionmaker[Session], fetchers: Fetchers | None = None
) -> NoReturn:
    """Poll for fetch Jobs until stopped, sleeping `fetch_poll_interval` whenever none waits."""
    # ArchiveLimits' semaphores belong to the running event loop, so they are built inside it.
    fetchers = fetchers or Fetchers.from_settings(settings, ArchiveLimits.from_settings(settings))
    while True:
        try:
            claimed = await run_once(
                sessions, fetchers, settings.cache_dir, batch_size=settings.fetch_batch_size
            )
        except Exception:
            # Postgres restarting, say. Claims already made wait for release_stale; the
            # service outlives the blip instead of exiting on it.
            logger.exception("fetch pass failed; retrying after the poll interval")
            claimed = 0
        if not claimed:
            await asyncio.sleep(settings.fetch_poll_interval)


def main(settings: Settings, sessions: sessionmaker[Session]) -> None:
    """Check the Cache mount, wait for the schema head, then run the worker; `CacheNotMounted`
    or `SchemaNotAtHead` before anything starts."""
    check_cache_mount(settings.cache_dir, allow_unmounted=settings.allow_unmounted_cache)
    wait_for_schema_head(
        sessions, timeout=settings.schema_wait_timeout, interval=settings.schema_wait_interval
    )
    asyncio.run(run(settings, sessions))


def _store(
    cache_dir: Path, gaia_source_id: int, survey: Survey, outcome: Outcome
) -> dict[str, Any]:
    """Write one survey's Light Curves for one Target to the Cache; its `light_curve_files`
    row, without the Target."""
    directory = cache_dir / survey / str(gaia_source_id)
    shutil.rmtree(directory, ignore_errors=True)
    if isinstance(outcome, Exception):
        return _failed_row(survey, outcome)

    curves = [lc for lc in outcome if lc.time.size]
    bands = [lc.band for lc in curves]
    if any(lc.survey != survey for lc in curves):
        raise ValueError(f"{survey} returned Light Curves of another survey")
    if len(set(bands)) != len(bands):
        raise ValueError(f"{survey} returned two Light Curves for one band: {bands}")
    if not curves:
        return _row(survey, LightCurveFileStatus.EMPTY)

    try:
        for lc in curves:
            lc.to_parquet(cache_path(cache_dir, survey, gaia_source_id, lc.band))
    except Exception:
        # No half-written survey: the `failed` row this becomes must not sit beside band files.
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return _row(
        survey,
        LightCurveFileStatus.OK,
        # ZTF's source_key is already a "+"-joined list of oids per band; this joins the bands'.
        source_key="+".join(dict.fromkeys(lc.source_key for lc in curves)),
        path=str(directory.relative_to(cache_dir)),
        n_points=sum(lc.time.size for lc in curves),
        t_min_bjd=min(float(lc.time[0]) for lc in curves),
        t_max_bjd=max(float(lc.time[-1]) for lc in curves),
        bands=bands,
    )


def _failed_row(survey: Survey, error: Exception) -> dict[str, Any]:
    return _row(survey, LightCurveFileStatus.FAILED, error=_describe(error))


def _describe(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"


def _row(survey: Survey, status: LightCurveFileStatus, **columns: object) -> dict[str, Any]:
    """A full `light_curve_files` row bar `target_id`, so an upsert resets every column."""
    return {
        "survey": survey,
        "status": status,
        "source_key": None,
        "path": None,
        "n_points": 0,
        "t_min_bjd": None,
        "t_max_bjd": None,
        "bands": [],
        "error": None,
        "fetched_at": datetime.now(UTC),
        "fetcher_version": FETCHER_VERSION,
    } | columns


def _upsert(session: Session, target_id: int, row: dict[str, Any]) -> None:
    statement = insert(LightCurveFile).values(target_id=target_id, **row)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[LightCurveFile.target_id, LightCurveFile.survey],
            set_={column: statement.excluded[column] for column in row},
        )
    )


def _log_failure(survey: str, targets: Sequence[TargetRef], error: Exception) -> None:
    logger.warning(
        "%s failed for %s: %r",
        survey,
        ", ".join(target.vsx_name for target in targets),
        error,
        extra={"survey": survey},
    )
