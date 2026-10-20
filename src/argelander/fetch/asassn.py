"""ASAS-SN Light Curves from Sky Patrol v2, one for V and one for g.

Spec §5.1. ASAS-SN's value is its baseline: a decade of V and g, long enough to confirm the
slow variables and long periods ZTF alone cannot. Its cadence rarely settles a short period
on its own.

This module talks to Sky Patrol's HTTP API with `requests` rather than through `pyasassn`
(the `skypatrol` package, 0.6.21 as of 2025-10-22). The client is the API's only documented
consumer and is the reference for the calls here, but it cannot run inside a fetch worker:
its constructor makes four requests before any query, none of its requests has a timeout, it
downloads through a `multiprocessing.Pool`, and it retries in a bare `except:` loop that
would hide 429s and 5xxs from `with_retries`. It also brings pandas and matplotlib. The two
calls it makes for a cone search are simple to make directly:

1. `POST /lookup_cone/radius{deg}_ra{deg}_dec{deg}` on the load balancer, with
   `download: true`, returns the cone's targets from the `master_list` input catalog as
   Parquet. An empty table means ASAS-SN has no target there.
2. `GET /get_block/query_hash-{hash}-block_idx-0-catalog-master_list` on one of the light
   curve servers that `GET /get_block_servers` names returns every target's photometry as
   Parquet. The hash is the base64 of the cone query's own description, built exactly as
   the client builds it. A 3″ cone holds far fewer than the 1,000 targets in a block.

An empty server list means the light-curve service is down for maintenance (it was from
2026-10-13); that is `SkyPatrolUnavailable`, a `ConnectionError`, so the fetch is retried.

Of the targets in the cone, the one nearest the Target is kept. A point is kept when Sky
Patrol graded its image good (`quality == "G"`) and it is a detection: a non-detection is
reported as an upper limit at the image's limiting magnitude with `mag_err` 99.99, so this
one test drops both. The band comes from the camera, by the client's own camera lists; a
camera on neither list is dropped, as the client drops it.

Times are HJD in UTC from a geocentric observer (the Sky Patrol `jd` column) and become
BJD_TDB here. Magnitudes are V from the V cameras and g from the g cameras, both calibrated
against APASS.
"""

import io
import logging
from base64 import encodebytes
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

import astropy.units as u
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import requests
from astropy.coordinates import SkyCoord

from argelander.analysis.time import asassn_hjd_to_bjd_tdb
from argelander.fetch.base import ArchiveLimits, TargetRef, with_retries
from argelander.lightcurve import LightCurve

logger = logging.getLogger(__name__)

SKY_PATROL_PORT = 9006
SKY_PATROL_URL = f"http://asassn-lb01.ifa.hawaii.edu:{SKY_PATROL_PORT}"
CATALOG = "master_list"
SEARCH_RADIUS_ARCSEC = 3.0
# `mag_err` at or above this marks a non-detection, reported as an upper limit.
NON_DETECTION_MAG_ERR = 99.0

# From `pyasassn.utils`: the original V-band units and the g-band units that replaced them.
V_CAMERAS = frozenset({"ba", "bb", "bc", "bd", "be", "bf", "bg", "bh"})
G_CAMERAS = frozenset(
    {
        *("bA", "bB", "bC", "bD", "bE", "bF", "bG", "bH", "cB"),
        *("bi", "bj", "bk", "bl", "bm", "bn", "bo", "bp", "bq", "br", "bs", "bt"),
    }
)
_BANDS = (("V", V_CAMERAS), ("g", G_CAMERAS))

# The client's default columns for `master_list`; they are also part of the query hash.
_INDEX_COLUMNS = ("asas_sn_id", "ra_deg", "dec_deg", "catalog_sources")
_LIGHT_CURVE_COLUMNS = ("asas_sn_id", "jd", "mag", "mag_err", "camera", "quality")
# (connect, read) seconds. A 3″ cone is one target with a few thousand epochs.
_TIMEOUT = (10.0, 120.0)


class SkyPatrolUnavailable(ConnectionError):
    """Sky Patrol names no light-curve servers: the service is down for maintenance."""


@dataclass(frozen=True)
class SkyPatrolResponse:
    """The two Parquet bodies one cone search returns."""

    index: bytes  # the cone's targets
    light_curves: bytes | None  # their photometry; None when the index is empty


def query_sky_patrol(ra: float, dec: float) -> SkyPatrolResponse:
    """Sky Patrol's targets within `SEARCH_RADIUS_ARCSEC` of `ra`, `dec` and their photometry."""
    radius = SEARCH_RADIUS_ARCSEC / 3600.0
    response = requests.post(
        f"{SKY_PATROL_URL}/lookup_cone/radius{radius}_ra{ra}_dec{dec}",
        json={
            "catalog": CATALOG,
            "cols": list(_INDEX_COLUMNS),
            "format": "arrow",
            "download": True,
        },
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    index = response.content
    if _read_table(index, _INDEX_COLUMNS, "target index").num_rows == 0:
        return SkyPatrolResponse(index=index, light_curves=None)

    servers_response = requests.get(f"{SKY_PATROL_URL}/get_block_servers", timeout=_TIMEOUT)
    servers_response.raise_for_status()
    servers: list[str] = servers_response.json()
    if not servers:
        raise SkyPatrolUnavailable("Sky Patrol lists no light-curve servers")

    query = f"conectr-{radius}_conera-{ra}_conedec-{dec}|catalog-{CATALOG}|cols-" + "/".join(
        _INDEX_COLUMNS
    )
    # `encodebytes`, newlines and all, because that is the hash the client sends and so the
    # one the server has been tested against.
    query_hash = encodebytes(query.encode()).decode()
    # As the client does: start at the first server and move on whenever one fails, whether
    # with an HTTP error or a body that is not the block. Only the last server's failure
    # reaches `with_retries`.
    for server in servers[:-1]:
        try:
            return SkyPatrolResponse(index=index, light_curves=_get_block(server, query_hash))
        except (requests.RequestException, ValueError) as error:
            logger.warning(
                "asassn: light-curve server %s failed (%r); trying the next", server, error
            )
    return SkyPatrolResponse(index=index, light_curves=_get_block(servers[-1], query_hash))


def _get_block(server: str, query_hash: str) -> bytes:
    response = requests.get(
        f"http://{server}:{SKY_PATROL_PORT}/get_block/"
        f"query_hash-{query_hash}-block_idx-0-catalog-{CATALOG}",
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    _read_table(response.content, _LIGHT_CURVE_COLUMNS, "light-curve table")
    return response.content


def light_curves_from_sky_patrol(
    response: SkyPatrolResponse, ra: float, dec: float, *, fetched_at: str
) -> list[LightCurve]:
    """The V and g Light Curves of the Sky Patrol target nearest ICRS `ra`, `dec` in degrees.

    Bands with no good detections are left out, so an empty cone gives `[]`.
    """
    index = _read_table(response.index, _INDEX_COLUMNS, "target index")
    if index.num_rows == 0:
        return []
    if response.light_curves is None:
        raise ValueError("Sky Patrol listed targets but no light curves were fetched")

    target = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame="icrs")
    cone_targets = SkyCoord(
        ra=index["ra_deg"].to_numpy() * u.deg, dec=index["dec_deg"].to_numpy() * u.deg, frame="icrs"
    )
    separations = target.separation(cone_targets).to_value(u.arcsec)
    nearest = int(np.nanargmin(separations))
    asas_sn_id = int(index["asas_sn_id"][nearest].as_py())
    catalog_sources = index["catalog_sources"][nearest].as_py() or []

    rows = _read_table(response.light_curves, _LIGHT_CURVE_COLUMNS, "light-curve table")
    columns = {name: rows[name].to_numpy(zero_copy_only=False) for name in _LIGHT_CURVE_COLUMNS}
    jd = columns["jd"].astype(np.float64)
    mag = columns["mag"].astype(np.float64)
    mag_err = columns["mag_err"].astype(np.float64)
    camera = columns["camera"]
    good = (
        (columns["asas_sn_id"] == asas_sn_id)
        & (columns["quality"] == "G")
        & np.isfinite(jd)
        & np.isfinite(mag)
        & np.isfinite(mag_err)
        & (mag_err < NON_DETECTION_MAG_ERR)
    )

    curves = []
    for band, cameras in _BANDS:
        selected = good & np.isin(camera, sorted(cameras))
        if not selected.any():
            continue
        time = asassn_hjd_to_bjd_tdb(jd[selected], ra, dec)
        order = np.argsort(time, kind="stable")
        curves.append(
            LightCurve(
                survey="asassn",
                source_key=str(asas_sn_id),
                band=band,
                time=time[order],
                value=mag[selected][order],
                error=mag_err[selected][order],
                quality=np.zeros(int(selected.sum()), dtype=np.int32),
                value_kind="mag",
                meta={
                    "asas_sn_id": asas_sn_id,
                    "separation_arcsec": float(separations[nearest]),
                    "catalog_sources": catalog_sources,
                    "cameras": sorted(set(camera[selected].tolist())),
                    "time_system": "HJD UTC, geocentric",
                    "fetched_at": fetched_at,
                },
            )
        )
    return curves


def _read_table(data: bytes, columns: Iterable[str], what: str) -> pa.Table:
    try:
        table = pq.read_table(io.BytesIO(data))
    except pa.ArrowInvalid as error:
        # Sky Patrol answers some bad requests with an HTML page and HTTP 200.
        raise ValueError(f"Sky Patrol {what} is not Parquet: {data[:300]!r}") from error
    missing = set(columns) - set(table.column_names)
    if missing:
        raise ValueError(f"Sky Patrol {what} lacks columns {sorted(missing)}")
    return table


class AsassnFetcher:
    """The ASAS-SN `Fetcher`: one Sky Patrol cone search per Target, under the ASAS-SN
    semaphore, with retries."""

    survey: str = "asassn"

    def __init__(
        self,
        limits: ArchiveLimits,
        *,
        max_attempts: int,
        query: Callable[[float, float], SkyPatrolResponse] = query_sky_patrol,
    ) -> None:
        self.limits = limits
        self.max_attempts = max_attempts
        self.query = query

    async def fetch(self, target: TargetRef) -> list[LightCurve]:
        response = await with_retries(
            lambda: self.limits.run("asassn", self.query, target.ra, target.dec),
            survey=self.survey,
            max_attempts=self.max_attempts,
        )
        return light_curves_from_sky_patrol(
            response,
            target.ra,
            target.dec,
            fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
