"""ZTF Light Curves from IRSA's light-curve service, one per band with its oids merged.

Spec §5.1. A position query returns a separate object id (oid) for every field and CCD
quadrant the star fell in, per filter, and each oid carries its own photometric zero point.
Per band, the oid with the most good points is the reference; every other oid is shifted by
the difference between the two medians so the merged curve does not show the field-to-field
offsets as variability. An offset above 0.05 mag is more than a zero point explains, most
likely a neighbour blended into one field's photometry, so the band is flagged
`blend_suspect`. Keeping only the reference oid would avoid the question and throw away half
the epochs.

Only rows with `catflags == 0` are kept; IRSA's own `BAD_CATFLAGS_MASK` is not used, so the
filter is visible here and in the tests.
"""

import csv
import io
import statistics
from collections.abc import Callable
from datetime import UTC, datetime

import numpy as np
import requests

from argelander.analysis.time import ztf_mjd_to_bjd_tdb
from argelander.fetch.base import ArchiveLimits, TargetRef, with_retries
from argelander.lightcurve import LightCurve

IRSA_LIGHT_CURVES_URL = "https://irsa.ipac.caltech.edu/cgi-bin/ZTF/nph_light_curves"
SEARCH_RADIUS_ARCSEC = 1.5
BLEND_OFFSET_MAG = 0.05

# IRSA's filtercode → `LightCurve.band`. The i band is out of scope (spec §5.1).
_BANDS = {"zg": "g", "zr": "r"}
_COLUMNS = frozenset({"oid", "mjd", "mag", "magerr", "catflags", "filtercode"})
# (connect, read) seconds. A bright, well-covered star returns a few MB of CSV.
_TIMEOUT = (10.0, 120.0)


def query_irsa(ra: float, dec: float) -> str:
    """The raw CSV IRSA returns for g and r rows within `SEARCH_RADIUS_ARCSEC` of `ra`, `dec`."""
    response = requests.get(
        IRSA_LIGHT_CURVES_URL,
        params={
            "POS": f"CIRCLE {ra} {dec} {SEARCH_RADIUS_ARCSEC / 3600.0}",
            "BANDNAME": ",".join(_BANDS.values()),
            "FORMAT": "csv",
        },
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    return response.text


def light_curves_from_irsa_csv(
    text: str, ra: float, dec: float, *, fetched_at: str
) -> list[LightCurve]:
    """One Light Curve per band in IRSA's CSV for a Target at ICRS `ra`, `dec` in degrees.

    Bands with no good rows are left out, so a star outside ZTF's footprint gives `[]`.
    """
    reader = csv.DictReader(io.StringIO(text))
    missing = _COLUMNS - set(reader.fieldnames or ())
    if missing:
        # IRSA reports a bad query as a VOTable with HTTP 200, not as an error status.
        raise ValueError(f"IRSA response is not a ZTF light-curve CSV: {text[:300]!r}")

    # filtercode → oid → (mjd, mag, magerr) rows
    groups: dict[str, dict[str, list[tuple[float, float, float]]]] = {}
    for row in reader:
        if row["filtercode"] not in _BANDS or int(row["catflags"]) != 0:
            continue
        groups.setdefault(row["filtercode"], {}).setdefault(row["oid"], []).append(
            (float(row["mjd"]), float(row["mag"]), float(row["magerr"]))
        )

    return [
        _merge_oids(_BANDS[code], groups[code], ra, dec, fetched_at)
        for code in _BANDS
        if code in groups
    ]


def _merge_oids(
    band: str,
    by_oid: dict[str, list[tuple[float, float, float]]],
    ra: float,
    dec: float,
    fetched_at: str,
) -> LightCurve:
    oids = sorted(by_oid)
    # Ties go to the lowest oid, so the choice does not depend on row order.
    reference = max(oids, key=lambda oid: len(by_oid[oid]))
    reference_median = statistics.median(mag for _, mag, _ in by_oid[reference])
    offsets = {
        oid: reference_median - statistics.median(mag for _, mag, _ in by_oid[oid])
        for oid in oids
        if oid != reference
    }

    rows = np.array(
        [
            (mjd, mag + offsets.get(oid, 0.0), magerr)
            for oid in oids
            for mjd, mag, magerr in by_oid[oid]
        ],
        dtype=np.float64,
    )
    time = ztf_mjd_to_bjd_tdb(rows[:, 0], ra, dec)
    order = np.argsort(time, kind="stable")
    return LightCurve(
        survey="ztf",
        source_key="+".join(oids),
        band=band,
        time=time[order],
        value=rows[order, 1],
        error=rows[order, 2],
        quality=np.zeros(len(rows), dtype=np.int32),
        value_kind="mag",
        meta={
            "oids": oids,
            "reference_oid": reference,
            # Magnitudes added to each non-reference oid's points.
            "zero_point_offsets": offsets,
            "blend_suspect": any(abs(o) > BLEND_OFFSET_MAG for o in offsets.values()),
            "time_system": "MJD UTC at Palomar",
            "fetched_at": fetched_at,
        },
    )


class ZtfFetcher:
    """The ZTF `Fetcher`: one IRSA query per Target, under the IRSA semaphore, with retries."""

    survey: str = "ztf"

    def __init__(
        self,
        limits: ArchiveLimits,
        *,
        max_attempts: int,
        query: Callable[[float, float], str] = query_irsa,
    ) -> None:
        self.limits = limits
        self.max_attempts = max_attempts
        self.query = query

    async def fetch(self, target: TargetRef) -> list[LightCurve]:
        text = await with_retries(
            lambda: self.limits.run("irsa", self.query, target.ra, target.dec),
            survey=self.survey,
            max_attempts=self.max_attempts,
        )
        return light_curves_from_irsa_csv(
            text,
            target.ra,
            target.dec,
            fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
