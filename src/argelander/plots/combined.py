"""The combined light curve: every survey on one time axis (spec §8).

VSX asks data-mined submissions for exactly this, with each survey distinguishable, and
forbids attaching plots copied from survey websites. Magnitudes run brightest at the top.
TESS measures relative flux, so it is drawn as magnitudes relative to its own median and
shifted to the median of the magnitude surveys; its legend entry says so.
"""

from collections.abc import Collection, Sequence

import numpy as np
from matplotlib.axes import Axes
from matplotlib.figure import Figure

from argelander.lightcurve import LightCurve
from argelander.plots.style import SURVEY_MARKERS, series_label

TIME_OFFSET = 2400000.0  # days subtracted from BJD_TDB on the axis
FIGSIZE = (8.0, 4.5)  # inches


def draw_combined(
    ax: Axes, curves: Sequence[LightCurve], contaminated: Collection[str] = ()
) -> None:
    """`contaminated` names the surveys the blend checks flagged."""
    shown = [lc for lc in curves if lc.time.size]
    mags = [lc.value for lc in shown if lc.value_kind == "mag"]
    level = float(np.median(np.concatenate(mags))) if mags else 0.0
    for i, lc in enumerate(shown):
        label = series_label(lc.survey, lc.band, lc.survey in contaminated)
        time, value = lc.time, lc.value
        if lc.value_kind == "flux":
            # A faint Target's normalised flux can dip to zero or below, which has no magnitude.
            positive = value > 0
            time = time[positive]
            value = level - 2.5 * np.log10(value[positive] / np.median(value[positive]))
            label += ", Δmag from median"
        ax.scatter(
            time - TIME_OFFSET,
            value,
            s=6,
            marker=SURVEY_MARKERS.get(lc.survey, "o"),
            color=f"C{i % 10}",
            linewidths=0,
            label=label,
        )
    ax.invert_yaxis()
    ax.set_xlabel(f"BJD_TDB − {TIME_OFFSET:.0f}")
    ax.set_ylabel("Magnitude")
    ax.set_title("All surveys")
    if shown:
        ax.legend(loc="best", fontsize="small", markerscale=2)


def combined_figure(curves: Sequence[LightCurve], contaminated: Collection[str] = ()) -> Figure:
    fig = Figure(figsize=FIGSIZE, layout="constrained")
    draw_combined(fig.add_subplot(), curves, contaminated)
    return fig
