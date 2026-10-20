"""Phase plots: one band's Light Curve folded at the Proposal period (spec §8).

Phase 0 is the Proposal epoch, and the phase axis is laid out by type per the VSX FAQ
(`classify.types.phase_range`): −0.25 to 1.25 for eclipsers and ellipsoidals, so the
secondary eclipse sits at 0.5 in the middle, and 0 to 2 for everything else, so a pulsator's
maximum sits at 1.0. More than one cycle is shown, and a binned median over the fold is
drawn on top: it is the curve the magnitude range is read from (spec §7). The epoch itself
is not printed: the form takes it in HJD, and `submission/form.py` owns that conversion.
"""

import math

import numpy as np
from matplotlib.axes import Axes
from matplotlib.figure import Figure

from argelander.classify.types import phase_range
from argelander.lightcurve import LightCurve
from argelander.plots.style import CONTAMINATED, survey_name, value_axis
from argelander.submission.form import PASSBANDS, format_period

# Bins of the binned median per cycle: about `POINTS_PER_BIN` points each, within these
# bounds, so dense TESS data resolves a narrow eclipse that 20 bins would smear out.
MIN_BINS, MAX_BINS = 20, 100
POINTS_PER_BIN = 20
FIGSIZE = (8.0, 4.5)  # inches


def fold(time: np.ndarray, period: float, epoch: float) -> np.ndarray:
    """Phase in [0, 1), with phase 0 at `epoch`."""
    return ((time - epoch) / period) % 1.0


def binned_median(phase: np.ndarray, value: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(bin centres, median value) over one cycle, for the bins that hold any points."""
    n_bins = int(np.clip(phase.size // POINTS_PER_BIN, MIN_BINS, MAX_BINS))
    bins = np.minimum((phase * n_bins).astype(np.intp), n_bins - 1)
    occupied = np.unique(bins)
    medians = np.array([np.median(value[bins == b]) for b in occupied])
    return (occupied + 0.5) / n_bins, medians


def title(lc: LightCurve, period: float, baseline: float, contaminated: bool) -> str:
    """Survey, the band's VSX passband and the period as they go into the revision form."""
    band = PASSBANDS.get((lc.survey, lc.band), lc.band)
    text = f"{survey_name(lc.survey)}, passband {band}, P = {format_period(period, baseline)} d"
    return f"{text} ({CONTAMINATED})" if contaminated else text


def draw_phase(
    ax: Axes,
    lc: LightCurve,
    *,
    period: float,
    epoch: float,
    vsx_type: str,
    baseline: float,
    contaminated: bool,
) -> None:
    """Fold `lc` onto `ax`. `baseline` sets the period's precision in the title: the span of
    every survey that determined the period, not only this band."""
    low, high = phase_range(vsx_type)
    phase = fold(lc.time, period, epoch)
    centres, medians = binned_median(phase, lc.value)
    cycles = range(math.floor(low), math.ceil(high))
    for k in cycles:
        shown = (phase + k >= low) & (phase + k <= high)
        ax.scatter(phase[shown] + k, lc.value[shown], s=4, color="0.35", linewidths=0)
    line_x = np.concatenate([centres + k for k in cycles])
    line_y = np.tile(medians, len(cycles))
    kept = (line_x >= low) & (line_x <= high)
    ax.plot(line_x[kept], line_y[kept], color="tab:red", linewidth=1.5, label="binned median")
    ax.set_xlim(low, high)
    ax.set_xlabel(f"Phase ({vsx_type})")
    value_axis(ax, lc)
    ax.set_title(title(lc, period, baseline, contaminated))


def phase_figure(
    lc: LightCurve,
    *,
    period: float,
    epoch: float,
    vsx_type: str,
    baseline: float,
    contaminated: bool = False,
) -> Figure:
    fig = Figure(figsize=FIGSIZE, layout="constrained")
    draw_phase(
        fig.add_subplot(),
        lc,
        period=period,
        epoch=epoch,
        vsx_type=vsx_type,
        baseline=baseline,
        contaminated=contaminated,
    )
    return fig
