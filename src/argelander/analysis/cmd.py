"""Where a Target sits on Gaia's colour-magnitude diagram.

Spec §6 step 8. A type proposal has to be consistent with the star's position: an RR Lyrae
belongs in the instability strip, a W UMa binary on the main sequence. Absolute G comes from
the parallax only when it is significant, `parallax / parallax_error` above
`MIN_PARALLAX_OVER_ERROR`; below that, 1 / parallax is too biased a distance to place the star.
The region is a coarse label from the fixed polygons in `classify.types`.
"""

import math

from argelander.analysis.model import CmdPosition, Region
from argelander.catalog.gaia import GaiaRecord
from argelander.classify.types import CMD_REGIONS

MIN_PARALLAX_OVER_ERROR = 5.0


def cmd_position(record: GaiaRecord) -> CmdPosition:
    abs_g = distance_pc = None
    parallax, error = record.parallax, record.parallax_error
    if (
        record.g is not None
        and parallax is not None
        and error is not None
        and error > 0
        and parallax / error > MIN_PARALLAX_OVER_ERROR
    ):
        # Parallax in mas: M = m + 5 log10(parallax / 1000) + 5.
        abs_g = record.g + 5.0 * math.log10(parallax) - 10.0
        distance_pc = 1000.0 / parallax
    bp_rp = record.bp_rp
    return CmdPosition(
        bp_rp=bp_rp,
        abs_g=abs_g,
        distance_pc=distance_pc,
        region=region(bp_rp, abs_g) if abs_g is not None and bp_rp is not None else "unknown",
    )


def region(bp_rp: float, abs_g: float) -> Region:
    """The first region of `CMD_REGIONS` whose polygon holds the point, else "unknown"."""
    return next(
        (name for name, polygon in CMD_REGIONS if _inside(bp_rp, abs_g, polygon)), "unknown"
    )


def _inside(x: float, y: float, polygon: tuple[tuple[float, float], ...]) -> bool:
    # Ray casting: count the edges a ray from the point towards +x crosses.
    inside = False
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1], strict=True):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside
