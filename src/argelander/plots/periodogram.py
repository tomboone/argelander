"""The periodogram plot: every band's period search on one period axis (spec §8).

One panel per band and method, with a solid line at the Proposal period, dashed lines at
each of its aliases the alias test tried for that survey, and a dotted line at Gaia's
published period, which is always scored and plotted (spec §6 step 4).
"""

from collections.abc import Collection, Sequence

from matplotlib.artist import Artist
from matplotlib.figure import Figure

from argelander.analysis.aliases import aliases_of
from argelander.analysis.model import PeriodogramResult
from argelander.plots.style import series_label

METHOD_NAMES = {"lomb_scargle": "Lomb-Scargle", "bls": "BLS"}
WIDTH = 8.0  # inches
PANEL_HEIGHT = 1.8


def periodogram_figure(
    results: Sequence[PeriodogramResult],
    *,
    period: float | None,
    contaminated: Collection[str] = (),
) -> Figure:
    """`contaminated` names the surveys the blend checks flagged."""
    shown = [r for r in results if not r.insufficient]
    fig = Figure(figsize=(WIDTH, PANEL_HEIGHT * max(len(shown), 1) + 0.6), layout="constrained")
    if not shown:
        ax = fig.add_subplot()
        ax.text(0.5, 0.5, "No band had enough points to search", ha="center", va="center")
        ax.set_axis_off()
        return fig
    axes = fig.subplots(len(shown), 1, sharex=True, squeeze=False)[:, 0]
    for ax, result in zip(axes, shown, strict=True):
        ax.plot(1.0 / result.frequency, result.power, color="0.2", linewidth=0.6)
        if period is not None:
            ax.axvline(period, color="tab:red", linewidth=1.2, label="Proposal period")
            for i, (_, alias) in enumerate(aliases_of(period, result.survey)):
                label = "tested alias" if i == 0 else None
                ax.axvline(alias, color="tab:orange", linestyle="--", linewidth=0.8, label=label)
        if result.gaia is not None:
            ax.axvline(
                result.gaia.period, color="tab:blue", linestyle=":", linewidth=1.2, label="Gaia"
            )
        label = series_label(result.survey, result.band, result.survey in contaminated)
        ax.set_title(f"{label}, {METHOD_NAMES[result.method]}", fontsize="medium")
        ax.set_ylabel("Power")
    axes[0].set_xscale("log")
    axes[-1].set_xlabel("Period (d)")
    handles: dict[str, Artist] = {}
    for ax in axes:
        for handle, label in zip(*ax.get_legend_handles_labels(), strict=True):
            handles.setdefault(label, handle)
    if handles:
        fig.legend(handles.values(), handles.keys(), loc="outside upper right", ncols=3)
    return fig
