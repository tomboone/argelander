"""The light curve's shape at the agreed period, and its variability whatever the period.

Spec §7. The classifier's rules read numbers, not Light Curves: the amplitude to set against
each type's range, the width and depth of the minima that tell eclipses apart, and the rise
time and skewness that tell an RRab star from an RRc. `shape` folds one band at the
Agreement's period and measures them; `variability` says how far a band's spread exceeds
its errors, which is what tells a constant star from an irregular one when no period is
agreed.

One band speaks for the Target: the one whose fold stands clearest above its scatter, among
surveys the blend checks found clean when there are any, and among magnitude bands before
flux bands. TESS's relative flux is diluted by every neighbour in its 21″ pixels, so its
amplitude and shape are a last resort; it is converted to magnitudes first. Gaia's few
dozen epochs are never folded here.

The eclipse measures come from the binned fold, its bin medians smoothed over three bins,
which keeps a narrow eclipse's depth. The pulsation measures come from a Fourier series
fitted to the fold, which keeps a steep rise smooth where bins would make it a step.
"""

from collections.abc import Collection, Iterable

import numpy as np

from argelander.analysis.clean import sufficient
from argelander.analysis.model import Agreement, Shape, Variability
from argelander.classify.types import AMPLITUDE_TO_V, HALF_DEPTH, NEAR_MAXIMUM, SECONDARY_WINDOW
from argelander.lightcurve import LightCurve

N_BINS = 50
MIN_PER_BIN = 5
MIN_BINS = 10
FOURIER_ORDER = 6  # fewer harmonics round off an RRab star's steep rise
CURVE_SAMPLES = 1000  # phases the fitted curve is evaluated at
# The 5th to the 95th percentile of a normal distribution spans 3.29 standard deviations.
NORMAL_SPREAD = 3.29


def shape(
    curves: Iterable[LightCurve], agreement: Agreement | None, contaminated: Collection[str]
) -> Shape | None:
    """The shape at the agreed period in the clearest band; None without Agreement or a band
    with enough points."""
    if agreement is None:
        return None
    bands = _bands(curves)
    if not bands:
        return None
    measured = [(lc, _measure(lc, agreement.agreed_period)) for lc in bands]
    _, best = max(
        measured,
        key=lambda m: (m[0].survey not in contaminated, m[0].value_kind == "mag", m[1].snr),
    )
    return best


def variability(curves: Iterable[LightCurve], contaminated: Collection[str]) -> Variability | None:
    """The most variable clean band's spread against its noise; None when every band is
    contaminated, since a neighbour's light could be what varies."""
    measured = [_variability(lc) for lc in _bands(curves) if lc.survey not in contaminated]
    return max(measured, key=lambda v: v.excess) if measured else None


def magnitudes(lc: LightCurve) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(time, magnitude, error) of a Light Curve, a flux band converted and its non-positive
    fluxes dropped."""
    if lc.value_kind == "mag":
        return lc.time, lc.value, lc.error
    keep = lc.value > 0
    flux = lc.value[keep]
    return lc.time[keep], -2.5 * np.log10(flux), 2.5 / np.log(10) * lc.error[keep] / flux


def _bands(curves: Iterable[LightCurve]) -> list[LightCurve]:
    return [lc for lc in curves if lc.survey != "gaia" and sufficient(lc)]


def _measure(lc: LightCurve, period: float) -> Shape:
    time, mag, _ = magnitudes(lc)
    phase = ((time - time[0]) / period) % 1.0

    binned, scatter = _binned(phase, mag)
    faintest, brightest = float(binned.max()), float(binned.min())
    amplitude = faintest - brightest
    n_bins = binned.size
    # Phases from primary minimum, the faintest bin, and the depth below maximum at each as a
    # fraction of the amplitude: 1 at primary minimum, 0 at maximum light.
    depth = np.roll(binned, -int(binned.argmax())) - brightest
    depth = depth / amplitude if amplitude > 0 else np.zeros_like(depth)
    low, high = (round(edge * n_bins) for edge in SECONDARY_WINDOW)

    curve, amplitudes = _fourier(phase, mag)
    return Shape(
        survey=lc.survey,
        band=lc.band,
        n_points=int(mag.size),
        period=period,
        amplitude=amplitude,
        amplitude_v=amplitude * AMPLITUDE_TO_V.get(lc.band, 1.0),
        snr=amplitude / scatter if scatter > 0 else float("inf"),
        rise=float((curve.argmin() - curve.argmax()) % curve.size) / curve.size,
        skewness=_skewness(curve),
        r21=float(amplitudes[1] / amplitudes[0]) if amplitudes[0] > 0 else 0.0,
        eclipse_width=_width(depth) / n_bins,
        secondary_depth=float(depth[low : high + 1].max()),
        flat_fraction=float(np.mean(depth < NEAR_MAXIMUM)),
    )


def _binned(phase: np.ndarray, mag: np.ndarray) -> tuple[np.ndarray, float]:
    """The fold's bin medians, gaps filled around the cycle and smoothed over three bins, and
    the points' standard deviation about them."""
    n_bins = max(MIN_BINS, min(N_BINS, mag.size // MIN_PER_BIN))
    bins = np.minimum((phase * n_bins).astype(np.intp), n_bins - 1)
    medians = np.full(n_bins, np.nan)
    for i in np.unique(bins):
        medians[i] = np.median(mag[bins == i])
    filled = ~np.isnan(medians)
    index = np.arange(n_bins)
    medians = np.interp(index, index[filled], medians[filled], period=n_bins)
    smoothed = (np.roll(medians, 1) + medians + np.roll(medians, -1)) / 3.0
    return smoothed, float(np.std(mag - smoothed[bins]))


def _width(depth: np.ndarray) -> int:
    """Bins around primary minimum, at index 0, deeper than `HALF_DEPTH` of the primary's."""
    width = 1
    for step in (1, -1):
        for k in range(1, depth.size // 2):
            if depth[step * k] <= HALF_DEPTH:
                break
            width += 1
    return width


def _fourier(phase: np.ndarray, mag: np.ndarray) -> tuple[np.ndarray, list[float]]:
    """A Fourier series fitted to the fold: the curve over a cycle, and each harmonic's
    amplitude."""

    def design(x: np.ndarray) -> np.ndarray:
        angle = 2 * np.pi * x
        columns = [np.ones_like(x)]
        for k in range(1, FOURIER_ORDER + 1):
            columns += [np.cos(k * angle), np.sin(k * angle)]
        return np.column_stack(columns)

    coefficients, *_ = np.linalg.lstsq(design(phase), mag, rcond=None)
    grid = np.arange(CURVE_SAMPLES) / CURVE_SAMPLES
    amplitudes = [
        float(np.hypot(coefficients[2 * k - 1], coefficients[2 * k]))
        for k in range(1, FOURIER_ORDER + 1)
    ]
    return design(grid) @ coefficients, amplitudes


def _skewness(curve: np.ndarray) -> float:
    centred = curve - curve.mean()
    spread = float(np.sqrt(np.mean(centred**2)))
    return float(np.mean(centred**3)) / spread**3 if spread > 0 else 0.0


def _variability(lc: LightCurve) -> Variability:
    _, mag, error = magnitudes(lc)
    spread = float(np.percentile(mag, 95) - np.percentile(mag, 5))
    noise = float(np.median(error))
    return Variability(
        survey=lc.survey,
        band=lc.band,
        n_points=int(mag.size),
        spread=spread,
        noise=noise,
        excess=spread / (NORMAL_SPREAD * noise) if noise > 0 else float("inf"),
    )
