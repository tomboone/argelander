"""What every plot shares: how a series is named, marked and oriented.

Figures are built on `matplotlib.figure.Figure` directly, never through pyplot, so rendering
needs no display or GUI backend and a long-running worker holds no global figure state.
"""

from matplotlib.axes import Axes

from argelander.analysis.blend import SURVEY_NAMES
from argelander.lightcurve import LightCurve

# One marker per survey, as VSX asks of combined plots; colours separate the bands.
SURVEY_MARKERS = {"ztf": "o", "tess": ".", "gaia": "s", "asassn": "^"}
CONTAMINATED = "contaminated"


def survey_name(survey: str) -> str:
    return SURVEY_NAMES.get(survey, survey)


def series_label(survey: str, band: str, contaminated: bool) -> str:
    """`ZTF g`, or `ZTF g (contaminated)` when the blend checks flagged the survey."""
    label = f"{survey_name(survey)} {band}"
    return f"{label} ({CONTAMINATED})" if contaminated else label


def value_axis(ax: Axes, lc: LightCurve) -> None:
    """Label the value axis, brightest at the top for magnitudes."""
    if lc.value_kind == "mag":
        ax.set_ylabel(f"{lc.band} (mag)")
        if not ax.yaxis_inverted():
            ax.invert_yaxis()
    else:
        ax.set_ylabel("Relative flux")
