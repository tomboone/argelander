"""Every survey's time system → BJD_TDB, the one scale Light Curves are folded on.

Spec §6 step 2. Fetchers call these at the boundary, so `LightCurve.time` is always BJD_TDB.
The four surveys report:

- ZTF: MJD in UTC, observed from Palomar (topocentric).
- TESS: BTJD = BJD_TDB − 2457000, already barycentric.
- Gaia DR3 epoch photometry: barycentric JD in TCB − 2455197.5.
- ASAS-SN: HJD in UTC (heliocentric, geocentric observer).

Each function takes a scalar or an array of times and returns float64 JDs of the same shape.
The barycentric corrections use astropy's built-in ERFA ephemeris, which needs no download
and is accurate to tens of microseconds, far inside the 1 s the analysis needs.
"""

import astropy.units as u
import numpy as np
import numpy.typing as npt
from astropy.coordinates import EarthLocation, SkyCoord
from astropy.time import Time

TESS_BTJD_ZERO = 2457000.0
GAIA_TIME_ZERO = 2455197.5

# ZTF's P48 at Palomar (MPC code I41). The observer's position moves the barycentric
# correction by at most an Earth radius of light travel (21 ms), so a kilometre here is noise.
_PALOMAR = EarthLocation.from_geodetic(
    lon=-116.8639 * u.deg, lat=33.3574 * u.deg, height=1700 * u.m
)
# ASAS-SN observes from several sites and publishes geocentric HJDs, so undo and redo the
# correction from the geocentre.
_GEOCENTRE = EarthLocation.from_geocentric(0.0, 0.0, 0.0, unit=u.m)
_EPHEMERIS = "builtin"


def ztf_mjd_to_bjd_tdb(mjd: npt.ArrayLike, ra: float, dec: float) -> np.ndarray:
    """ZTF MJD (UTC, Palomar) → BJD_TDB for a Target at ICRS `ra`, `dec` in degrees."""
    t = Time(np.asarray(mjd, dtype=np.float64), format="mjd", scale="utc", location=_PALOMAR)
    return _utc_to_bjd_tdb(t, _icrs(ra, dec))


def tess_btjd_to_bjd_tdb(btjd: npt.ArrayLike) -> np.ndarray:
    """TESS BTJD → BJD_TDB. BTJD is already barycentric TDB, offset by 2457000."""
    return np.asarray(btjd, dtype=np.float64) + TESS_BTJD_ZERO


def gaia_tcb_to_bjd_tdb(t: npt.ArrayLike) -> np.ndarray:
    """Gaia DR3 epoch time (barycentric JD in TCB − 2455197.5) → BJD_TDB.

    Already barycentric; only the time scale changes. TCB runs ahead of TDB by about
    1.55e-8 of elapsed time, which is roughly 20 s by Gaia DR3's epochs.
    """
    tcb = Time(GAIA_TIME_ZERO, np.asarray(t, dtype=np.float64), format="jd", scale="tcb")
    return np.asarray(tcb.tdb.jd, dtype=np.float64)


def asassn_hjd_to_bjd_tdb(hjd: npt.ArrayLike, ra: float, dec: float) -> np.ndarray:
    """ASAS-SN HJD (UTC) → BJD_TDB for a Target at ICRS `ra`, `dec` in degrees.

    Removes the heliocentric correction to recover the UTC JD, then applies the barycentric
    one. HJD = JD + Δ(JD), and Δ changes by under 0.1 s across its own ~8 minute size, so two
    fixed-point steps recover the JD to well under a millisecond.
    """
    target = _icrs(ra, dec)
    hjd = np.asarray(hjd, dtype=np.float64)
    jd = hjd
    for _ in range(2):
        t = Time(jd, format="jd", scale="utc", location=_GEOCENTRE)
        delta = t.light_travel_time(target, kind="heliocentric", ephemeris=_EPHEMERIS)
        jd = hjd - delta.to_value(u.day)
    return _utc_to_bjd_tdb(Time(jd, format="jd", scale="utc", location=_GEOCENTRE), target)


def _icrs(ra: float, dec: float) -> SkyCoord:
    return SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame="icrs")


def _utc_to_bjd_tdb(t: Time, target: SkyCoord) -> np.ndarray:
    delta = t.light_travel_time(target, kind="barycentric", ephemeris=_EPHEMERIS)
    return np.asarray((t.tdb + delta).jd, dtype=np.float64)
