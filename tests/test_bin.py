"""TESS binning before the period search (spec §6 step 3, NEU-2187), on synthetic curves.

The long curve is shaped like a Draco Target in TESS's continuous viewing zone: 2-minute
cadence, sector after sector, the sectors in groups a year apart, about 250,000 points.
"""

import numpy as np
import pytest

from argelander.analysis.agreement import tolerance
from argelander.analysis.aliases import alias_test, candidates
from argelander.analysis.bin import TESS_BIN_WIDTH, bin_tess
from argelander.analysis.clean import clean
from argelander.analysis.periodogram import lomb_scargle
from argelander.analysis.pipeline import analyze
from argelander.lightcurve import LightCurve

SECOND = 1 / 86400  # days
SECTOR = 27.0  # days
T0 = 2459000.0


def sectors(starts: list[float], cadence: float) -> np.ndarray:
    """Times at `cadence` days through a 27-day sector starting at each of `starts`."""
    per_sector = np.arange(0.0, SECTOR, cadence)
    return np.concatenate([T0 + start + per_sector for start in starts])


def tess(
    time: np.ndarray, value: np.ndarray, sigma: float, sap: np.ndarray | None = None
) -> LightCurve:
    return LightCurve(
        survey="tess",
        source_key="1",
        band="T",
        time=time,
        value=value,
        error=np.full(time.size, sigma),
        quality=np.zeros(time.size, dtype=np.int32),
        value_kind="flux",
        meta={},
        sap=sap,
    )


def rrab(phase: np.ndarray) -> np.ndarray:
    """An RRAB-like flux: a fast rise and slow decline, from its first four harmonics."""
    return 1.0 + 0.2 * sum(np.sin(2 * np.pi * k * phase) / k for k in range(1, 5))


def amplitude(lc: LightCurve, period: float) -> float:
    """Semi-amplitude of the least-squares sinusoid at `period`."""
    angle = 2 * np.pi * lc.time / period
    design = np.column_stack([np.ones(lc.time.size), np.sin(angle), np.cos(angle)])
    (_, a, b), *_ = np.linalg.lstsq(design, lc.value, rcond=None)
    return float(np.hypot(a, b))


def test_a_continuous_viewing_zone_curve_finds_the_same_period_binned() -> None:
    rng = np.random.default_rng(2187)
    period = 0.5671
    # Five sectors in a row, then four a year later and four more two years after that.
    starts = [i * SECTOR for i in range(5)]
    starts += [365 + i * SECTOR for i in range(4)] + [730 + i * SECTOR for i in range(4)]
    time = sectors(starts, 120 * SECOND)
    value = rrab(time / period) + rng.normal(0, 0.01, time.size)
    raw = clean(tess(time, value, 0.01))
    binned = bin_tess(raw)

    assert raw.time.size > 250_000
    span = raw.time[-1] - raw.time[0]
    assert binned.time.size <= int(span / TESS_BIN_WIDTH) + 1
    assert binned.time.size == pytest.approx(raw.time.size / 5, rel=0.01)

    found = {}
    for name, lc in (("raw", raw), ("binned", binned)):
        # Lomb-Scargle alone: BLS over the unbinned curve would triple the test's time.
        search = lomb_scargle(lc)
        best = alias_test(lc, candidates(search)).best
        assert search.best is not None and best is not None
        found[name] = (search.best.period, best.period)
    allowed = tolerance(period, span)
    for raw_period, binned_period in zip(found["raw"], found["binned"], strict=True):
        assert binned_period == pytest.approx(raw_period, rel=allowed)
        assert binned_period == pytest.approx(period, rel=allowed)


def test_a_dsct_at_the_shortest_initial_type_period_survives_binning() -> None:
    rng = np.random.default_rng(1694)
    period = 0.06
    time = sectors([0.0, SECTOR], 120 * SECOND)
    value = 1.0 + 0.05 * np.sin(2 * np.pi * time / period) + rng.normal(0, 0.01, time.size)
    raw = clean(tess(time, value, 0.01))
    binned = bin_tess(raw)

    best = lomb_scargle(binned).best
    assert best is not None
    assert best.period == pytest.approx(period, rel=1e-3)
    assert amplitude(binned, period) == pytest.approx(amplitude(raw, period), rel=0.05)


@pytest.mark.parametrize("cadence", [600, 1800])
def test_a_full_frame_curve_with_one_point_per_bin_passes_through(cadence: int) -> None:
    rng = np.random.default_rng(cadence)
    time = sectors([0.0], cadence * SECOND)
    lc = tess(
        time,
        1.0 + rng.normal(0, 0.01, time.size),
        0.01,
        sap=1.0 + rng.normal(0, 0.01, time.size),
    )

    binned = bin_tess(lc)

    for name in ("time", "value", "error", "quality", "sap"):
        assert np.array_equal(getattr(binned, name), getattr(lc, name)), name


def test_a_bin_takes_the_mean_and_the_propagated_error_and_gaps_stay_gaps() -> None:
    step = TESS_BIN_WIDTH / 4
    # Three points in the first bin, one in the second, then nothing for a day, then two.
    time = T0 + np.array([0.0, step, 1.5 * step, 4 * step, 1.0, 1.0 + step])
    value = np.array([1.0, 2.0, 4.0, 5.0, 3.0, 5.0])
    error = np.array([0.3, 0.4, 1.2, 0.5, 0.1, 0.1])
    sap = value * 2
    lc = LightCurve(
        survey="tess",
        source_key="1",
        band="T",
        time=time,
        value=value,
        error=error,
        quality=np.zeros(time.size, dtype=np.int32),
        value_kind="flux",
        meta={"clean": {"n_input": 6}},
        sap=sap,
    )

    binned = bin_tess(lc)

    assert np.allclose(binned.time, [T0 + 2.5 * step / 3, T0 + 4 * step, T0 + 1.0 + step / 2])
    assert np.allclose(binned.value, [7 / 3, 5.0, 4.0])
    assert binned.sap is not None and np.allclose(binned.sap, [14 / 3, 10.0, 8.0])
    assert np.allclose(binned.error, [1.3 / 3, 0.5, np.sqrt(0.02) / 2])
    assert np.array_equal(binned.quality, np.zeros(3))
    assert binned.meta == {
        "clean": {"n_input": 6},
        "bin": {"width_days": TESS_BIN_WIDTH, "n_input": 6},
    }


@pytest.mark.parametrize(("survey", "band"), [("ztf", "g"), ("asassn", "V"), ("gaia", "G")])
def test_only_tess_is_binned_in_analyze(survey: str, band: str) -> None:
    rng = np.random.default_rng(42)
    time = T0 + np.arange(0.0, 3.0, 120 * SECOND)
    value = 15.0 + 0.3 * np.sin(2 * np.pi * time / 0.4) + rng.normal(0, 0.01, time.size)
    other = LightCurve(
        survey=survey,
        source_key="1",
        band=band,
        time=time,
        value=value,
        error=np.full(time.size, 0.01),
        quality=np.zeros(time.size, dtype=np.int32),
        value_kind="mag",
        meta={},
    )

    assert bin_tess(other) is other
    _, cleaned = analyze([other, tess(time, value, 0.01)], None, target_id=1)

    kept, binned = cleaned
    assert kept == clean(other)
    assert binned.time.size < time.size / 4
