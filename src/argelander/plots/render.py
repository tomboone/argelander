"""Render every plot of one Analysis to its directory (spec §8).

The files are `phase-{survey}-{band}.png` for each band with enough points to search,
`periodogram.png`, `cmd.png`, `combined.png` and `submission.png`. `submission.png` is the
one file the VSX revision form accepts: the chosen band's phase plot above the combined
light curve, under the form's 1 MB limit. Without a period (a `VAR` Proposal with no
Agreement) there are no phase plots, and `submission.png` is the combined light curve alone,
the JD light curve VSX asks for with irregular variables.

Plots are disposable: `render_all` overwrites whatever its directory holds.
"""

import io
from collections.abc import Collection, Sequence
from pathlib import Path

from matplotlib.figure import Figure

from argelander.analysis.clean import sufficient
from argelander.analysis.model import Analysis
from argelander.lightcurve import LightCurve
from argelander.plots.cmd import cmd_figure
from argelander.plots.combined import combined_figure, draw_combined
from argelander.plots.periodogram import periodogram_figure
from argelander.plots.phase import draw_phase, phase_figure

SUBMISSION_MAX_BYTES = 1_000_000  # the revision form's 1 MB upload limit
# Tried in order until the composite fits under the limit.
SUBMISSION_DPIS = (100, 80, 60)
SUBMISSION_FIGSIZE = (8.0, 9.0)  # inches; 800 × 900 pixels at the first DPI
DPI = 100


def plot_dir(plots_dir: Path, gaia_source_id: int, pipeline_version: str) -> Path:
    """Where one Analysis's plots go: `{plots_dir}/{gaia_source_id}/{pipeline_version}`."""
    return plots_dir / str(gaia_source_id) / pipeline_version


def render_all(
    analysis: Analysis,
    curves: Sequence[LightCurve],
    *,
    vsx_type: str,
    period: float | None,
    epoch: float | None,
    out_dir: Path,
    chosen: tuple[str, str] | None = None,
) -> dict[str, Path]:
    """Write every plot to `out_dir` and return each file's kind (its name without `.png`)
    mapped to its path, as `analyses.plots` records them.

    `curves` are the Target's cleaned Light Curves. `vsx_type`, `period` and `epoch`
    (BJD_TDB) are the Proposal's; without an epoch, phase 0 is the first observation.
    `chosen` is the (survey, band) whose phase plot goes into `submission.png`, by default
    the band with the most points.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    contaminated = analysis.blend.surveys if analysis.blend is not None else frozenset()
    figures: dict[str, Figure] = {
        "periodogram": periodogram_figure(
            analysis.periods, period=period, contaminated=contaminated
        ),
        "cmd": cmd_figure(analysis.cmd, contaminated="gaia" in contaminated),
        "combined": combined_figure(curves, contaminated),
    }

    searched = [lc for lc in curves if sufficient(lc)]
    if period is not None and searched:
        phase_zero = epoch if epoch is not None else min(float(lc.time[0]) for lc in searched)
        baseline = max(float(lc.time[-1]) for lc in searched) - min(
            float(lc.time[0]) for lc in searched
        )
        for lc in searched:
            figures[f"phase-{lc.survey}-{lc.band}"] = phase_figure(
                lc,
                period=period,
                epoch=phase_zero,
                vsx_type=vsx_type,
                baseline=baseline,
                contaminated=lc.survey in contaminated,
            )
        submission = submission_figure(
            curves,
            lead=lead_curve(searched, chosen),
            period=period,
            epoch=phase_zero,
            vsx_type=vsx_type,
            baseline=baseline,
            contaminated=contaminated,
        )
    else:
        submission = submission_figure(curves, contaminated=contaminated)

    paths = {}
    for kind, fig in figures.items():
        paths[kind] = out_dir / f"{kind}.png"
        fig.savefig(paths[kind], dpi=DPI)
    paths["submission"] = out_dir / "submission.png"
    paths["submission"].write_bytes(_under_limit(submission))
    return paths


def submission_figure(
    curves: Sequence[LightCurve],
    *,
    lead: LightCurve | None = None,
    period: float = 0.0,
    epoch: float = 0.0,
    vsx_type: str = "VAR",
    baseline: float = 0.0,
    contaminated: Collection[str] = (),
) -> Figure:
    """`lead`'s phase plot above the combined light curve; the combined light curve alone
    without a `lead`. The fold arguments are `draw_phase`'s and only read with a `lead`."""
    fig = Figure(figsize=SUBMISSION_FIGSIZE, layout="constrained")
    if lead is None:
        draw_combined(fig.add_subplot(), curves, contaminated)
        return fig
    top, bottom = fig.subplots(2, 1)
    draw_phase(
        top,
        lead,
        period=period,
        epoch=epoch,
        vsx_type=vsx_type,
        baseline=baseline,
        contaminated=lead.survey in contaminated,
    )
    draw_combined(bottom, curves, contaminated)
    return fig


def lead_curve(curves: Sequence[LightCurve], chosen: tuple[str, str] | None) -> LightCurve:
    """The Light Curve whose phase plot leads `submission.png`: the (survey, band) Tom's
    Review chose, else the one with the most points."""
    if chosen is None:
        return max(curves, key=lambda lc: lc.time.size)
    match = next((lc for lc in curves if (lc.survey, lc.band) == chosen), None)
    if match is None:
        raise ValueError(f"no searchable Light Curve for {chosen}")
    return match


def _under_limit(fig: Figure) -> bytes:
    """The figure as PNG at the highest of `SUBMISSION_DPIS` that fits the form's limit."""
    for dpi in SUBMISSION_DPIS:
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png", dpi=dpi)
        if buffer.tell() < SUBMISSION_MAX_BYTES:
            return buffer.getvalue()
    raise ValueError(f"submission plot exceeds {SUBMISSION_MAX_BYTES} bytes at every DPI")
