"""Alias tests: fold each candidate period and its aliases, and rank them by phase scatter.

Spec §6 step 5. A periodogram peak is only a candidate. Sampling puts false peaks beside
the true one: a survey that observes once a night sees a star at frequency f and at
f ± 1 c/d alike, and at f ± 2 c/d and the sidereal day's f ± 1.00274 c/d more faintly.
TESS's 13.7 d orbit modulates its systematics and adds sidelobes at f ± 1/13.7 c/d. A
sinusoid fits an eclipsing binary with similar eclipses best at half its period, and an
ellipsoidal or W UMa star can be mistaken for a pulsator at half its period too (the
ASAS-3 `DSCTr` stars VSX names are EW at 2P). So every candidate P is tried at 2P and
P/2, and at its aliases 1 / |1/P ± 1/T| for T of 1 d, 0.5 d and the sidereal day, and for
TESS also its orbit.

Each hypothesis is folded and cut into phase bins. Its scatter is phase dispersion
(Stellingwerf 1978): the pooled within-bin variance over the total variance. A wrong period
spreads the star's cycle across every bin and leaves that ratio near 1; the true one
leaves only the noise. Scatter alone cannot choose between P and 2P, though. At a fixed bin
count each bin at 2P spans twice as much of the cycle, so its fold blurs and the shorter
period wins even when its fold superposes two unequal minima: the `DSCTr` error itself. So
2P is cut into twice as many bins as P, and P/2 into half as many, which gives all three
the same resolution per cycle of the true period. More bins always fit a little better, so
the ranking key is the Bayesian information criterion of the binned fold,
N ln(RSS/N) + bins × ln N, and a longer period has to earn its extra bins by separating
something the shorter one superposes. Minima too alike to tell apart stay at the shorter
period, and the classifier weighs EW against ELL from there.

The ranking pools every candidate's hypotheses for the band, so the best fold wins whichever
peak it came from. The fit is unweighted: errors from faint, sparse photometry are
themselves too uncertain to weight by.
"""

from collections.abc import Iterable

import numpy as np

from argelander.analysis.clean import sufficient
from argelander.analysis.model import AliasTest, Hypothesis, PeriodogramResult
from argelander.analysis.periodogram import LS_PERIOD_RANGE
from argelander.lightcurve import LightCurve

SIDEREAL_DAY = 0.99727  # days
TESS_ORBIT = 13.7  # days
# The sampling periods whose aliases every survey is tested at, by relation label.
SAMPLING_ALIASES = {"1d": 1.0, "0.5d": 0.5, "sidereal": SIDEREAL_DAY}
N_BINS = 20  # at P; 2P gets twice as many and P/2 half
MIN_PER_BIN = 5  # fewer bins for small Light Curves, so each bin's variance means something


def candidates(*results: PeriodogramResult) -> list[float]:
    """Every distinct period a band's periodograms put forward: their peaks and Gaia's period."""
    found: list[float] = []
    for result in results:
        periods = [peak.period for peak in result.peaks]
        if result.gaia is not None:
            periods.append(result.gaia.period)
        found.extend(p for p in periods if p not in found)
    return found


def alias_test(lc: LightCurve, periods: Iterable[float]) -> AliasTest:
    """Fold a cleaned Light Curve at each candidate and its aliases; rank lowest score first."""
    if not sufficient(lc):
        return AliasTest(survey=lc.survey, band=lc.band, ranking=())
    n_bins = min(N_BINS, lc.time.size // MIN_PER_BIN)
    hypotheses = [
        _fold(lc, candidate, relation, period, bins)
        for candidate in periods
        for relation, period, bins in _hypotheses(candidate, lc.survey, n_bins)
    ]
    return AliasTest(
        survey=lc.survey,
        band=lc.band,
        ranking=tuple(sorted(hypotheses, key=lambda h: h.score)),
    )


def _hypotheses(period: float, survey: str, n_bins: int) -> list[tuple[str, float, int]]:
    """(relation, period, bins) for a candidate and its aliases inside the Lomb-Scargle range.

    The candidate itself is always kept, so a Gaia period outside the range is still scored.
    """
    sampling = dict(SAMPLING_ALIASES)
    if survey == "tess":
        sampling["tess_orbit"] = TESS_ORBIT
    tried = [("2P", 2 * period, 2 * n_bins), ("P/2", period / 2, n_bins // 2)]
    for label, interval in sampling.items():
        for sign, direction in (("+", 1.0), ("-", -1.0)):
            frequency = abs(1.0 / period + direction / interval)
            if frequency > 0:
                tried.append((label + sign, 1.0 / frequency, n_bins))
    low, high = LS_PERIOD_RANGE
    return [("P", period, n_bins)] + [t for t in tried if low <= t[1] <= high]


def _fold(
    lc: LightCurve, candidate: float, relation: str, period: float, n_bins: int
) -> Hypothesis:
    """Fold at `period` into `n_bins` phase bins and score the binned fit."""
    phase = ((lc.time - lc.time[0]) / period) % 1.0
    bins = np.minimum((phase * n_bins).astype(np.intp), n_bins - 1)
    value = lc.value - lc.value.mean()
    count = np.bincount(bins, minlength=n_bins)
    total = np.bincount(bins, weights=value, minlength=n_bins)
    squares = np.bincount(bins, weights=value**2, minlength=n_bins)
    occupied = count > 0
    residual = float(np.sum(squares[occupied] - total[occupied] ** 2 / count[occupied]))
    n, dof = value.size, value.size - np.count_nonzero(occupied)
    variance = float(np.sum(value**2)) / (n - 1)
    # Nothing to explain, or too few points per bin to say: the fold explains nothing.
    scatter = residual / dof / variance if dof > 0 and variance > 0 else 1.0
    # A noiseless fold would leave no residual; a floor keeps its logarithm finite.
    floor = np.finfo(float).tiny
    return Hypothesis(
        period=period,
        candidate=candidate,
        relation=relation,
        bins=n_bins,
        scatter=scatter,
        score=float(n * np.log(max(residual, floor) / n) + n_bins * np.log(n)),
    )
