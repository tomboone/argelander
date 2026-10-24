"""The light curve's shape at the agreed period, and its variability (spec §7, NEU-1720)."""

from collections.abc import Callable

import numpy as np
import pytest

from argelander.analysis.model import Agreement
from argelander.analysis.shape import magnitudes, shape, variability
from argelander.lightcurve import LightCurve

Profile = Callable[[np.ndarray], np.ndarray]  # phase → magnitudes below the mean level


def curve(
    profile: Profile,
    *,
    period: float = 0.4,
    n: int = 600,
    survey: str = "ztf",
    band: str = "g",
    noise: float = 0.005,
    seed: int = 1,
) -> LightCurve:
    rng = np.random.default_rng(seed)
    time = 2459000.0 + np.sort(rng.uniform(0.0, 300.0, n))
    phase = ((time - time[0]) / period) % 1.0
    return LightCurve(
        survey=survey,
        source_key="1",
        band=band,
        time=time,
        value=15.0 + profile(phase) + rng.normal(0.0, noise, n),
        error=np.full(n, noise),
        quality=np.zeros(n, dtype=np.int32),
        value_kind="mag",
        meta={},
    )


def agreed(period: float = 0.4) -> Agreement:
    return Agreement(
        agreed_period=period, agreed_sources=("ztf_g", "ztf_r"), relation="1:1", tolerance=1e-3
    )


def pulsation(amplitude: float, rise: float) -> Profile:
    """Light rising linearly from minimum to maximum over `rise` of the cycle, then falling
    off fast and levelling out towards minimum, as an RRab star's does."""

    def profile(phase: np.ndarray) -> np.ndarray:
        light = np.where(phase < rise, phase / rise, ((1.0 - phase) / (1.0 - rise)) ** 2)
        return amplitude * (1.0 - light)

    return profile


def sinusoid(amplitude: float) -> Profile:
    return lambda phase: amplitude / 2 * (1.0 - np.cos(2 * np.pi * phase))


def eclipses(depth: float, secondary: float, power: int = 4) -> Profile:
    """Two minima half a cycle apart, the second `secondary` as deep; a higher `power` makes
    them narrower."""

    def profile(phase: np.ndarray) -> np.ndarray:
        dip = np.abs(np.cos(np.pi * phase * 2.0)) ** power
        return depth * np.where((phase < 0.25) | (phase >= 0.75), dip, secondary * dip)

    return profile


def detached(depth: float, secondary: float, width: float = 0.02) -> Profile:
    """Gaussian eclipses on a flat maximum."""

    def profile(phase: np.ndarray) -> np.ndarray:
        def dip(centre: float) -> np.ndarray:
            distance = np.minimum(np.abs(phase - centre), 1.0 - np.abs(phase - centre))
            return np.exp(-0.5 * (distance / width) ** 2)

        return depth * (dip(0.0) + secondary * dip(0.5))

    return profile


def test_no_shape_without_agreement() -> None:
    assert shape([curve(pulsation(1.0, 0.15))], None, ()) is None


def test_no_shape_without_a_band_to_fold() -> None:
    gaia = curve(pulsation(1.0, 0.15), survey="gaia", band="G")
    assert shape([gaia], agreed(), ()) is None
    assert shape([curve(pulsation(1.0, 0.15), n=20)], agreed(), ()) is None


def test_a_steep_rise_is_measured() -> None:
    measured = shape([curve(pulsation(1.0, 0.15))], agreed(), ())
    assert measured is not None
    # Six harmonics round the corners, so the rise comes out a little long.
    assert 0.1 < measured.rise < 0.25
    assert measured.amplitude == pytest.approx(1.0, abs=0.1)
    assert measured.amplitude_v == pytest.approx(0.85 * measured.amplitude)
    assert measured.skewness < -0.5  # sharp maxima
    assert measured.eclipse_width > 0.3
    assert measured.snr > 50


def test_a_symmetric_curve_has_a_half_cycle_rise() -> None:
    measured = shape([curve(sinusoid(0.5))], agreed(), ())
    assert measured is not None
    assert measured.rise == pytest.approx(0.5, abs=0.04)
    assert abs(measured.skewness) < 0.2
    assert measured.r21 < 0.2


def test_contact_binary_minima_are_narrow_and_equal() -> None:
    measured = shape([curve(eclipses(0.7, 0.95))], agreed(), ())
    assert measured is not None
    assert measured.eclipse_width <= 0.22
    assert measured.secondary_depth == pytest.approx(0.95, abs=0.08)
    assert measured.flat_fraction < 0.5


def test_a_detached_binary_has_flat_maxima() -> None:
    measured = shape([curve(detached(1.0, 0.2), period=1.3)], agreed(1.3), ())
    assert measured is not None
    assert measured.eclipse_width <= 0.12
    assert measured.secondary_depth == pytest.approx(0.2, abs=0.08)
    assert measured.flat_fraction > 0.7


def test_the_clean_band_speaks_before_a_clearer_contaminated_one() -> None:
    clean = curve(pulsation(1.0, 0.15), band="r", noise=0.05)
    contaminated = curve(pulsation(1.0, 0.15), survey="asassn", band="V", noise=0.001)
    measured = shape([contaminated, clean], agreed(), {"asassn"})
    assert measured is not None
    assert (measured.survey, measured.band) == ("ztf", "r")


def test_a_magnitude_band_speaks_before_tess_flux() -> None:
    mags = curve(pulsation(1.0, 0.15), noise=0.05)
    flux = curve(pulsation(1.0, 0.15), survey="tess", band="T", noise=0.001)
    flux = LightCurve(
        survey="tess",
        source_key="1",
        band="T",
        time=flux.time,
        value=10 ** (-0.4 * (flux.value - 15.0)),
        error=np.full(flux.time.size, 0.001),
        quality=flux.quality,
        value_kind="flux",
        meta={},
    )
    measured = shape([flux, mags], agreed(), ())
    assert measured is not None
    assert measured.survey == "ztf"
    assert shape([flux], agreed(), ()) is not None


def test_flux_converts_to_magnitudes() -> None:
    lc = LightCurve(
        survey="tess",
        source_key="1",
        band="T",
        time=np.array([1.0, 2.0, 3.0]),
        value=np.array([1.0, 0.1, -0.5]),
        error=np.array([0.01, 0.01, 0.01]),
        quality=np.zeros(3, dtype=np.int32),
        value_kind="flux",
        meta={},
    )
    time, mag, error = magnitudes(lc)
    assert time.tolist() == [1.0, 2.0]
    assert mag == pytest.approx([0.0, 2.5])
    assert error == pytest.approx([0.010857, 0.10857], rel=1e-3)


def test_a_constant_star_spreads_only_as_far_as_its_errors() -> None:
    flat = curve(lambda phase: np.zeros_like(phase), noise=0.01)
    measured = variability([flat], ())
    assert measured is not None
    assert measured.excess == pytest.approx(1.0, abs=0.15)


def test_a_variable_star_spreads_beyond_its_errors() -> None:
    measured = variability([curve(sinusoid(0.5), noise=0.01)], ())
    assert measured is not None
    assert measured.excess > 10


def test_variability_skips_contaminated_surveys() -> None:
    assert variability([curve(sinusoid(0.5))], {"ztf"}) is None
