"""`argelander classify --replot`: redraw stored Analyses' plots without analysing again (spec §8).

Plots are disposable. Each Analysis at the current Pipeline Version that has a Proposal is
drawn again at its newest Proposal's type, period and epoch, and `analyses.plots` is rewritten.
Nothing else is recomputed or written: no rule runs, and the Analysis and Proposal rows stand.

Two things are rebuilt because the database deliberately does not store them. The Light
Curves come from the Cache, cleaned and then re-aligned at the zero points stored in
`analyses.blend`. The periodogram arrays are searched again on the cleaned curves before
re-alignment, as the pipeline searched them (§4: "a plot redraws them from the Light
Curves"), so the BLS search, the analyzer's slow step, is the one cost a replot shares with
an analysis.
"""

from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from argelander import PIPELINE_VERSION
from argelander.analysis.blend import realign
from argelander.analysis.clean import clean
from argelander.analysis.model import (
    Analysis,
    Blend,
    CmdPosition,
    Contamination,
    ZeroPointOffsets,
)
from argelander.analysis.periodogram import box_least_squares, lomb_scargle
from argelander.analysis.worker import load_curves
from argelander.catalog.gaia import Neighbour
from argelander.classify.propose import draw
from argelander.db.models import Analysis as AnalysisRow
from argelander.db.models import LightCurveFile, Proposal, Target
from argelander.selector import gaia_record


class UnknownTarget(Exception):
    """No Target has the VSX name `--target` gave."""


def replot_all(
    sessions: sessionmaker[Session],
    cache_dir: Path,
    plots_dir: Path,
    *,
    vsx_name: str | None = None,
    pipeline_version: str = PIPELINE_VERSION,
) -> int:
    """Redraw every Analysis at `pipeline_version` that has a Proposal, or only `vsx_name`'s;
    the number redrawn. One transaction each, so a failure keeps the ones before it."""
    with sessions() as session:
        query = (
            select(AnalysisRow.id)
            .join(Target, Target.id == AnalysisRow.target_id)
            .where(
                AnalysisRow.pipeline_version == pipeline_version,
                select(Proposal.id).where(Proposal.analysis_id == AnalysisRow.id).exists(),
            )
            .order_by(AnalysisRow.id)
        )
        if vsx_name is not None:
            if session.scalar(select(Target.id).where(Target.vsx_name == vsx_name)) is None:
                raise UnknownTarget(f"no Target is named {vsx_name!r}")
            query = query.where(Target.vsx_name == vsx_name)
        ids = list(session.scalars(query))
    for analysis_id in ids:
        with sessions.begin() as session:
            replot(session, session.get_one(AnalysisRow, analysis_id), cache_dir, plots_dir)
    return len(ids)


def replot(session: Session, row: AnalysisRow, cache_dir: Path, plots_dir: Path) -> None:
    """Redraw one Analysis at its newest Proposal and rewrite `row.plots`; does not commit."""
    proposal = session.scalars(
        select(Proposal).where(Proposal.analysis_id == row.id).order_by(Proposal.id.desc())
    ).first()
    if proposal is None:
        raise ValueError(f"Analysis {row.id} has no Proposal to draw")
    target = session.get_one(Target, row.target_id)
    ids = [survey["light_curve_file_id"] for survey in row.inputs.values()]
    files = session.scalars(
        select(LightCurveFile).where(LightCurveFile.id.in_(ids)).order_by(LightCurveFile.survey)
    ).all()
    cleaned = [clean(lc) for lc in load_curves(cache_dir, target, files)]

    record = gaia_record(target)
    gaia_period = record.vari_period if record is not None else None
    blend = stored_blend(row.blend)
    analysis = Analysis(
        target_id=target.id,
        pipeline_version=row.pipeline_version,
        blend=blend,
        cmd=CmdPosition(**row.cmd) if row.cmd is not None else None,
    )
    for lc in cleaned:
        analysis.periods.extend((lomb_scargle(lc, gaia_period), box_least_squares(lc, gaia_period)))
    if blend is not None:
        cleaned = [realign(lc, blend.zero_points) for lc in cleaned]
    draw(row, proposal, analysis, cleaned, target.gaia_source_id, plots_dir)


def stored_blend(column: dict[str, Any] | None) -> Blend | None:
    """The `Blend` the analyze worker wrote into `analyses.blend`, rebuilt."""
    if column is None:
        return None
    return Blend(
        contaminations=tuple(
            Contamination(
                survey=c["survey"],
                flag=c["flag"],
                neighbours=tuple(Neighbour(**n) for n in c["neighbours"]),
                flux_fraction=c["flux_fraction"],
                remark=c["remark"],
            )
            for c in column["contaminations"]
        ),
        zero_points=tuple(
            ZeroPointOffsets(
                band=z["band"],
                period=z["period"],
                bins=z["bins"],
                reference_oid=z["reference_oid"],
                offsets=z["offsets"],
                realignment=z["realignment"],
                unmeasured=tuple(z["unmeasured"]),
                suspect=z["suspect"],
            )
            for z in column["zero_points"]
        ),
    )
