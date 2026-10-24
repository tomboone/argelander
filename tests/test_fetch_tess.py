"""The TESS fetcher: author preference, stitching, quality filtering, BJD_TDB, the SAP column,
and the cutout fallback that leaves no file behind (spec §5.1).

Recorded products, fetched on 2026-10-19 and trimmed to stay small:

- QLP sectors 14 and 15 for NSVS 2914677 (TIC 198457096), an RRab in Draco at V 13.5 with
  P = 0.447 d. Every third row is kept; the QUALITY-flagged rows among them are original.
- A 7×7-pixel TESScut cutout of sector 14 around CSS_J173907.6+550053, an RRab at G 14.7
  that no QLP, TGLC or SPOC product covers, so the fetcher's cutout fallback is its only
  TESS source. Every eighth cadence is kept.

Expected values come from the FITS tables read with astropy, not from lightkurve or the code
under test.
"""

import asyncio
import dataclasses
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import lightkurve as lk
import numpy as np
import pytest
import requests
from astropy.io import fits
from astropy.table import Table
from astropy.time import Time

from argelander.fetch import tess
from argelander.fetch.base import ArchiveLimits, Fetcher, TargetRef, TessField
from argelander.fetch.tess import (
    AUTHORS,
    TessFetcher,
    choose_products,
    download_cutouts,
    download_prebuilt,
    light_curve_from_products,
)
from argelander.lightcurve import LightCurve

FIXTURES = Path(__file__).parent / "fixtures" / "tess"
QLP_FILES = sorted(FIXTURES.glob("hlsp_qlp_*.fits"))
CUTOUT_FILE = FIXTURES / "tesscut_css_j173907_s0014_7x7.fits"
QLP_TARGET = TargetRef(
    gaia_source_id=1434401257322242816,
    ra=261.81704877,
    dec=57.55480617,
    vsx_name="NSVS 2914677",
)
CUTOUT_TARGET = TargetRef(
    gaia_source_id=1418010841782026880,
    ra=264.78197032,
    dec=55.01459443,
    vsx_name="CSS_J173907.6+550053",
)


def read_qlp() -> list[Any]:
    return [lk.read(str(path), quality_bitmask="none") for path in QLP_FILES]


def good_rows(path: Path) -> np.ndarray:
    data = fits.getdata(path, 1)
    keep = (data["QUALITY"] == 0) & np.isfinite(data["TIME"]) & np.isfinite(data["SAP_FLUX"])
    return data[keep]


def fetch(fetcher: TessFetcher, target: TargetRef = QLP_TARGET) -> list[LightCurve]:
    return asyncio.run(fetcher.fetch(target))


def qlp_fetcher() -> TessFetcher:
    return TessFetcher(
        ArchiveLimits({"mast": 1}),
        max_attempts=1,
        prebuilt=lambda ra, dec, directory: read_qlp(),
        cutouts=lambda ra, dec, directory: pytest.fail("cutouts made despite a prebuilt product"),
    )


def test_tess_fetcher_satisfies_the_fetcher_protocol() -> None:
    fetcher: Fetcher = TessFetcher(ArchiveLimits({"mast": 1}), max_attempts=1)

    assert fetcher.survey == "tess"


def test_prebuilt_sectors_stitch_into_one_t_band_flux_light_curve() -> None:
    (lc,) = fetch(qlp_fetcher())

    assert (lc.survey, lc.band, lc.value_kind) == ("tess", "T", "flux")
    assert lc.source_key == "TIC 198457096"
    assert lc.meta["source"] == "QLP"
    assert lc.meta["sectors"] == [14, 15]
    assert lc.meta["dropped_sectors"] == []
    assert len(lc.time) == sum(len(good_rows(path)) for path in QLP_FILES)
    assert np.all(np.diff(lc.time) >= 0)
    assert np.all(lc.quality == 0)


def test_only_quality_zero_cadences_are_kept() -> None:
    flagged = sum(int((fits.getdata(path, 1)["QUALITY"] != 0).sum()) for path in QLP_FILES)
    assert flagged > 0, "the recording should hold flagged cadences"

    (lc,) = fetch(qlp_fetcher())

    total = sum(len(fits.getdata(path, 1)) for path in QLP_FILES)
    assert len(lc.time) == total - flagged


def test_times_are_btjd_plus_2457000_in_bjd_tdb() -> None:
    (lc,) = fetch(qlp_fetcher())

    btjd = np.sort(np.concatenate([good_rows(path)["TIME"] for path in QLP_FILES]))
    np.testing.assert_allclose(lc.time, btjd + 2457000.0, rtol=0, atol=1e-9)
    assert lc.meta["time_system"] == "BTJD (BJD_TDB - 2457000.0)"


def test_each_sector_is_normalised_by_its_own_median() -> None:
    (lc,) = fetch(qlp_fetcher())

    for path in QLP_FILES:
        rows = good_rows(path)
        index = np.searchsorted(lc.time, rows["TIME"] + 2457000.0)
        sap = rows["SAP_FLUX"].astype(np.float64)
        np.testing.assert_allclose(lc.value[index], sap / np.median(sap), rtol=1e-12)
        assert np.median(lc.value[index]) == pytest.approx(1.0)
        # QLP publishes only the KSPSAP uncertainty; its fraction carries over to SAP.
        fraction = rows["KSPSAP_FLUX_ERR"] / rows["KSPSAP_FLUX"]
        np.testing.assert_allclose(lc.error[index], fraction * lc.value[index], rtol=1e-6)


def test_qlp_value_is_sap_flux_because_kspsap_is_high_pass_filtered() -> None:
    (lc,) = fetch(qlp_fetcher())

    assert lc.meta["flux_column"] == lc.meta["sap_column"] == "sap_flux"
    np.testing.assert_array_equal(lc.sap, lc.value)


def synthetic_product(author: str, sector: int, n: int = 6) -> Any:
    """A lightkurve product with every column the authors in `AUTHORS` publish."""
    rng = np.random.default_rng(sector)
    btjd = 2000.0 + sector * 30.0 + np.arange(n) * 0.1
    pdcsap = 1000.0 + rng.normal(0.0, 5.0, n)
    product = lk.TessLightCurve(time=Time(btjd, format="btjd", scale="tdb"), flux=pdcsap)
    product["pdcsap_flux"] = pdcsap
    product["pdcsap_flux_err"] = np.full(n, 2.0)
    product["sap_flux"] = pdcsap * 1.5 + np.linspace(0.0, 50.0, n)  # a trend PDC removed
    product["psf_flux"] = pdcsap * 0.9
    product["aperture_flux"] = product["sap_flux"] * 0.8
    product["quality"] = np.zeros(n, dtype=np.int32)
    product.meta.update({"AUTHOR": author, "SECTOR": sector, "OBJECT": "TIC 1"})
    return product


@pytest.mark.parametrize(
    ("author", "value_column", "sap_column"),
    [
        ("SPOC", "pdcsap_flux", "sap_flux"),
        ("TESS-SPOC", "pdcsap_flux", "sap_flux"),
        # TGLC's cal_* fluxes are detrended, like QLP's KSPSAP; the raw ones are kept.
        ("TGLC", "psf_flux", "aperture_flux"),
    ],
)
def test_value_is_the_corrected_flux_and_sap_the_raw_one(
    author: str, value_column: str, sap_column: str
) -> None:
    products = [synthetic_product(author, 20), synthetic_product(author, 21)]

    lc = light_curve_from_products(products, tess_field=None, fetched_at="2026-10-19")

    assert lc is not None
    assert (lc.meta["flux_column"], lc.meta["sap_column"]) == (value_column, sap_column)
    expected_value = np.concatenate(
        [np.asarray(p[value_column]) / np.median(np.asarray(p[value_column])) for p in products]
    )
    expected_sap = np.concatenate(
        [np.asarray(p[sap_column]) / np.median(np.asarray(p[sap_column])) for p in products]
    )
    np.testing.assert_allclose(lc.value, expected_value, rtol=1e-12)
    assert lc.sap is not None
    np.testing.assert_allclose(lc.sap, expected_sap, rtol=1e-12)
    assert not np.allclose(lc.value, lc.sap)


def test_qlp_from_sector_56_takes_its_error_from_the_renamed_det_columns() -> None:
    product = synthetic_product("QLP", 60)
    product["det_flux"] = np.full(len(product), 1.0)
    product["det_flux_err"] = np.full(len(product), 0.003)

    lc = light_curve_from_products([product], tess_field=None, fetched_at="2026-10-19")

    assert lc is not None
    np.testing.assert_allclose(lc.error, 0.003 * lc.value, rtol=1e-12)


def test_tglc_publishes_no_errors_so_they_are_nan() -> None:
    lc = light_curve_from_products(
        [synthetic_product("TGLC", 20)], tess_field=None, fetched_at="2026-10-19"
    )

    assert lc is not None
    assert np.all(np.isnan(lc.error))


def test_both_flux_columns_survive_the_parquet_round_trip(tmp_path: Path) -> None:
    lc = light_curve_from_products(
        [synthetic_product("SPOC", 20), synthetic_product("SPOC", 21)],
        tess_field=TessField(n_sources=2, brightest_g=13.9, combined_g=13.4),
        fetched_at="2026-10-19",
    )
    assert lc is not None
    path = tmp_path / "T.parquet"

    lc.to_parquet(path)
    back = LightCurve.from_parquet(path)

    assert back == lc
    np.testing.assert_array_equal(back.value, lc.value)
    assert back.sap is not None
    np.testing.assert_array_equal(back.sap, lc.sap)
    assert "sap" not in back.meta


def test_products_from_two_authors_are_refused() -> None:
    with pytest.raises(ValueError, match="one author"):
        light_curve_from_products(
            [synthetic_product("SPOC", 20), synthetic_product("QLP", 21)],
            tess_field=None,
            fetched_at="2026-10-19",
        )


def test_a_sector_with_no_good_cadence_is_dropped_and_recorded() -> None:
    bad = synthetic_product("SPOC", 21)
    bad["quality"] = np.full(len(bad), 4096, dtype=np.int32)

    lc = light_curve_from_products(
        [synthetic_product("SPOC", 20), bad], tess_field=None, fetched_at="2026-10-19"
    )

    assert lc is not None
    assert lc.meta["sectors"] == [20]
    assert lc.meta["dropped_sectors"] == [21]


def test_the_tess_field_is_recorded_when_the_gaia_record_has_one() -> None:
    target = dataclasses.replace(QLP_TARGET, tess_field=TessField(3, 12.4, 12.1))

    (with_field,) = fetch(qlp_fetcher(), target)
    (without,) = fetch(qlp_fetcher())

    assert with_field.meta["tess_field"] == {
        "n_sources": 3,
        "brightest_g": 12.4,
        "combined_g": 12.1,
    }
    assert without.meta["tess_field"] is None


def search_table(rows: list[tuple[str, str, float, int, float]]) -> Table:
    return Table(
        rows=rows, names=("author", "target_name", "distance", "sequence_number", "exptime")
    )


def test_the_first_preferred_author_wins_with_its_nearest_target() -> None:
    table = search_table(
        [
            ("SPOC", "1", 0.0, 14, 120.0),
            ("TGLC", "1001", 2.5, 14, 1800.0),  # a neighbour
            ("TGLC", "1000", 0.1, 15, 1800.0),
            ("TGLC", "1000", 0.1, 14, 1800.0),
            ("TASOC", "1", 0.0, 14, 1800.0),
        ]
    )

    assert choose_products(table) == [3, 2]


def test_one_product_per_sector_the_longest_exposure() -> None:
    table = search_table(
        [
            ("SPOC", "1", 0.0, 14, 20.0),
            ("SPOC", "1", 0.0, 14, 120.0),
            ("SPOC", "1", 0.0, 15, 120.0),
        ]
    )

    assert choose_products(table) == [1, 2]


def test_no_preferred_author_means_no_products() -> None:
    assert choose_products(search_table([("TASOC", "1", 0.0, 14, 1800.0)])) == []
    assert choose_products(Table()) == []


def test_the_preference_order_is_qlp_tglc_spoc() -> None:
    assert AUTHORS == ("QLP", "TGLC", "SPOC", "TESS-SPOC")


def test_download_prebuilt_fetches_only_the_chosen_rows_into_the_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    downloads: list[dict[str, Any]] = []

    class FakeResult:
        def __init__(self, table: Table) -> None:
            self.table = table

        def __getitem__(self, rows: list[int]) -> "FakeResult":
            return FakeResult(self.table[rows])

        def download_all(self, **kwargs: Any) -> list[str]:
            downloads.append({"sectors": list(self.table["sequence_number"]), **kwargs})
            return ["lc"]

    searches: list[dict[str, Any]] = []

    def fake_search(coord: Any, **kwargs: Any) -> FakeResult:
        searches.append(kwargs)
        rows = [("QLP", "9", 0.2, 15, 600.0), ("SPOC", "9", 0.0, 15, 120.0)]
        return FakeResult(search_table(rows))

    monkeypatch.setattr(tess.lk, "search_lightcurve", fake_search)

    assert download_prebuilt(1.0, 2.0, tmp_path) == ["lc"]
    (search,) = searches
    assert search["author"] == AUTHORS
    assert search["radius"].to_value("arcsec") == pytest.approx(3.0)
    (download,) = downloads
    assert download == {"sectors": [15], "quality_bitmask": "none", "download_dir": str(tmp_path)}


def test_overlapping_downloads_leave_stdout_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """lightkurve's `suppress_stdout` is not thread-safe: two overlapping downloads can put
    back each other's closed devnull as `sys.stdout`. The downloads must take turns."""

    class FakeResult:
        def __len__(self) -> int:
            return 1

        @lk.utils.suppress_stdout
        def download_all(self, **kwargs: Any) -> list[str]:
            time.sleep(0.05)
            return ["cutout"]

    monkeypatch.setattr(tess.lk, "search_tesscut", lambda coord: FakeResult())
    stdout = sys.stdout

    def staggered(delay: float) -> list[Any]:
        time.sleep(delay)
        return download_cutouts(1.0, 2.0, tmp_path)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(staggered, [0.0, 0.01]))

    assert results == [["cutout"], ["cutout"]]
    assert sys.stdout is stdout
    assert not sys.stdout.closed


def cutout_fetcher(cutouts: Any) -> TessFetcher:
    return TessFetcher(
        ArchiveLimits({"mast": 1}),
        max_attempts=1,
        prebuilt=lambda ra, dec, directory: [],
        cutouts=cutouts,
    )


def test_without_a_prebuilt_product_a_cutout_light_curve_is_extracted() -> None:
    def recorded(ra: float, dec: float, directory: Path) -> list[Any]:
        return [lk.read(str(CUTOUT_FILE), quality_bitmask="none")]

    (lc,) = fetch(cutout_fetcher(recorded), CUTOUT_TARGET)

    assert lc.meta["source"] == "TESScut"
    assert lc.source_key == "TESScut 264.781970 +55.014594"
    assert lc.meta["sectors"] == [14]
    assert (lc.band, lc.value_kind) == ("T", "flux")
    assert len(lc.time) == len(fits.getdata(CUTOUT_FILE, 1))
    assert np.median(lc.value) == pytest.approx(1.0)
    assert np.all(np.isfinite(lc.error)) and np.all(lc.error > 0)
    np.testing.assert_array_equal(lc.sap, lc.value)
    # An RRab varies by tens of percent; a misplaced aperture on sky would not.
    assert np.ptp(lc.value) > 0.2


def copy_cutout_into(directory: Path, seen: list[Path]) -> Any:
    copy = directory / "tesscut" / CUTOUT_FILE.name
    copy.parent.mkdir()
    shutil.copy(CUTOUT_FILE, copy)
    seen.append(copy)
    return lk.read(str(copy), quality_bitmask="none")


def test_the_cutout_file_is_deleted_after_extraction() -> None:
    seen: list[Path] = []

    (lc,) = fetch(
        cutout_fetcher(lambda ra, dec, directory: [copy_cutout_into(directory, seen)]),
        CUTOUT_TARGET,
    )

    (copy,) = seen
    assert lc.meta["source"] == "TESScut"
    assert not copy.exists()
    assert not copy.parent.parent.exists()


def test_the_cutout_file_is_deleted_when_extraction_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Path] = []

    def broken(*args: Any, **kwargs: Any) -> None:
        assert seen[0].exists()
        raise RuntimeError("extraction failed")

    monkeypatch.setattr(tess, "_cutout_sector", broken)
    fetcher = cutout_fetcher(lambda ra, dec, directory: [copy_cutout_into(directory, seen)])

    with pytest.raises(RuntimeError, match="extraction failed"):
        fetch(fetcher, CUTOUT_TARGET)

    (copy,) = seen
    assert not copy.parent.parent.exists()


def test_the_cutout_file_is_deleted_when_the_download_raises() -> None:
    seen: list[Path] = []

    def partial_download(ra: float, dec: float, directory: Path) -> list[Any]:
        copy_cutout_into(directory, seen)
        raise ValueError("second sector failed")

    with pytest.raises(ValueError, match="second sector failed"):
        fetch(cutout_fetcher(partial_download), CUTOUT_TARGET)

    (copy,) = seen
    assert not copy.parent.parent.exists()


def test_no_prebuilt_product_and_no_cutout_means_no_tess_data() -> None:
    assert fetch(cutout_fetcher(lambda ra, dec, directory: []), CUTOUT_TARGET) == []


def test_a_transient_cutout_failure_lightkurve_rewrapped_is_surfaced_for_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    gateway_timeout = requests.HTTPError("504", response=SimpleNamespace(status_code=504))

    class FakeResult:
        def __len__(self) -> int:
            return 1

        def download_all(self, **kwargs: Any) -> None:
            try:
                raise gateway_timeout
            except requests.HTTPError as exc:
                raise lk.search.SearchError(f"Unable to download FFI cutout: {exc}")  # noqa: B904

    monkeypatch.setattr(tess.lk, "search_tesscut", lambda coord: FakeResult())

    with pytest.raises(requests.HTTPError) as raised:
        download_cutouts(1.0, 2.0, tmp_path)

    assert raised.value is gateway_timeout


@pytest.mark.live
def test_live_fetch_returns_a_prebuilt_light_curve_and_records_its_source() -> None:
    fetcher = TessFetcher(ArchiveLimits({"mast": 2}), max_attempts=3)

    (lc,) = fetch(fetcher)

    assert lc.meta["source"] in AUTHORS
    assert lc.meta["source"] == "QLP"  # the only preferred author covering it on 2026-10-19
    assert len(lc.meta["sectors"]) > 2
    assert lc.sap is not None
    assert len(lc.time) == len(lc.sap) > 1000
    # BJD_TDB, not BTJD: TESS began observing in 2018 (JD 2458325).
    assert lc.time[0] > 2458300.0
