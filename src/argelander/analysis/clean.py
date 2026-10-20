"""Survey quality flags and outlier clipping → the cleaned Light Curve the periodograms see.

Spec §6 step 3. Three cuts, in order:

1. Drop points the survey flagged (`quality != 0`) or whose value or error is unusable.
2. Sigma-clip at 5σ around a running median: a point is clipped when it sits more than 5σ
   from the median of its `NEIGHBOURS` neighbours on each side, where σ is the MAD-derived
   scatter of every such residual. The point itself is left out of its own median: where
   the star changes faster than the noise, the median of a window that includes it is the
   point itself, and those zero residuals would shrink σ.
3. Require `MIN_POINTS` survivors before a band is searched for a period.

A point is only judged when all of its neighbours lie within `MAX_NEIGHBOUR_GAP` days of
it, which in practice means TESS 120 s and 200 s data. Anywhere sparser, the neighbours sit
at unrelated phases (ZTF, ASAS-SN and Gaia return a few points a night at most) or far
enough round the cycle (TESS 600 s and 1800 s full-frame images against a 0.05 d pulsator)
that their median is not the star's level at that moment, and every point inside an eclipse
or at a pulsation's extreme would look like an outlier. Those Light Curves are cleaned by
their survey flags alone. In faint, sparse photometry a wild point and an eclipse point look
alike, so nothing here could tell them apart anyway; the alias tests and Agreement downstream
are what keep a single bad point from deciding a period.
"""

import numpy as np

from argelander.lightcurve import LightCurve

MIN_POINTS = 30
SIGMA_CLIP = 5.0
NEIGHBOURS = 2  # on each side, so the running median is taken over four points
MAX_NEIGHBOUR_GAP = 0.005  # days; ±7 minutes, which two 200 s cadences fit inside

# 1.4826 × MAD is σ for Gaussian noise.
_MAD_TO_SIGMA = 1.4826


def clean(lc: LightCurve) -> LightCurve:
    """Return `lc` with flagged, unusable and outlying points removed and `quality` all 0.

    `meta["clean"]` records how many points each cut removed.
    """
    usable = lc.clean_mask() & np.isfinite(lc.value) & np.isfinite(lc.error) & (lc.error > 0)
    time, value = lc.time[usable], lc.value[usable]
    clipped = _outliers(time, value)
    keep = np.flatnonzero(usable)[~clipped]
    return LightCurve(
        survey=lc.survey,
        source_key=lc.source_key,
        band=lc.band,
        time=lc.time[keep],
        value=lc.value[keep],
        error=lc.error[keep],
        quality=np.zeros(keep.size, dtype=np.int32),
        value_kind=lc.value_kind,
        meta={
            **lc.meta,
            "clean": {
                "n_input": int(lc.time.size),
                "n_unusable": int(lc.time.size - usable.sum()),
                "n_clipped": int(clipped.sum()),
            },
        },
    )


def sufficient(lc: LightCurve) -> bool:
    """Whether a cleaned Light Curve has enough points to search for a period."""
    return lc.time.size >= MIN_POINTS


def _outliers(time: np.ndarray, value: np.ndarray) -> np.ndarray:
    """Boolean mask of points more than `SIGMA_CLIP` σ from their running median."""
    clipped = np.zeros(time.size, dtype=bool)
    centre = np.arange(NEIGHBOURS, time.size - NEIGHBOURS)
    near = (time[centre + NEIGHBOURS] - time[centre] <= MAX_NEIGHBOUR_GAP) & (
        time[centre] - time[centre - NEIGHBOURS] <= MAX_NEIGHBOUR_GAP
    )
    dense = centre[near]
    if dense.size == 0:
        return clipped
    offsets = [o for o in range(-NEIGHBOURS, NEIGHBOURS + 1) if o != 0]
    neighbours = np.stack([value[dense + o] for o in offsets])
    residual = value[dense] - np.median(neighbours, axis=0)
    sigma = _MAD_TO_SIGMA * np.median(np.abs(residual - np.median(residual)))
    if sigma == 0:
        return clipped
    clipped[dense] = np.abs(residual) > SIGMA_CLIP * sigma
    return clipped
