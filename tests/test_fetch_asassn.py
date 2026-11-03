"""The ASAS-SN fetcher: target choice, V/g split, cleaning and BJD_TDB (spec §5.1).

The offline tests replay a recorded Sky Patrol response for CSS_J173907.6+550053, an RRAB in
Draco at G 14.7 that Sky Patrol knows as `asas_sn_id` 223338434361 and saw with V cameras
ba and bc and g cameras bC and bs. It was fetched on 2026-11-02 with the fetcher's own
`query_sky_patrol` and trimmed to stay small: the target index is whole, and the 1,069
light-curve rows are cut to every 8th row, plus the one non-detection (`mag_err` 99.99),
the one image graded "U", and the six images in `css_j173907_variables_hjd.csv`. The kept
rows, columns and dtypes are original, including the pandas index column Sky Patrol adds.

`css_j173907_variables_hjd.csv` holds the HJDs the ASAS-SN Variable Stars Database
(asas-sn.osu.edu/variables, an older pipeline) publishes for six of those images, the three
at each extreme of the heliocentric correction. It pins down the time system: Sky Patrol's
`jd` is a UTC JD with no light-travel correction, not an HJD.

What a real Target does not show (a camera on neither list, a second target in the cone) is
added to the recording by copying recorded rows. Expected values come from the Parquet rows
with plain Python, not from the code under test.
"""

import asyncio
import csv
import io
from base64 import decodebytes
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import requests

from argelander.analysis.time import asassn_jd_to_bjd_tdb, bjd_tdb_to_hjd
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

FIXTURES = Path(__file__).parent / "fixtures" / "asassn"
TARGET = TargetRef(
    gaia_source_id=1418010841782026880,
    ra=264.78197,
    dec=55.01459,
    vsx_name="CSS_J173907.6+550053",
)
TARGET_ID = 223338434361
# A second target 2.5″ north, inside the 3″ cone.
NEIGHBOUR_ID = 223338434999
V_CAMERAS = {"ba", "bc"}
G_CAMERAS = {"bC", "bs"}

RECORDED_LIGHT_CURVES = (FIXTURES / "css_j173907_light_curves.parquet").read_bytes()
RECORDED = SkyPatrolResponse(
    index=(FIXTURES / "css_j173907_index.parquet").read_bytes(),
    light_curves=RECORDED_LIGHT_CURVES,
)
LIGHT_CURVE_TABLE = pq.read_table(io.BytesIO(RECORDED_LIGHT_CURVES))
ROWS: list[dict[str, Any]] = LIGHT_CURVE_TABLE.to_pylist()


def good_rows(cameras: set[str]) -> list[dict[str, Any]]:
    """The recorded rows Sky Patrol graded good that are detections, in time order."""
    return sorted(
        (
            row
            for row in ROWS
            if row["camera"] in cameras and row["quality"] == "G" and row["mag_err"] < 99
        ),
        key=lambda row: row["jd"],
    )


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


def with_copied_rows(count: int, **changes: Any) -> bytes:
    """The recorded light curves plus copies of the first `count` good rows with `changes`."""
    copies = pa.Table.from_pylist(
        [{**row, **changes} for row in good_rows(V_CAMERAS | G_CAMERAS)[:count]],
        schema=LIGHT_CURVE_TABLE.schema,
    )
    return parquet(pa.concat_tables([LIGHT_CURVE_TABLE, copies]))


TARGET_ENTRY = (TARGET_ID, 264.78197118, 55.014594, ["stellar_main", "tic", "aavsovsx"])
NEIGHBOUR_ENTRY = (NEIGHBOUR_ID, 264.78197, 55.01459 + 2.5 / 3600, ["stellar_main"])
WITH_NEIGHBOUR = with_copied_rows(5, asas_sn_id=NEIGHBOUR_ID)


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
        assert lc.meta["time_system"] == "JD UTC"
        assert np.all(lc.quality == 0)
    assert curves["V"].meta["cameras"] == sorted(V_CAMERAS)
    assert curves["g"].meta["cameras"] == sorted(G_CAMERAS)


@pytest.mark.parametrize(("band", "cameras"), [("V", V_CAMERAS), ("g", G_CAMERAS)])
def test_only_good_detections_survive_in_bjd_tdb_and_time_order(
    band: str, cameras: set[str]
) -> None:
    lc = fetch_recorded()[band]
    good = good_rows(cameras)
    jd = np.array([row["jd"] for row in good])

    np.testing.assert_allclose(
        lc.time, asassn_jd_to_bjd_tdb(jd, TARGET.ra, TARGET.dec), rtol=0, atol=1e-9
    )
    # JD (UTC) → BJD_TDB is TDB−UTC (67–69 s here) plus a barycentric correction of under
    # two minutes this close to the ecliptic pole.
    assert np.all(np.abs(lc.time - jd) < 200.0 / 86400.0)
    np.testing.assert_array_equal(lc.value, [row["mag"] for row in good])
    np.testing.assert_array_equal(lc.error, [row["mag_err"] for row in good])


def test_sky_patrol_times_agree_with_the_hjds_asas_sn_published_for_the_same_images() -> None:
    lc = fetch_recorded()["V"]
    with (FIXTURES / "css_j173907_variables_hjd.csv").open() as f:
        published = [(float(row["hjd"]), float(row["mag"])) for row in csv.DictReader(f)]

    for hjd, mag in published:
        image = int(np.argmin(np.abs(lc.time - hjd)))
        jd = good_rows(V_CAMERAS)[image]["jd"]
        # The same image: the two pipelines' magnitudes differ by about 0.07.
        assert abs(lc.value[image] - mag) < 0.2
        # Read as an HJD, Sky Patrol's time would be 84–114 s off for these images.
        assert abs(jd - hjd) * 86400.0 > 60.0
        # Read as a JD, the HJD it gives matches the published one within 10 s: what is
        # left is about 8 % of the heliocentric correction, a scale one of the two pipelines
        # applies and the other does not (fetch/asassn.py).
        converted = bjd_tdb_to_hjd(lc.time[image], TARGET.ra, TARGET.dec)
        assert abs(float(converted) - hjd) * 86400.0 < 15.0


def test_bad_images_and_non_detections_are_dropped() -> None:
    curves = fetch_recorded()

    assert {row["quality"] for row in ROWS} == {"G", "B", "U"}
    assert any(row["mag_err"] >= 99 for row in ROWS)
    assert sum(len(lc.time) for lc in curves.values()) == len(good_rows(V_CAMERAS | G_CAMERAS))
    assert all(np.all(lc.error < 99) for lc in curves.values())


def test_a_camera_on_neither_list_is_dropped() -> None:
    response = SkyPatrolResponse(
        index=RECORDED.index, light_curves=with_copied_rows(3, camera="zz")
    )

    curves = fetch_recorded(response)

    assert sum(len(lc.time) for lc in curves.values()) == len(good_rows(V_CAMERAS | G_CAMERAS))


@pytest.mark.parametrize(
    "entries", [(NEIGHBOUR_ENTRY, TARGET_ENTRY), (TARGET_ENTRY, NEIGHBOUR_ENTRY)]
)
def test_the_nearer_of_two_targets_is_kept_whichever_order_they_arrive_in(
    entries: tuple[tuple[int, float, float, list[str]], ...],
) -> None:
    response = SkyPatrolResponse(index=index_bytes(*entries), light_curves=WITH_NEIGHBOUR)

    curves = fetch_recorded(response)

    assert {lc.source_key for lc in curves.values()} == {str(TARGET_ID)}
    assert sum(len(lc.time) for lc in curves.values()) == len(good_rows(V_CAMERAS | G_CAMERAS))


def test_a_band_with_no_good_points_is_left_out() -> None:
    v_only = LIGHT_CURVE_TABLE.filter(pa.array([row["camera"] in V_CAMERAS for row in ROWS]))
    response = SkyPatrolResponse(index=RECORDED.index, light_curves=parquet(v_only))

    assert list(fetch_recorded(response)) == ["V"]


def test_an_empty_cone_means_no_asassn_data() -> None:
    assert fetch_recorded(SkyPatrolResponse(index=index_bytes(), light_curves=None)) == {}


@pytest.mark.parametrize(
    "response",
    [
        SkyPatrolResponse(index=b"<!DOCTYPE HTML><title>404 Not Found</title>", light_curves=None),
        SkyPatrolResponse(index=RECORDED.index, light_curves=b"<html></html>"),
        SkyPatrolResponse(index=RECORDED.index, light_curves=parquet(pa.table({"jd": [1.0]}))),
        SkyPatrolResponse(index=RECORDED.index, light_curves=None),
    ],
    ids=["html-index", "html-light-curves", "missing-columns", "no-light-curves"],
)
def test_a_response_that_is_not_sky_patrol_parquet_is_an_error(
    response: SkyPatrolResponse,
) -> None:
    with pytest.raises(ValueError, match="Sky Patrol"):
        light_curves_from_sky_patrol(response, TARGET.ra, TARGET.dec, fetched_at="2026-11-02")


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
    fake = FakeSkyPatrol(RECORDED.index, servers=["asassn-db01.example"])
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
    fake = FakeSkyPatrol(RECORDED.index, servers=["db01", "db02"], failing={"db01": failure})
    install(monkeypatch, fake)

    assert query_sky_patrol(TARGET.ra, TARGET.dec).light_curves == RECORDED.light_curves
    assert fake.block_hosts() == ["db01", "db02"]


def test_the_last_servers_failure_is_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSkyPatrol(
        RECORDED.index,
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
    install(monkeypatch, FakeSkyPatrol(RECORDED.index, servers=[]))

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
