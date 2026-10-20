"""The colour-magnitude plot: the Target over a Gaia DR3 background (spec §8).

The background is a random subsample of Gaia DR3 sources with precise parallaxes, committed
under `data/` with the query that drew it, so rendering never calls the archive.
"""

from functools import cache
from importlib.resources import files

import numpy as np
from matplotlib.figure import Figure

from argelander.analysis.model import CmdPosition
from argelander.plots.style import CONTAMINATED

BACKGROUND = "gaia_dr3_cmd_background.csv"
REGION_NAMES = {
    "main_sequence": "main sequence",
    "giant_branch": "giant branch",
    "instability_strip": "instability strip",
    "white_dwarf": "white dwarf",
    "unknown": "unknown",
}
FIGSIZE = (5.5, 6.0)  # inches


@cache
def background() -> np.ndarray:
    """(N, 2) array of BP−RP and absolute G."""
    with (files("argelander.plots") / "data" / BACKGROUND).open() as f:
        return np.loadtxt(f, delimiter=",", comments="#")


def cmd_figure(position: CmdPosition | None, *, contaminated: bool = False) -> Figure:
    """`contaminated` when the blend checks flagged Gaia, whose colour may then be blended."""
    fig = Figure(figsize=FIGSIZE, layout="constrained")
    ax = fig.add_subplot()
    stars = background()
    ax.scatter(stars[:, 0], stars[:, 1], s=2, color="0.7", linewidths=0, label="Gaia DR3")
    region = position.region if position is not None else "unknown"
    if position is not None and position.bp_rp is not None and position.abs_g is not None:
        ax.scatter(
            position.bp_rp,
            position.abs_g,
            s=180,
            marker="*",
            color="tab:red",
            edgecolors="black",
            zorder=3,
            label="Target",
        )
        ax.legend(loc="upper right")
    else:
        ax.text(
            0.5,
            0.97,
            "No significant parallax or colour: the Target cannot be placed",
            transform=ax.transAxes,
            ha="center",
            va="top",
            fontsize="small",
        )
    ax.invert_yaxis()
    ax.set_xlabel("BP − RP (mag)")
    ax.set_ylabel("Absolute G (mag)")
    title = f"Colour-magnitude position: {REGION_NAMES[region]}"
    ax.set_title(f"{title} (Gaia {CONTAMINATED})" if contaminated else title)
    return fig
