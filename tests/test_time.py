"""Survey time systems → BJD_TDB (spec §6 step 2).

Every conversion is checked against a value derived without the code under test, to 1 s:

- TESS and Gaia against their published definitions (BTJD; IAU 2006 Resolution B3).
- ZTF and ASAS-SN against barycorrpy's published UTC → BJD_TDB reference for τ Ceti
  (Kanodia & Wright 2018, RNAAS 2, 4; test_JDUTC_BJDTDB in its suite), an independent
  implementation of the Wright & Eastman (2014) barycentric correction on JPL DE430.
  ASAS-SN's input HJD comes from the Astronomical Almanac's low-precision Sun, not astropy.

The astropy round trips then check the same functions to a millisecond. The one conversion
the other way, BJD_TDB → HJD for VSX's epoch, is checked against the same references and as
the inverse of the ASAS-SN conversion.
"""

from collections.abc import Callable

import astropy.units as u
import numpy as np
import pytest
from astropy.coordinates import EarthLocation, SkyCoord
from astropy.time import Time

from argelander.analysis.time import (
    asassn_hjd_to_bjd_tdb,
    bjd_tdb_to_hjd,
    gaia_tcb_to_bjd_tdb,
    tess_btjd_to_bjd_tdb,
    ztf_mjd_to_bjd_tdb,
)

ONE_SECOND = 1.0 / 86400.0
ONE_MILLISECOND = ONE_SECOND / 1000.0

# barycorrpy's reference: JD_UTC 2458000.0 for HIP 8102 (τ Ceti) from CTIO → BJD_TDB.
REF_JD_UTC = 2458000.0
REF_BJD_TDB = 2458000.00505211


def tau_ceti_icrs_at(jd: float) -> tuple[float, float]:
    """τ Ceti's ICRS position at `jd`, from its J2000 Hipparcos position and proper motion.

    barycorrpy propagates the proper motion (~2″/yr); ignoring it costs ~0.08 s here.
    """
    ra, dec = 26.0213645867, -15.9395557246
    pmra, pmdec = -1721.05, 854.16  # mas/yr, pmra already × cos(dec)
    years = (jd - 2451545.0) / 365.25
    return (
        ra + pmra * years / 3.6e6 / np.cos(np.radians(dec)),
        dec + pmdec * years / 3.6e6,
    )


def almanac_hjd(jd_utc: float, ra: float, dec: float) -> float:
    """HJD from the Astronomical Almanac's low-precision solar coordinates (good to 0.01°).

    Precessed back to the J2000 ecliptic so the Sun and the ICRS Target direction share axes. The
    position error is worth under 0.2 s of light travel.
    """
    n = jd_utc - 2451545.0
    g = np.radians(357.528 + 0.9856003 * n)
    mean_longitude = 280.460 + 0.9856474 * n
    precession = 5028.8 / 3600.0 * n / 36525.0
    lam = np.radians(mean_longitude + 1.915 * np.sin(g) + 0.020 * np.sin(2 * g) - precession)
    r_au = 1.00014 - 0.01671 * np.cos(g) - 0.00014 * np.cos(2 * g)
    eps = np.radians(23.439281)
    sun = r_au * np.array([np.cos(lam), np.cos(eps) * np.sin(lam), np.sin(eps) * np.sin(lam)])
    ra_r, dec_r = np.radians(ra), np.radians(dec)
    direction = np.array(
        [np.cos(dec_r) * np.cos(ra_r), np.cos(dec_r) * np.sin(ra_r), np.sin(dec_r)]
    )
    light_seconds_per_au = 499.004784
    # The Sun sits opposite the Earth's heliocentric position.
    return jd_utc - float(sun @ direction) * light_seconds_per_au / 86400.0


def iau_2006_tcb_to_tdb(jd_tcb: np.ndarray) -> np.ndarray:
    """IAU 2006 Resolution B3: TDB = TCB − L_B (JD_TCB − T0) 86400 s + TDB0."""
    l_b, t0, tdb0_seconds = 1.550519768e-8, 2443144.5003725, -6.55e-5
    return jd_tcb - l_b * (jd_tcb - t0) + tdb0_seconds / 86400.0


def test_ztf_matches_barycorrpy_reference() -> None:
    ra, dec = tau_ceti_icrs_at(REF_JD_UTC)

    bjd = ztf_mjd_to_bjd_tdb(np.array([REF_JD_UTC - 2400000.5]), ra, dec)

    # Palomar vs CTIO moves the correction by under 0.05 s.
    assert bjd[0] == pytest.approx(REF_BJD_TDB, abs=ONE_SECOND)


def test_asassn_matches_barycorrpy_reference() -> None:
    ra, dec = tau_ceti_icrs_at(REF_JD_UTC)
    hjd = almanac_hjd(REF_JD_UTC, ra, dec)

    bjd = asassn_hjd_to_bjd_tdb(np.array([hjd]), ra, dec)

    assert bjd[0] == pytest.approx(REF_BJD_TDB, abs=ONE_SECOND)


def test_hjd_matches_barycorrpy_reference() -> None:
    ra, dec = tau_ceti_icrs_at(REF_JD_UTC)

    hjd = bjd_tdb_to_hjd(np.array([REF_BJD_TDB]), ra, dec)

    assert hjd[0] == pytest.approx(almanac_hjd(REF_JD_UTC, ra, dec), abs=ONE_SECOND)


def test_tess_is_btjd_plus_2457000() -> None:
    # TESS Science Data Products Description Document: BTJD = BJD_TDB − 2457000.0.
    btjd = np.array([0.0, 1325.29, 3135.755972])

    np.testing.assert_allclose(
        tess_btjd_to_bjd_tdb(btjd), btjd + 2457000.0, rtol=0, atol=ONE_SECOND
    )


def test_gaia_matches_iau_2006_tcb_to_tdb() -> None:
    # Spans Gaia DR3's epoch photometry, 2014-07-25 to 2017-05-28.
    t = np.array([1666.4, 2100.0, 2700.9])

    expected = iau_2006_tcb_to_tdb(t + 2455197.5)

    np.testing.assert_allclose(gaia_tcb_to_bjd_tdb(t), expected, rtol=0, atol=ONE_SECOND)
    # The TCB → TDB step is real: ~20 s here, so skipping it fails the 1 s budget.
    assert np.all(np.abs(t + 2455197.5 - expected) > 15 * ONE_SECOND)


def test_tess_and_ztf_agree_on_the_same_instant() -> None:
    ra, dec = 268.5, 61.2  # Draco
    mjd_utc = np.array([59010.25, 59011.75, 59400.1])
    t = Time(
        mjd_utc,
        format="mjd",
        scale="utc",
        location=EarthLocation.from_geocentric(0, 0, 0, unit=u.m),
    )
    target = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame="icrs")
    btjd = (t.tdb + t.light_travel_time(target, ephemeris="builtin")).jd - 2457000.0

    np.testing.assert_allclose(
        tess_btjd_to_bjd_tdb(btjd), ztf_mjd_to_bjd_tdb(mjd_utc, ra, dec), rtol=0, atol=ONE_SECOND
    )


def test_asassn_inverts_astropys_heliocentric_correction() -> None:
    ra, dec = 268.5, 61.2
    geocentre = EarthLocation.from_geocentric(0, 0, 0, unit=u.m)
    t = Time(
        np.array([2457000.3, 2458500.8, 2460300.1]), format="jd", scale="utc", location=geocentre
    )
    target = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame="icrs")
    hjd = t.jd + t.light_travel_time(target, kind="heliocentric", ephemeris="builtin").to_value(
        u.day
    )
    bjd = (t.tdb + t.light_travel_time(target, kind="barycentric", ephemeris="builtin")).jd

    np.testing.assert_allclose(
        asassn_hjd_to_bjd_tdb(hjd, ra, dec), bjd, rtol=0, atol=ONE_MILLISECOND
    )


def test_hjd_round_trips_the_asassn_conversion() -> None:
    ra, dec = 268.5, 61.2
    hjd = np.array([2457000.3, 2458500.8, 2460300.1])

    np.testing.assert_allclose(
        bjd_tdb_to_hjd(asassn_hjd_to_bjd_tdb(hjd, ra, dec), ra, dec),
        hjd,
        rtol=0,
        atol=ONE_MILLISECOND,
    )


def test_hjd_is_the_bjd_less_about_tdb_minus_utc() -> None:
    # Near the ecliptic pole the heliocentric and barycentric corrections differ by only the
    # Sun's offset from the barycentre, so the gap is mostly TDB − UTC (69.184 s since 2017).
    hjd = bjd_tdb_to_hjd(2460000.5, 270.0, 66.56)

    assert float(hjd) == pytest.approx(2460000.5 - 69.184 / 86400.0, abs=5 * ONE_SECOND)


def test_gaia_inverts_astropys_tdb_to_tcb() -> None:
    tdb = Time(np.array([2457000.3, 2457500.8]), format="jd", scale="tdb")

    t = tdb.tcb.jd - 2455197.5

    np.testing.assert_allclose(gaia_tcb_to_bjd_tdb(t), tdb.jd, rtol=0, atol=ONE_MILLISECOND)


@pytest.mark.parametrize(
    "convert",
    [
        lambda t: ztf_mjd_to_bjd_tdb(t + 57000.0, 268.5, 61.2),
        lambda t: tess_btjd_to_bjd_tdb(t + 1500.0),
        lambda t: gaia_tcb_to_bjd_tdb(t + 2000.0),
        lambda t: asassn_hjd_to_bjd_tdb(t + 2457000.0, 268.5, 61.2),
        lambda t: bjd_tdb_to_hjd(t + 2457000.0, 268.5, 61.2),
    ],
    ids=["ztf", "tess", "gaia", "asassn", "hjd"],
)
def test_conversions_take_and_return_float64_arrays(
    convert: Callable[[np.ndarray], np.ndarray],
) -> None:
    t = np.linspace(0.0, 30.0, 7)

    out = convert(t)

    assert isinstance(out, np.ndarray)
    assert out.dtype == np.float64
    assert out.shape == t.shape
    assert np.all(np.diff(out) > 0)
