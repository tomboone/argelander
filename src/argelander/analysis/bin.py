"""TESS binned to a fixed time grid after cleaning, before the period search (NEU-2187).

Spec §6 step 3. A Target in TESS's continuous viewing zone (Draco holds the north ecliptic
pole) has a stitched 2-minute Light Curve of up to 250,000 points, and every step after
cleaning costs time in proportion to them: on the mini, about 1.8 s per 1,000 TESS points.
Binning happens after `clean()` because cleaning's running-median clip needs the native
cadence's neighbours, and before everything else, so the periodograms, alias tests, blend
checks, Agreement, shape, the Proposal's range and epoch, and the plots all read one curve.

Bin k holds the points with `floor((time - time[0]) / TESS_BIN_WIDTH + 0.5) == k`: the grid
starts half a bin before the first point, so a cadence of exactly the bin width puts each
point mid-bin rather than on an edge where rounding would decide its bin. Empty bins
produce nothing, so the gaps between sectors stay gaps. Each bin becomes one point at its
points' mean time and mean value, with the propagated error `sqrt(sum(error²)) / n`. Mean,
not median: cleaning has already clipped the outliers, and the mean keeps the precision a
median loses. A bin of one point passes it through unchanged, so 600 s and 1,800 s
full-frame curves come out as they went in, bar the odd pair of 600 s points that lands in
one bin where a later sector's cadence, in BJD a hair off 10 minutes, drifts across an edge.

ZTF, ASAS-SN and Gaia hold a few hundred points a band and are returned as they are.
"""

import numpy as np

from argelander.lightcurve import LightCurve

# 10 minutes. The shortest period searched is Lomb-Scargle's 0.05 d (72 min,
# `periodogram.LS_PERIOD_RANGE`), and a 10-minute bin is 14 % of that cycle, so a sinusoid
# there keeps about 97 % of its amplitude (sinc(π × 0.139)). It is also narrower than BLS's
# narrowest box (0.01 d, 14.4 min). 200 s data shrinks about 3×, 120 s data about 5×.
TESS_BIN_WIDTH = 10 / 1440  # days


def bin_tess(lc: LightCurve) -> LightCurve:
    """A cleaned TESS Light Curve binned to `TESS_BIN_WIDTH`; any other survey's unchanged.

    `meta["bin"]` records the width and how many points went in.
    """
    if lc.survey != "tess":
        return lc
    n = lc.time.size
    meta = {**lc.meta, "bin": {"width_days": TESS_BIN_WIDTH, "n_input": int(n)}}
    if n == 0:
        return _with(lc, lc.time, lc.value, lc.error, lc.sap, meta)
    index = np.floor((lc.time - lc.time[0]) / TESS_BIN_WIDTH + 0.5).astype(np.int64)
    starts = np.flatnonzero(np.r_[True, np.diff(index) > 0])
    counts = np.diff(np.r_[starts, n])
    first = np.repeat(starts, counts)

    def mean(values: np.ndarray) -> np.ndarray:
        # Summed as offsets from each bin's first point, so a BJD near 2.46 million keeps
        # its precision; a one-point bin's offset is exactly 0 and passes it through.
        return values[starts] + np.add.reduceat(values - values[first], starts) / counts

    error = np.where(
        counts == 1,
        lc.error[starts],
        np.sqrt(np.add.reduceat(lc.error**2, starts)) / counts,
    )
    sap = mean(lc.sap) if lc.sap is not None else None
    return _with(lc, mean(lc.time), mean(lc.value), error, sap, meta)


def _with(
    lc: LightCurve,
    time: np.ndarray,
    value: np.ndarray,
    error: np.ndarray,
    sap: np.ndarray | None,
    meta: dict,
) -> LightCurve:
    return LightCurve(
        survey=lc.survey,
        source_key=lc.source_key,
        band=lc.band,
        time=time,
        value=value,
        error=error,
        quality=np.zeros(time.size, dtype=np.int32),
        value_kind=lc.value_kind,
        meta=meta,
        oid=None,
        sap=sap,
    )
