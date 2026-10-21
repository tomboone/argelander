"""The target selector Stage: a constellation and a magnitude range become a Slice of Targets
with their Gaia DR3 data, each with a fetch Job queued (spec §3, §4; handover: Target selector).

`gather` does the archive work: VizieR's copy of VSX for the entries (`catalog/vsx.py`), then
the Gaia catalog join for all their source IDs at once (`catalog/gaia.py`). `store` writes the
result inside the caller's transaction:

- one `slices` row per run, as §4 has it;
- the Targets upserted on `vsx_oid`: a new entry is inserted with status `selected`, and one
  already in the table has its Gaia columns refreshed but keeps its Slice, its VSX columns and
  its status;
- a fetch Job for every Target still `selected`. `queue.enqueue` skips a Target whose fetch
  Job is still pending or claimed, and a Target past `selected` has been fetched already, so
  running the command twice queues nothing new.

VSX can list one Gaia source under two OIDs, which `targets.gaia_source_id` (unique) cannot
hold. The Target already stored for the source keeps it; otherwise the lower OID does. The
entries dropped are counted in the summary.
"""

import itertools
import math
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from argelander import PIPELINE_VERSION
from argelander.catalog.gaia import GaiaRecord, Neighbour, fetch_catalog, run_upload_query
from argelander.catalog.vsx import VsxEntry, fetch_vsx_slice, run_vizier_query
from argelander.db import queue
from argelander.db.models import JobKind, Slice, Target, TargetStatus
from argelander.fetch.base import TessField

# The `targets` columns filled from a GaiaRecord, which a rerun refreshes.
GAIA_COLUMNS = (
    "gaia_g",
    "gaia_bp_rp",
    "parallax",
    "parallax_error",
    "gaia_vari_class",
    "gaia_vari_class_score",
    "gaia_vari_period",
    "gaia_vari_detail",
    "neighbours",
    "tess_field",
)
# Rows per INSERT, well inside Postgres' 65,535 bind parameters at ~20 columns a row.
BATCH_SIZE = 1_000


@dataclass(frozen=True)
class SliceSummary:
    """What one `select-slice` run selected, for the command to print."""

    slice_id: int
    name: str
    # Targets this run selected, including ones an earlier Slice holds: a Target keeps the
    # Slice that first selected it, so this can exceed the Targets whose slice_id is this one.
    n_targets: int
    n_new: int  # Targets not in the table before this run
    n_with_gaia: int  # Targets the Gaia catalog join returned a record for
    n_gaia_vari_class: int  # of those, the ones Gaia's classifier gave a vari_* class
    n_gaia_period: int  # and the ones with a period in one of Gaia's vari_* tables
    n_enqueued: int  # fetch Jobs queued by this run
    duplicate_gaia_ids: int  # VSX entries dropped for sharing a Gaia source with another OID
    # Targets per whole magnitude of VSX's magnitude at maximum: {13: n} counts 13 ≤ mag < 14.
    mag_counts: dict[int, int]

    def lines(self) -> list[str]:
        lines = [
            f"Slice {self.name} (id {self.slice_id}): {self.n_targets} Targets, {self.n_new} new",
            f"  with a Gaia DR3 record: {self.n_with_gaia}; "
            f"with a Gaia vari_* class: {self.n_gaia_vari_class}; "
            f"with a Gaia period: {self.n_gaia_period}",
            *(f"  {mag}–{mag + 1} mag: {n}" for mag, n in sorted(self.mag_counts.items())),
            f"  fetch Jobs queued: {self.n_enqueued}",
        ]
        if self.duplicate_gaia_ids:
            lines.append(
                f"  skipped {self.duplicate_gaia_ids} VSX entries sharing a Gaia source "
                "with another OID"
            )
        return lines


def default_name(constellation: str, mag_min: float, mag_max: float) -> str:
    """A Slice's name when none is given, e.g. "Dra-13-16"."""
    return f"{constellation}-{mag_min:g}-{mag_max:g}"


async def gather(
    constellation: str,
    mag_min: float,
    mag_max: float,
    *,
    max_attempts: int,
    vsx_query: Callable[[dict[str, str]], Any] = run_vizier_query,
    gaia_query: Callable[[str, list[int]], Any] = run_upload_query,
) -> tuple[list[VsxEntry], dict[int, GaiaRecord]]:
    """The Slice's VSX entries, and the Gaia DR3 record of each source Gaia has."""
    entries = await fetch_vsx_slice(
        constellation, mag_min, mag_max, max_attempts=max_attempts, query=vsx_query
    )
    records = await fetch_catalog(
        [entry.gaia_source_id for entry in entries], max_attempts=max_attempts, query=gaia_query
    )
    return entries, records


def store(
    session: Session,
    *,
    name: str,
    constellation: str,
    mag_min: float,
    mag_max: float,
    entries: list[VsxEntry],
    records: dict[int, GaiaRecord],
) -> SliceSummary:
    """Write one Slice, upsert its Targets and queue their fetch Jobs; does not commit."""
    # gaia_source_id → vsx_oid of the Targets already stored under this run's OIDs or sources.
    stored: dict[int, int] = {}
    for batch in itertools.batched(entries, BATCH_SIZE):
        stored.update(
            session.execute(
                select(Target.gaia_source_id, Target.vsx_oid).where(
                    or_(
                        Target.vsx_oid.in_([entry.oid for entry in batch]),
                        Target.gaia_source_id.in_([entry.gaia_source_id for entry in batch]),
                    )
                )
            ).all()
        )
    # One entry per source: the stored Target's OID when there is one, else the lowest. A
    # source an earlier run stored under an OID this run lacks stays with that Target.
    kept: dict[int, VsxEntry] = {}
    for entry in sorted(
        entries, key=lambda e: (stored.get(e.gaia_source_id, e.oid) != e.oid, e.oid)
    ):
        kept.setdefault(entry.gaia_source_id, entry)
    targets = sorted(
        (e for e in kept.values() if stored.get(e.gaia_source_id, e.oid) == e.oid),
        key=lambda e: e.oid,
    )

    slice_ = Slice(
        name=name,
        constellation=constellation,
        mag_min=mag_min,
        mag_max=mag_max,
        pipeline_version=PIPELINE_VERSION,
        n_targets=len(targets),
    )
    session.add(slice_)
    session.flush()

    rows = [
        _vsx_columns(entry) | gaia_columns(records.get(entry.gaia_source_id)) for entry in targets
    ]
    selected = []
    for batch in itertools.batched(rows, BATCH_SIZE):
        statement = insert(Target).values(
            [row | {"slice_id": slice_.id, "status": TargetStatus.SELECTED} for row in batch]
        )
        statement = statement.on_conflict_do_update(
            index_elements=[Target.vsx_oid],
            set_={column: statement.excluded[column] for column in GAIA_COLUMNS},
        ).returning(Target.id, Target.status)
        selected += [
            target_id
            for target_id, status in session.execute(statement)
            if status == TargetStatus.SELECTED
        ]
    enqueued = [queue.enqueue(session, JobKind.FETCH, target_id) for target_id in selected]

    with_gaia = [records[e.gaia_source_id] for e in targets if e.gaia_source_id in records]
    return SliceSummary(
        slice_id=slice_.id,
        name=name,
        n_targets=len(targets),
        n_new=len({entry.oid for entry in targets} - set(stored.values())),
        n_with_gaia=len(with_gaia),
        n_gaia_vari_class=sum(record.vari_class is not None for record in with_gaia),
        n_gaia_period=sum(record.vari_period is not None for record in with_gaia),
        n_enqueued=sum(job is not None for job in enqueued),
        duplicate_gaia_ids=len(entries) - len(targets),
        mag_counts=_mag_counts(targets, mag_min, mag_max),
    )


def gaia_columns(record: GaiaRecord | None) -> dict[str, Any]:
    """The `GAIA_COLUMNS` values for a Target, all empty when Gaia has no record of it.

    `gaia_vari_detail` holds the rest of the record by its field names: the vari_* detail,
    and RUWE and the 2MASS designation, which §4 has no column for.
    """
    if record is None:
        return dict.fromkeys(GAIA_COLUMNS) | {"neighbours": []}
    return {
        "gaia_g": record.g,
        "gaia_bp_rp": record.bp_rp,
        "parallax": record.parallax,
        "parallax_error": record.parallax_error,
        "gaia_vari_class": record.vari_class,
        "gaia_vari_class_score": record.vari_class_score,
        "gaia_vari_period": record.vari_period,
        "gaia_vari_detail": {
            "in_vari_summary": record.in_vari_summary,
            "vari_g_trimmed_range": record.vari_g_trimmed_range,
            "vari_subclass": record.vari_subclass,
            "vari_periods": record.vari_periods,
            "ruwe": record.ruwe,
            "cross_ids": record.cross_ids,
        },
        "neighbours": [asdict(neighbour) for neighbour in record.neighbours],
        "tess_field": asdict(record.tess_field),
    }


def gaia_record(target: Target) -> GaiaRecord | None:
    """The GaiaRecord `gaia_columns` stored on `target`, rebuilt without asking Gaia again;
    None when Gaia had no record of it.

    Gaia's own position is not stored, so the record carries the Target's, which is VSX's;
    the analyzer never reads it.
    """
    detail = target.gaia_vari_detail
    if detail is None or target.tess_field is None:
        return None
    return GaiaRecord(
        source_id=target.gaia_source_id,
        ra=target.ra,
        dec=target.dec,
        g=target.gaia_g,
        bp_rp=target.gaia_bp_rp,
        parallax=target.parallax,
        parallax_error=target.parallax_error,
        ruwe=detail["ruwe"],
        in_vari_summary=detail["in_vari_summary"],
        vari_g_trimmed_range=detail["vari_g_trimmed_range"],
        vari_class=target.gaia_vari_class,
        vari_class_score=target.gaia_vari_class_score,
        vari_subclass=detail["vari_subclass"],
        vari_periods=detail["vari_periods"],
        neighbours=tuple(Neighbour(**neighbour) for neighbour in target.neighbours),
        tess_field=TessField(**target.tess_field),
        cross_ids=detail["cross_ids"],
    )


def _vsx_columns(entry: VsxEntry) -> dict[str, Any]:
    return {
        "vsx_oid": entry.oid,
        "vsx_name": entry.name,
        "gaia_source_id": entry.gaia_source_id,
        "ra": entry.ra,
        "dec": entry.dec,
        "vsx_type": entry.type,
        "vsx_period": entry.period,
        "vsx_mag_max": entry.mag_max,
        "vsx_mag_min": entry.mag_min,
        "vsx_mag_min_is_amplitude": entry.mag_min_is_amplitude,
        "vsx_band": entry.band,
    }


def _mag_counts(entries: list[VsxEntry], mag_min: float, mag_max: float) -> dict[int, int]:
    # The Slice's range is inclusive, so an entry at exactly a whole mag_max joins the bin
    # below it, unless that would put it below mag_min (a range such as 13..13).
    top = max(math.ceil(mag_max) - 1, math.floor(mag_min))
    return dict(
        Counter(
            min(math.floor(entry.mag_max), top) for entry in entries if entry.mag_max is not None
        )
    )
