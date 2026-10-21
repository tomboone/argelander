"""Cleaning and the per-band period searches (spec §6 steps 3–4), on synthetic light curves.

The synthetic stars are sampled the way the sparse surveys sample: a few hundred epochs at
random times of night over several observing seasons, so the searches face real gaps.
"""

import dataclasses

import numpy as np
from astropy.timeseries import LombScargle

from argelander.analysis.clean import MIN_POINTS, clean
from argelander.analysis.periodogram import lomb_scargle, periodograms
from argelander.lightcurve import LightCurve

TOLERANCE = 1e-3  # 0.1 %, the prototype's pass criterion (spec §11)


def seasonal_times(rng: np.random.Generator, n: int, seasons: int = 5) -> np.ndarray:
    """`n` epochs over `seasons` 200-day seasons a year apart, each at a random time of night."""
    nights = rng.integers(0, 200, n) + 365 * rng.integers(0, seasons, n)
    return np.sort(2459000.5 + nights + rng.uniform(0.15, 0.4, n))


def light_curve(
    time: np.ndarray,
    value: np.ndarray,
    sigma: float,
    *,
    value_kind: str = "mag",
    quality: np.ndarray | None = None,
) -> LightCurve:
    return LightCurve(
        survey="ztf",
        source_key="1",
        band="g",
        time=time,
        value=value,
        error=np.full(time.size, sigma),
        quality=np.zeros(time.size, dtype=np.int32) if quality is None else quality,
        value_kind=value_kind,
        meta={},
    )


def relative_error(found: float, true: float) -> float:
    return abs(found - true) / true


def test_sinusoid_with_gaps_recovers_period() -> None:
    rng = np.random.default_rng(1706)
    period = 3.14159
    time = seasonal_times(rng, 300)
    mag = 15.0 + 0.3 * np.sin(2 * np.pi * time / period) + rng.normal(0, 0.02, time.size)

    ls, _ = periodograms(light_curve(time, mag, 0.02))

    assert not ls.insufficient
    assert ls.best is not None
    assert relative_error(ls.best.period, period) < TOLERANCE
    assert ls.best.false_alarm_probability is not None
    assert ls.best.false_alarm_probability < 1e-10
    assert len(ls.peaks) == 5
    assert [p.power for p in ls.peaks] == sorted((p.power for p in ls.peaks), reverse=True)


def test_eclipsing_binary_bls_finds_true_period_ls_finds_half() -> None:
    rng = np.random.default_rng(1706)
    period, width = 1.3713, 0.08  # width in phase
    time = seasonal_times(rng, 600)
    phase = (time / period) % 1.0
    mag = 14.0 + rng.normal(0, 0.01, time.size)
    mag[np.abs(phase - 0.25) < width / 2] += 0.5  # primary eclipse
    mag[np.abs(phase - 0.75) < width / 2] += 0.1  # shallower secondary

    ls, bls = periodograms(light_curve(time, mag, 0.01))

    assert ls.best is not None and bls.best is not None
    assert relative_error(bls.best.period, period) < TOLERANCE
    assert relative_error(ls.best.period, period / 2) < TOLERANCE
    assert bls.best.false_alarm_probability is None


def test_bls_finds_narrow_eclipses() -> None:
    """A detached binary eclipsed for 2 % of its cycle, over six seasons."""
    rng = np.random.default_rng(1706)
    period, width = 2.7183, 0.02
    time = seasonal_times(rng, 800, seasons=6)
    phase = (time / period) % 1.0
    mag = 15.0 + rng.normal(0, 0.02, time.size)
    mag[np.abs(phase - 0.3) < width / 2] += 0.7
    mag[np.abs(phase - 0.8) < width / 2] += 0.14

    _, bls = periodograms(light_curve(time, mag, 0.02))

    assert bls.best is not None
    assert relative_error(bls.best.period, period) < TOLERANCE


def test_eclipses_in_sparse_data_survive_cleaning() -> None:
    rng = np.random.default_rng(1706)
    time = seasonal_times(rng, 600)
    mag = 14.0 + rng.normal(0, 0.01, time.size)
    mag[::20] += 0.5  # one point in twenty in eclipse

    assert clean(light_curve(time, mag, 0.01)).time.size == time.size


def test_cleaning_drops_flagged_points_and_clips_dense_outliers() -> None:
    rng = np.random.default_rng(1706)
    time = 2459000.5 + np.arange(1000) * 200 / 86400  # TESS's 200 s cadence
    flux = 1.0 + 0.05 * np.sin(2 * np.pi * time / 0.8) + rng.normal(0, 0.001, time.size)
    flux[500] += 0.02  # 20σ, but well inside the star's own variation
    quality = np.zeros(time.size, dtype=np.int32)
    quality[:10] = 1
    flux[10] = np.nan

    cleaned = clean(light_curve(time, flux, 0.001, value_kind="flux", quality=quality))

    assert cleaned.meta["clean"] == {"n_input": 1000, "n_unusable": 11, "n_clipped": 1}
    assert cleaned.time.size == 988
    assert time[500] not in cleaned.time
    assert np.all(cleaned.quality == 0)


def test_cleaning_keeps_each_surviving_points_oid() -> None:
    rng = np.random.default_rng(1706)
    time = 2459000.5 + np.arange(1000) * 200 / 86400
    flux = 1.0 + 0.05 * np.sin(2 * np.pi * time / 0.8) + rng.normal(0, 0.001, time.size)
    flux[500] += 0.02
    quality = np.zeros(time.size, dtype=np.int32)
    quality[:10] = 1
    oid = np.where(np.arange(time.size) % 3 == 0, 797112400008865, 1830101400004824)
    lc = dataclasses.replace(
        light_curve(time, flux, 0.001, value_kind="flux", quality=quality), oid=oid
    )

    cleaned = clean(lc)

    assert cleaned.oid is not None
    assert cleaned.oid.size == cleaned.time.size == 989
    np.testing.assert_array_equal(cleaned.oid, oid[np.isin(time, cleaned.time)])


def test_cleaning_keeps_a_fast_pulsators_extremes() -> None:
    rng = np.random.default_rng(1706)
    time = 2459000.5 + np.arange(5000) * 120 / 86400  # TESS's 120 s cadence
    flux = 1.0 + 0.3 * np.sin(2 * np.pi * time / 0.06) + rng.normal(0, 0.005, time.size)

    cleaned = clean(light_curve(time, flux, 0.005, value_kind="flux"))

    assert cleaned.meta["clean"]["n_clipped"] == 0


def test_too_few_points_after_cleaning_is_insufficient() -> None:
    rng = np.random.default_rng(1706)
    time = seasonal_times(rng, MIN_POINTS + 10)
    quality = np.zeros(time.size, dtype=np.int32)
    quality[:11] = 4  # leaves MIN_POINTS − 1

    results = periodograms(light_curve(time, np.full(time.size, 15.0), 0.02, quality=quality))

    for result in results:
        assert result.insufficient
        assert result.n_points == MIN_POINTS - 1
        assert result.peaks == ()
        assert result.best is None


def test_gaia_period_power_is_always_recorded() -> None:
    rng = np.random.default_rng(1706)
    period = 0.61
    time = seasonal_times(rng, 300, seasons=1)
    mag = 15.0 + 0.3 * np.sin(2 * np.pi * time / period) + rng.normal(0, 0.02, time.size)
    lc = light_curve(time, mag, 0.02)

    ls, bls = periodograms(lc, gaia_period=period)
    off_peak = lomb_scargle(lc, gaia_period=7.0)

    assert ls.gaia is not None and ls.best is not None
    assert ls.gaia.period == period
    assert ls.gaia.power > 0.9 * ls.best.power
    assert bls.gaia is not None and bls.gaia.period == period
    assert off_peak.gaia is not None and off_peak.gaia.period == 7.0
    # Single-trial odds: insignificant off the peak, yet below the whole-grid odds the same
    # power would get as a peak picked from the grid.
    assert ls.gaia.false_alarm_probability is not None
    assert ls.gaia.false_alarm_probability < 1e-10
    whole_grid = LombScargle(lc.time, lc.value, lc.error).false_alarm_probability(
        off_peak.gaia.power,
        method="baluev",
        minimum_frequency=off_peak.frequency[0],
        maximum_frequency=off_peak.frequency[-1],
    )
    assert off_peak.gaia.false_alarm_probability is not None
    assert 0.01 < off_peak.gaia.false_alarm_probability < whole_grid


def test_zero_baseline_is_insufficient() -> None:
    time = np.full(MIN_POINTS, 2459000.5)

    for result in periodograms(light_curve(time, np.full(time.size, 15.0), 0.02)):
        assert result.insufficient


def test_gaia_period_shorter_than_every_box_is_still_scored() -> None:
    rng = np.random.default_rng(1706)
    time = seasonal_times(rng, 300, seasons=1)
    mag = 15.0 + rng.normal(0, 0.02, time.size)

    ls, bls = periodograms(light_curve(time, mag, 0.02), gaia_period=0.008)

    assert ls.gaia is not None and bls.gaia is not None
