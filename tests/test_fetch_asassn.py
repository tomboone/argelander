"""The ASAS-SN fetcher: target choice, V/g split, cleaning and BJD_TDB (spec §5.1).

The offline tests replay a Sky Patrol response built here in the schema the service
documents (`pyasassn` docs, "Individual Light Curves") and returns: a cone index of
`asas_sn_id`, `ra_deg`, `dec_deg`, `catalog_sources`, and per-epoch rows of `asas_sn_id`,
`jd`, `flux`, `flux_err`, `mag`, `mag_err`, `limit`, `fwhm`, `image_id`, `camera`,
`quality`. They are constructed, not recorded: the light-curve service was down for
maintenance when this fetcher was written (2026-10-19), though the cone search answered and
resolved CSS_J173907.6+550053 to `asas_sn_id` 223338434361, whose position the index uses.
A recorded response replaces this one in NEU-1764.
"""

import asyncio
import io
from base64 import decodebytes
from types import SimpleNamespace
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import requests

from argelander.analysis.time import asassn_hjd_to_bjd_tdb
from argelander.fetch import asassn
from argelander.fetch.asassn import (
    AsassnFetcher,
    SkyPatrolResponse,
    SkyPatrolUnavailable,
    light_curves_from_sky_patrol,
    query_sky_patrol,
)
from argelander.fetch.base import ArchiveLimits, Fetcher, TargetRef, is_transient
from argelander.lightcurve import LightCurve

TARGET = TargetRef(
    gaia_source_id=1418010841782026880,
    ra=264.78197,
    dec=55.01459,
    vsx_name="CSS_J173907.6+550053",
)
TARGET_ID = 223338434361
# A second target 2.5″ north, inside the 3″ cone.
NEIGHBOUR_ID = 223338434999

# (asas_sn_id, jd, mag, mag_err, camera, quality); unsorted, as Sky Patrol returns them.
ROWS = [
    (TARGET_ID, 2457100.80, 14.62, 0.031, "bd", "G"),  # V
    (TARGET_ID, 2456800.10, 14.95, 0.040, "ba", "G"),  # V
    (TARGET_ID, 2456900.40, 14.71, 0.035, "bd", "B"),  # V, bad image
    (TARGET_ID, 2457000.60, 17.10, 99.99, "ba", "G"),  # V, non-detection at the limit
    (TARGET_ID, 2459200.30, 15.12, 0.052, "bG", "G"),  # g
    (TARGET_ID, 2458500.70, 14.88, 0.044, "bi", "G"),  # g
    (TARGET_ID, 2459000.20, 15.30, 0.061, "cB", "G"),  # g
    (TARGET_ID, 2458700.90, 17.40, 99.99, "bG", "G"),  # g, non-detection
    (TARGET_ID, 2458800.50, 15.01, 0.050, "bo", "B"),  # g, bad image
    (TARGET_ID, 2458600.50, 15.05, 0.050, "zz", "G"),  # unknown camera
    (NEIGHBOUR_ID, 2458550.50, 16.20, 0.090, "bi", "G"),  # g, the other target
]
GOOD_V = [(2456800.10, 14.95, 0.040), (2457100.80, 14.62, 0.031)]
GOOD_G = [(2458500.70, 14.88, 0.044), (2459000.20, 15.30, 0.061), (2459200.30, 15.12, 0.052)]


def parquet(table: pa.Table) -> bytes:
    buffer = io.BytesIO()
    pq.write_table(table, buffer)
    return buffer.getvalue()


def index_bytes(*targets: tuple[int, float, float, list[str]]) -> bytes:
    ids, ras, decs, sources = zip(*targets, strict=True) if targets else ((), (), (), ())
    return parquet(
        pa.table(
            {
                "asas_sn_id": pa.array(ids, pa.int64()),
                "ra_deg": pa.array(ras, pa.float64()),
                "dec_deg": pa.array(decs, pa.float64()),
                "catalog_sources": pa.array(sources, pa.list_(pa.string())),
            }
        )
    )


def light_curve_bytes(rows: list[tuple[Any, ...]]) -> bytes:
    ids, jd, mag, mag_err, camera, quality = zip(*rows, strict=True)
    n = len(rows)
    return parquet(
        pa.table(
            {
                "asas_sn_id": pa.array(ids, pa.int64()),
                "jd": pa.array(jd, pa.float64()),
                "flux": pa.array([1.0] * n, pa.float64()),
                "flux_err": pa.array([0.05] * n, pa.float64()),
                "mag": pa.array(mag, pa.float64()),
                "mag_err": pa.array(mag_err, pa.float64()),
                "limit": pa.array([17.5] * n, pa.float64()),
                "fwhm": pa.array([1.5] * n, pa.float64()),
                "image_id": pa.array([f"{c}{i:06d}" for i, c in enumerate(camera)]),
                "camera": pa.array(camera, pa.string()),
                "quality": pa.array(quality, pa.string()),
            }
        )
    )


TARGET_ENTRY = (TARGET_ID, 264.78197118, 55.014594, ["stellar_main", "tic", "aavsovsx"])
NEIGHBOUR_ENTRY = (NEIGHBOUR_ID, 264.78197, 55.01459 + 2.5 / 3600, ["stellar_main"])
RECORDED = SkyPatrolResponse(
    index=index_bytes(NEIGHBOUR_ENTRY, TARGET_ENTRY), light_curves=light_curve_bytes(ROWS)
)


def fetch_recorded(response: SkyPatrolResponse = RECORDED) -> dict[str, LightCurve]:
    fetcher = AsassnFetcher(
        ArchiveLimits({"asassn": 1}), max_attempts=1, query=lambda ra, dec: response
    )
    return {lc.band: lc for lc in asyncio.run(fetcher.fetch(TARGET))}


def test_asassn_fetcher_satisfies_the_fetcher_protocol() -> None:
    fetcher: Fetcher = AsassnFetcher(ArchiveLimits({"asassn": 1}), max_attempts=1)

    assert fetcher.survey == "asassn"


def test_the_target_nearest_the_position_gives_one_v_and_one_g_light_curve() -> None:
    curves = fetch_recorded()

    assert sorted(curves) == ["V", "g"]
    for lc in curves.values():
        assert lc.survey == "asassn"
        assert lc.value_kind == "mag"
        assert lc.source_key == str(TARGET_ID)
        assert lc.meta["asas_sn_id"] == TARGET_ID
        assert lc.meta["catalog_sources"] == ["stellar_main", "tic", "aavsovsx"]
        assert lc.meta["separation_arcsec"] < 0.1
        assert np.all(lc.quality == 0)
    assert curves["V"].meta["cameras"] == ["ba", "bd"]
    assert curves["g"].meta["cameras"] == ["bG", "bi", "cB"]


@pytest.mark.parametrize(("band", "good"), [("V", GOOD_V), ("g", GOOD_G)])
def test_only_good_detections_survive_in_bjd_tdb_and_time_order(
    band: str, good: list[tuple[float, float, float]]
) -> None:
    lc = fetch_recorded()[band]
    hjd, mag, mag_err = (np.array(column) for column in zip(*good, strict=True))

    np.testing.assert_allclose(
        lc.time, asassn_hjd_to_bjd_tdb(hjd, TARGET.ra, TARGET.dec), rtol=0, atol=1e-9
    )
    # HJD → BJD_TDB is TDB−UTC (~67–69 s here) plus a heliocentric-to-barycentric shift of
    # seconds at most.
    assert np.all(np.abs(lc.time - hjd) < 90.0 / 86400.0)
    np.testing.assert_array_equal(lc.value, mag)
    np.testing.assert_array_equal(lc.error, mag_err)
    assert lc.meta["time_system"] == "HJD UTC, geocentric"


def test_bad_images_non_detections_unknown_cameras_and_other_targets_are_dropped() -> None:
    curves = fetch_recorded()

    assert sum(len(lc.time) for lc in curves.values()) == len(GOOD_V) + len(GOOD_G)
    assert all(np.all(lc.error < 99) for lc in curves.values())
    assert 16.20 not in curves["g"].value  # the neighbour's point


def test_the_nearer_of_two_targets_is_kept_whichever_order_they_arrive_in() -> None:
    response = SkyPatrolResponse(
        index=index_bytes(TARGET_ENTRY, NEIGHBOUR_ENTRY), light_curves=RECORDED.light_curves
    )

    curves = fetch_recorded(response)

    assert {lc.source_key for lc in curves.values()} == {str(TARGET_ID)}


def test_a_band_with_no_good_points_is_left_out() -> None:
    v_only = [row for row in ROWS if row[4] in ("ba", "bd")]
    response = SkyPatrolResponse(
        index=index_bytes(TARGET_ENTRY), light_curves=light_curve_bytes(v_only)
    )

    assert list(fetch_recorded(response)) == ["V"]


def test_an_empty_cone_means_no_asassn_data() -> None:
    assert fetch_recorded(SkyPatrolResponse(index=index_bytes(), light_curves=None)) == {}


@pytest.mark.parametrize(
    "response",
    [
        SkyPatrolResponse(index=b"<!DOCTYPE HTML><title>404 Not Found</title>", light_curves=None),
        SkyPatrolResponse(index=index_bytes(TARGET_ENTRY), light_curves=b"<html></html>"),
        SkyPatrolResponse(
            index=index_bytes(TARGET_ENTRY), light_curves=parquet(pa.table({"jd": [1.0]}))
        ),
        SkyPatrolResponse(index=index_bytes(TARGET_ENTRY), light_curves=None),
    ],
    ids=["html-index", "html-light-curves", "missing-columns", "no-light-curves"],
)
def test_a_response_that_is_not_sky_patrol_parquet_is_an_error(
    response: SkyPatrolResponse,
) -> None:
    with pytest.raises(ValueError, match="Sky Patrol"):
        light_curves_from_sky_patrol(response, TARGET.ra, TARGET.dec, fetched_at="2026-10-19")


class FakeSkyPatrol:
    """`requests.post` and `requests.get` for the three Sky Patrol endpoints.

    `failing` maps a light-curve server to how it fails: "refused" (a connection error),
    "not-found" (HTTP 404) or "html" (an HTML page with HTTP 200).
    """

    def __init__(
        self, index: bytes, servers: list[str], failing: dict[str, str] | None = None
    ) -> None:
        self.index = index
        self.servers = servers
        self.failing = failing or {}
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def post(self, url: str, **kwargs: object) -> SimpleNamespace:
        self.calls.append(("POST", url, kwargs))
        return SimpleNamespace(content=self.index, raise_for_status=lambda: None)

    def get(self, url: str, **kwargs: object) -> SimpleNamespace:
        self.calls.append(("GET", url, kwargs))
        if url.endswith("/get_block_servers"):
            return SimpleNamespace(json=lambda: self.servers, raise_for_status=lambda: None)
        host = url.removeprefix("http://").split(":")[0]
        match self.failing.get(host):
            case "refused":
                raise requests.ConnectionError(f"{host} refused the connection")
            case "not-found":
                error = requests.HTTPError(
                    "404 Not Found", response=SimpleNamespace(status_code=404)
                )
                return SimpleNamespace(content=b"", raise_for_status=lambda: _raise(error))
            case "html":
                return SimpleNamespace(content=b"<html></html>", raise_for_status=lambda: None)
        return SimpleNamespace(content=RECORDED.light_curves, raise_for_status=lambda: None)

    def block_hosts(self) -> list[str]:
        """The light-curve servers asked for a block, in order."""
        return [
            url.removeprefix("http://").split(":")[0]
            for method, url, _ in self.calls
            if method == "GET" and "/get_block/" in url
        ]


def _raise(error: Exception) -> None:
    raise error


def install(monkeypatch: pytest.MonkeyPatch, fake: FakeSkyPatrol) -> None:
    monkeypatch.setattr(asassn.requests, "post", fake.post)
    monkeypatch.setattr(asassn.requests, "get", fake.get)


def test_query_searches_three_arcsec_and_fetches_the_block_the_client_would(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeSkyPatrol(index_bytes(TARGET_ENTRY), servers=["asassn-db01.example"])
    install(monkeypatch, fake)

    response = query_sky_patrol(264.78197, 55.01459)

    assert response.light_curves == RECORDED.light_curves
    (cone, servers, block) = fake.calls
    method, url, kwargs = cone
    assert method == "POST"
    radius = 3.0 / 3600.0
    assert url == (
        f"http://asassn-lb01.ifa.hawaii.edu:9006/lookup_cone/radius{radius}_ra264.78197_dec55.01459"
    )
    assert kwargs["json"] == {
        "catalog": "master_list",
        "cols": ["asas_sn_id", "ra_deg", "dec_deg", "catalog_sources"],
        "format": "arrow",
        "download": True,
    }
    assert servers[1] == "http://asassn-lb01.ifa.hawaii.edu:9006/get_block_servers"
    method, url, _ = block
    prefix = "http://asassn-db01.example:9006/get_block/query_hash-"
    suffix = "-block_idx-0-catalog-master_list"
    assert url.startswith(prefix) and url.endswith(suffix)
    query_hash = url.removeprefix(prefix).removesuffix(suffix)
    assert decodebytes(query_hash.encode()).decode() == (
        f"conectr-{radius}_conera-264.78197_conedec-55.01459|catalog-master_list"
        "|cols-asas_sn_id/ra_deg/dec_deg/catalog_sources"
    )
    assert all(call[2]["timeout"] is not None for call in fake.calls)


@pytest.mark.parametrize("failure", ["refused", "not-found", "html"])
def test_query_moves_to_the_next_server_when_one_fails(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    fake = FakeSkyPatrol(
        index_bytes(TARGET_ENTRY), servers=["db01", "db02"], failing={"db01": failure}
    )
    install(monkeypatch, fake)

    assert query_sky_patrol(TARGET.ra, TARGET.dec).light_curves == RECORDED.light_curves
    assert fake.block_hosts() == ["db01", "db02"]


def test_the_last_servers_failure_is_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSkyPatrol(
        index_bytes(TARGET_ENTRY),
        servers=["db01", "db02"],
        failing={"db01": "html", "db02": "html"},
    )
    install(monkeypatch, fake)

    with pytest.raises(ValueError, match="light-curve table is not Parquet"):
        query_sky_patrol(TARGET.ra, TARGET.dec)


def test_query_skips_the_light_curve_servers_for_an_empty_cone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeSkyPatrol(index_bytes(), servers=["db01"])
    install(monkeypatch, fake)

    assert query_sky_patrol(TARGET.ra, TARGET.dec).light_curves is None
    assert [method for method, _, _ in fake.calls] == ["POST"]


def test_no_light_curve_servers_is_a_transient_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, FakeSkyPatrol(index_bytes(TARGET_ENTRY), servers=[]))

    with pytest.raises(SkyPatrolUnavailable) as raised:
        query_sky_patrol(TARGET.ra, TARGET.dec)

    assert is_transient(raised.value)


@pytest.mark.live
def test_live_fetch_returns_v_or_g_in_bjd_tdb() -> None:
    fetcher = AsassnFetcher(ArchiveLimits({"asassn": 1}), max_attempts=3)

    curves = {lc.band: lc for lc in asyncio.run(fetcher.fetch(TARGET))}

    assert curves, "Sky Patrol should hold V or g photometry for this RRAB"
    for lc in curves.values():
        assert lc.source_key == str(TARGET_ID)
        assert len(lc.time) > 100
        # BJD_TDB, not HJD − 2450000 or MJD: ASAS-SN spans 2012 onwards.
        assert np.all((lc.time > 2455900.0) & (lc.time < 2470000.0))
        assert np.all(lc.error < 99)
