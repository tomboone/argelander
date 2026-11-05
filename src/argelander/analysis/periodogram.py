"""Candidate periods per band: Lomb-Scargle for any shape, BoxLeastSquares for eclipsers.

Spec §6 step 4. Lomb-Scargle fits a sinusoid, so an eclipsing binary whose two eclipses look
alike folds best at half its period; BLS fits a single box-shaped dip per cycle and recovers
the true one when the secondary eclipse is much the shallower (under about 0.4 of the
primary's depth; deeper secondaries fold best at P/2 for BLS too, and the alias step, next,
has to settle those).

The two grids differ because the methods do. A Lomb-Scargle peak is 1/baseline wide in
frequency, so a regular grid at a tenth of that never misses one. A BLS peak is far
narrower: folding a frequency δf off the true one slides the eclipse by baseline × δf/f
days from one end of the Light Curve to the other, and a slide wider than the box smears
the eclipse out. Each BLS step keeps that slide under half an eclipse: under half the
narrowest box at short periods, and under half a `BLS_MIN_DUTY_CYCLE` eclipse at long ones,
where no box that narrow is plausible. That makes the grid geometric below a period of
`BLS_DURATIONS[0] / BLS_MIN_DUTY_CYCLE` and regular above it.

Evaluating BLS on that whole grid is most of the analyzer's time, so the search runs in two
stages (NEU-1839). A coarse pass steps `BLS_COARSEN` times wider, so a narrow eclipse
smears to up to that many times its width and its peak is diluted rather than lost. The
`BLS_REFINE_PEAKS` highest coarse peaks are then re-searched at the fine step, two coarse
steps either side. Far more peaks are refined than are reported: the dilution can sink a
true peak, a sparsely sampled one most of all, below hundreds of noise peaks that the coarse
step leaves alone.

TESS's coarse pass reads its Light Curve re-binned from 10 to 30 minutes
(`BLS_COARSE_BIN_WIDTH`, NEU-2191), because a pass costs one sweep over the points per trial
period and TESS's coarse pass was most of a continuous-viewing-zone Target's Analysis. The
coarse step already lets a 0.01 d eclipse sit up to 0.04 d off a trial period; a 30-minute
bin adds at most 0.010 d to that. The refine windows and the Gaia period are searched on the
10-minute curve as before, and TESS's peaks are the refined windows' own local maxima. Its
returned periodogram, which the plot draws, is the re-binned curve's throughout, the refined
windows scored on it too: the two curves' powers differ off the peaks (a real eclipse leaks
more power into the off-peak trials through 30-minute bins), so merging one curve's windows
into the other's coarse grid would draw a step at every window edge.

Gaia's published period is the first hypothesis: whenever one is supplied its power is
recorded beside the top peaks, whether or not a peak lands on it.
"""

import numpy as np
from astropy.timeseries import BoxLeastSquares, LombScargle

from argelander.analysis.bin import bin_tess
from argelander.analysis.clean import clean, sufficient
from argelander.analysis.model import Method, Peak, PeriodogramResult
from argelander.lightcurve import LightCurve

LS_PERIOD_RANGE = (0.05, 100.0)  # days
BLS_PERIOD_RANGE = (0.1, 30.0)
OVERSAMPLING = 10
N_PEAKS = 5
# Box widths tried at every BLS period. astropy requires each to be shorter than the shortest
# period searched; a longer eclipse still registers, as a box sitting inside it.
BLS_DURATIONS = (0.01, 0.02, 0.04, 0.08)  # days
BLS_MIN_DUTY_CYCLE = 0.01  # the narrowest eclipse searched for, as a fraction of the period
BLS_COARSEN = BLS_DURATIONS[-1] / BLS_DURATIONS[0]  # coarse pass's step, in fine steps
BLS_REFINE_PEAKS = 500  # coarse peaks re-searched at the fine step (spec §6 step 4)
BLS_REFINE_HALF_WIDTH = 2  # coarse steps either side of a peak that are re-searched
# TESS's coarse pass only. A third of the points of the 10-minute curve, and well inside the
# 0.04 d a coarse step already lets an eclipse drift (spec §6 step 4).
BLS_COARSE_BIN_WIDTH = 30 / 1440  # days
# Box widths, as fractions of the period, that `eclipse_duration` fits at one known period.
ECLIPSE_DUTY_CYCLES = np.geomspace(0.005, 0.3, 40)


def periodograms(
    lc: LightCurve, gaia_period: float | None = None
) -> tuple[PeriodogramResult, PeriodogramResult]:
    """Clean one band's Light Curve, then search it with Lomb-Scargle and BLS."""
    cleaned = clean(lc)
    return lomb_scargle(cleaned, gaia_period), box_least_squares(cleaned, gaia_period)


def lomb_scargle(lc: LightCurve, gaia_period: float | None = None) -> PeriodogramResult:
    """Lomb-Scargle over 0.05–100 d of a cleaned Light Curve, with Baluev false-alarm odds."""
    frequency = _regular_grid(lc.time, *LS_PERIOD_RANGE)
    if not sufficient(lc) or frequency.size < 3:
        return _insufficient(lc, "lomb_scargle")
    ls = LombScargle(lc.time, lc.value, lc.error)
    power = np.asarray(ls.power(frequency, assume_regular_frequency=True))

    def peak(period: float, peak_power: float) -> Peak:
        fap = ls.false_alarm_probability(
            peak_power,
            method="baluev",
            minimum_frequency=frequency[0],
            maximum_frequency=frequency[-1],
        )
        return Peak(period=period, power=peak_power, false_alarm_probability=float(fap))

    gaia = None
    if gaia_period is not None:
        # A period chosen before looking needs the single-frequency odds, not the whole-grid
        # odds a peak picked from the grid needs.
        gaia_power = float(ls.power(1.0 / gaia_period))
        gaia_fap = float(ls.false_alarm_probability(gaia_power, method="single"))
        gaia = Peak(period=gaia_period, power=gaia_power, false_alarm_probability=gaia_fap)
    return PeriodogramResult(
        survey=lc.survey,
        band=lc.band,
        method="lomb_scargle",
        n_points=lc.time.size,
        frequency=frequency,
        power=power,
        peaks=tuple(peak(p, w) for p, w in _top_peaks(frequency, power)),
        gaia=gaia,
        insufficient=False,
    )


def box_least_squares(
    lc: LightCurve, gaia_period: float | None = None, *, two_stage: bool = True
) -> PeriodogramResult:
    """BoxLeastSquares over 0.1–30 d of a cleaned Light Curve; its peaks carry no FAP.

    `two_stage=False` evaluates the whole fine grid, the search before NEU-1839. The pipeline
    never passes it; it is there to check the two-stage search against.
    """
    fine = _bls_grid(lc.time)
    if not sufficient(lc) or fine.size < 3:
        return _insufficient(lc, "bls")
    bls = _bls(lc)
    # Both grids come from this curve's times, so the refine windows line up with the fine
    # grid whichever curve ranks the coarse one.
    coarse = _bls_grid(lc.time, coarsen=BLS_COARSEN)
    if two_stage and coarse.size >= 3:
        coarse_lc = bin_tess(lc, width=BLS_COARSE_BIN_WIDTH)
        rebinned = coarse_lc is not lc and sufficient(coarse_lc)
        ranking = _bls(coarse_lc) if rebinned else bls
        coarse_power = _power(ranking, coarse)
        window = _windows(coarse, coarse_power, fine)
        refined_power = _power(bls, fine[window])
        if rebinned:
            # Peaks from the 10-minute refine; the periodogram from the 30-minute curve alone.
            peaks = _top_peaks(fine, _scatter(fine.size, window, refined_power))
            frequency, power = _merge(
                coarse, coarse_power, fine[window], _power(ranking, fine[window])
            )
        else:
            frequency, power = _merge(coarse, coarse_power, fine[window], refined_power)
            peaks = _top_peaks(frequency, power)
    else:
        frequency, power = fine, _power(bls, fine)
        peaks = _top_peaks(frequency, power)

    gaia = None
    if gaia_period is not None:
        # Outside the search range a published period may be shorter than every box.
        durations = [d for d in BLS_DURATIONS if d < gaia_period] or [gaia_period / 2]
        gaia_power = float(np.asarray(bls.power([gaia_period], durations).power)[0])
        gaia = Peak(period=gaia_period, power=gaia_power, false_alarm_probability=None)
    return PeriodogramResult(
        survey=lc.survey,
        band=lc.band,
        method="bls",
        n_points=lc.time.size,
        frequency=frequency,
        power=power,
        peaks=tuple(Peak(period=p, power=w, false_alarm_probability=None) for p, w in peaks),
        gaia=gaia,
        insufficient=False,
    )


def eclipse_duration(lc: LightCurve, period: float) -> float | None:
    """The primary eclipse's duration as a fraction of `period`: the box BLS fits best when
    folded at that one period, over boxes from 0.5 % to 30 % of it (`ECLIPSE_DUTY_CYCLES`).
    None when the band has too few points.

    The search's own boxes stop at `BLS_DURATIONS[-1]`, two hours, which is a sliver of a
    long-period eclipse, so the duration is fitted again at the Proposal period.
    """
    if not sufficient(lc):
        return None
    fit = _bls(lc).power([period], ECLIPSE_DUTY_CYCLES * period)
    return float(np.asarray(fit.duration)[0]) / period


def _bls(lc: LightCurve) -> BoxLeastSquares:
    # BLS looks for dips. A star in eclipse is fainter, which is a larger magnitude.
    y = -lc.value if lc.value_kind == "mag" else lc.value
    return BoxLeastSquares(lc.time, y, lc.error)


def _power(bls: BoxLeastSquares, frequency: np.ndarray) -> np.ndarray:
    return np.asarray(bls.power(1.0 / frequency, BLS_DURATIONS).power)


def _windows(coarse: np.ndarray, coarse_power: np.ndarray, fine: np.ndarray) -> np.ndarray:
    """Indices into `fine`, ascending, of the frequencies within `BLS_REFINE_HALF_WIDTH`
    coarse steps of the `BLS_REFINE_PEAKS` highest coarse peaks."""
    peaks = _maxima(coarse_power, BLS_REFINE_PEAKS)
    low = coarse[np.maximum(peaks - BLS_REFINE_HALF_WIDTH, 0)]
    high = coarse[np.minimum(peaks + BLS_REFINE_HALF_WIDTH, coarse.size - 1)]
    starts = np.searchsorted(fine, low, side="left")
    stops = np.searchsorted(fine, high, side="right")
    spans = [np.arange(a, b) for a, b in zip(starts, stops, strict=True)]
    return np.unique(np.concatenate([np.empty(0, dtype=np.intp), *spans]))


def _merge(
    coarse: np.ndarray, coarse_power: np.ndarray, refined: np.ndarray, refined_power: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """The coarse grid with the refined frequencies merged in, ascending.

    Where a refined frequency coincides with a coarse one, both are scored on one curve and
    their powers are identical, so np.unique may keep either copy.
    """
    frequency, keep = np.unique(np.concatenate([coarse, refined]), return_index=True)
    return frequency, np.concatenate([coarse_power, refined_power])[keep]


def _scatter(size: int, index: np.ndarray, power: np.ndarray) -> np.ndarray:
    """`power` at `index` in an array of `size` that is NaN elsewhere.

    A NaN neighbour is never exceeded, so `_maxima` finds no peak at a window's edge, and
    none outside the windows.
    """
    full = np.full(size, np.nan)
    full[index] = power
    return full


def _regular_grid(time: np.ndarray, min_period: float, max_period: float) -> np.ndarray:
    """1/d from `1/max_period` to `1/min_period` in steps of a tenth of a peak's width."""
    baseline = time[-1] - time[0] if time.size else 0.0
    if baseline <= 0:
        return np.empty(0)
    return np.arange(1.0 / max_period, 1.0 / min_period, 1.0 / (OVERSAMPLING * baseline))


def _bls_grid(time: np.ndarray, coarsen: float = 1.0) -> np.ndarray:
    """1/d over `BLS_PERIOD_RANGE`; δf = coarsen × max(narrowest box × f, duty cycle) / baseline."""
    baseline = time[-1] - time[0] if time.size else 0.0
    if baseline <= 0:
        return np.empty(0)
    low, high = 1.0 / BLS_PERIOD_RANGE[1], 1.0 / BLS_PERIOD_RANGE[0]
    knee = float(np.clip(BLS_MIN_DUTY_CYCLE / BLS_DURATIONS[0], low, high))
    step = coarsen * BLS_MIN_DUTY_CYCLE / baseline
    # low + step × k, not np.arange's accumulated sum, so that every `coarsen`-th fine
    # frequency is bit-identical to a coarse one and the refined merge drops it.
    regular = low + step * np.arange(np.ceil((knee - low) / step))
    ratio = 1.0 + coarsen * BLS_DURATIONS[0] / baseline
    geometric = knee * ratio ** np.arange(int(np.log(high / knee) / np.log(ratio)) + 1)
    return np.concatenate([regular, geometric])


def _top_peaks(frequency: np.ndarray, power: np.ndarray) -> list[tuple[float, float]]:
    """(period, power) of the `N_PEAKS` highest local maxima, strongest first."""
    return [(float(1.0 / frequency[i]), float(power[i])) for i in _maxima(power, N_PEAKS)]


def _maxima(power: np.ndarray, count: int) -> np.ndarray:
    """Indices of the `count` highest local maxima of `power`, strongest first."""
    inner = power[1:-1]
    maxima = np.flatnonzero((inner > power[:-2]) & (inner >= power[2:])) + 1
    return maxima[np.argsort(power[maxima])[::-1][:count]]


def _insufficient(lc: LightCurve, method: Method) -> PeriodogramResult:
    return PeriodogramResult(
        survey=lc.survey,
        band=lc.band,
        method=method,
        n_points=lc.time.size,
        frequency=np.empty(0),
        power=np.empty(0),
        peaks=(),
        gaia=None,
        insufficient=True,
    )
