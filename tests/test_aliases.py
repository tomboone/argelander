"""Alias tests (spec §6 step 5), on synthetic light curves sampled the way surveys sample."""

import numpy as np

from argelander.analysis.aliases import TESS_ORBIT, alias_test, candidates
from argelander.analysis.clean import MIN_POINTS
from argelander.analysis.model import Method, Peak, PeriodogramResult
from argelander.analysis.periodogram import periodograms
from argelander.lightcurve import LightCurve

TOLERANCE = 1e-3  # 0.1 %, the prototype's pass criterion (spec §11)


def nightly_times(rng: np.random.Generator, nights: int) -> np.ndarray:
    """One epoch a night for `nights` consecutive nights, at a random time within six hours."""
    return 2459000.5 + np.arange(nights) + rng.uniform(0.15, 0.4, nights)


def light_curve(
    time: np.ndarray, value: np.ndarray, sigma: float, survey: str = "ztf"
) -> LightCurve:
    return LightCurve(
        survey=survey,
        source_key="1",
        band="g",
        time=time,
        value=value,
        error=np.full(time.size, sigma),
        quality=np.zeros(time.size, dtype=np.int32),
        value_kind="mag",
        meta={},
    )


def relative_error(found: float, true: float) -> float:
    return abs(found - true) / true


def test_nightly_sampling_ranks_the_one_day_alias_below_the_true_period() -> None:
    rng = np.random.default_rng(1707)
    period = 0.6
    time = nightly_times(rng, 300)
    mag = 15.0 + 0.3 * np.sin(2 * np.pi * time / period) + rng.normal(0, 0.02, time.size)
    alias = 1.0 / (1.0 / period - 1.0)  # 1.5 d, the alias one cycle a day below

    result = alias_test(light_curve(time, mag, 0.02), [alias])

    assert result.best is not None
    assert result.best.relation == "1d+"
    assert result.best.candidate == alias
    assert relative_error(result.best.period, period) < TOLERANCE
    as_given = next(h for h in result.ranking if h.relation == "P")
    assert result.ranking.index(as_given) > 0
    assert as_given.scatter > 2 * result.best.scatter
    assert [h.score for h in result.ranking] == sorted(h.score for h in result.ranking)


def test_eclipsing_binary_found_at_half_period_is_settled_at_twice_that() -> None:
    rng = np.random.default_rng(1707)
    period, width = 1.3713, 0.08
    nights = rng.integers(0, 200, 600) + 365 * rng.integers(0, 5, 600)
    time = np.sort(2459000.5 + nights + rng.uniform(0.15, 0.4, 600))
    phase = (time / period) % 1.0
    mag = 14.0 + rng.normal(0, 0.01, time.size)
    mag[np.abs(phase - 0.25) < width / 2] += 0.5
    mag[np.abs(phase - 0.75) < width / 2] += 0.1
    lc = light_curve(time, mag, 0.01)
    ls, _ = periodograms(lc)
    assert ls.best is not None

    result = alias_test(lc, [ls.best.period])

    assert result.best is not None
    assert result.best.relation == "2P"
    assert relative_error(result.best.period, period) < TOLERANCE


def test_only_tess_is_tested_at_its_orbital_aliases() -> None:
    rng = np.random.default_rng(1707)
    time = np.sort(2459000.5 + rng.uniform(0, 27, 500))
    mag = 15.0 + 0.1 * np.sin(2 * np.pi * time / 2.0) + rng.normal(0, 0.01, time.size)

    tess = alias_test(light_curve(time, mag, 0.01, survey="tess"), [2.0])
    ztf = alias_test(light_curve(time, mag, 0.01), [2.0])

    tess_orbit = {h.relation: h.period for h in tess.ranking if h.relation.startswith("tess")}
    assert tess_orbit.keys() == {"tess_orbit+", "tess_orbit-"}
    assert relative_error(tess_orbit["tess_orbit+"], 1.0 / (0.5 + 1.0 / TESS_ORBIT)) < 1e-12
    assert not any(h.relation.startswith("tess") for h in ztf.ranking)
    assert {"P", "2P", "P/2", "1d+", "0.5d+", "sidereal+"} <= {h.relation for h in ztf.ranking}


def test_aliases_outside_the_search_range_are_dropped() -> None:
    rng = np.random.default_rng(1707)
    time = nightly_times(rng, 300)
    mag = 15.0 + rng.normal(0, 0.02, time.size)

    result = alias_test(light_curve(time, mag, 0.02), [1.0])

    # 1/|1 − 1| has no period and 1/|1 − 1/0.99727| is 365 d: neither is tried.
    relations = {h.relation for h in result.ranking}
    assert "1d-" not in relations and "sidereal-" not in relations
    assert all(0.05 <= h.period <= 100.0 for h in result.ranking if h.relation != "P")


def test_insufficient_light_curve_has_no_ranking() -> None:
    rng = np.random.default_rng(1707)
    time = nightly_times(rng, MIN_POINTS - 1)

    result = alias_test(light_curve(time, np.full(time.size, 15.0), 0.02), [0.6])

    assert result.ranking == ()
    assert result.best is None


def test_candidates_are_every_peak_and_the_gaia_period_once() -> None:
    def result(method: Method, periods: list[float], gaia: float | None) -> PeriodogramResult:
        return PeriodogramResult(
            survey="ztf",
            band="g",
            method=method,
            n_points=100,
            frequency=np.empty(0),
            power=np.empty(0),
            peaks=tuple(Peak(p, 1.0, None) for p in periods),
            gaia=None if gaia is None else Peak(gaia, 0.5, None),
            insufficient=False,
        )

    found = candidates(result("lomb_scargle", [0.6, 1.5], 0.61), result("bls", [1.2, 0.6], 0.61))

    assert found == [0.6, 1.5, 0.61, 1.2]


def test_contact_binary_with_nearly_equal_minima_is_settled_at_its_full_period() -> None:
    """The ASAS-3 `DSCTr` trap VSX names: a W UMa star taken for a pulsator at P/2."""
    rng = np.random.default_rng(1707)
    period = 0.3712
    nights = rng.integers(0, 200, 600) + 365 * rng.integers(0, 5, 600)
    time = np.sort(2459000.5 + nights + rng.uniform(0.15, 0.4, 600))
    phase = (time / period) % 1.0
    depth = np.where(phase < 0.5, 0.40, 0.36)
    mag = 14.0 + depth * (1 - np.cos(4 * np.pi * phase)) / 2 + rng.normal(0, 0.01, time.size)

    result = alias_test(light_curve(time, mag, 0.01), [period / 2])

    assert result.best is not None
    assert result.best.relation == "2P"
    assert relative_error(result.best.period, period) < TOLERANCE


def test_a_sinusoid_is_not_doubled() -> None:
    for seed in range(5):
        rng = np.random.default_rng(seed)
        time = nightly_times(rng, 300)
        mag = 15.0 + 0.3 * np.sin(2 * np.pi * time / 0.6) + rng.normal(0, 0.02, time.size)

        result = alias_test(light_curve(time, mag, 0.02), [0.6])

        assert result.best is not None and result.best.relation == "P"
