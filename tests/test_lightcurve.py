"""LightCurve: validation, Parquet round trip, Cache path layout (spec §5)."""

import ast
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import pytest

from argelander.lightcurve import LightCurve, cache_path

SRC = Path(__file__).resolve().parents[1] / "src" / "argelander"


def make_lc(**overrides: object) -> LightCurve:
    fields: dict[str, object] = {
        "survey": "ztf",
        "source_key": "633211200012345+633211100054321",
        "band": "g",
        "time": np.array([2459000.5, 2459001.25, 2459003.0, 2459003.0], dtype=np.float64),
        "value": np.array([14.21, 14.35, np.nan, 14.30]),
        "error": np.array([0.012, 0.015, 0.02, 0.011]),
        "quality": np.array([0, 0, 4, 0], dtype=np.int32),
        "value_kind": "mag",
        "meta": {
            "zero_point_offsets": {"633211100054321": -0.012},
            "original_time_system": "HMJD",
            "fetched_at": "2026-10-19T12:00:00+00:00",
        },
    }
    fields.update(overrides)
    return LightCurve(**fields)


def test_parquet_round_trip_is_equal_including_meta(tmp_path: Path) -> None:
    lc = make_lc()
    path = tmp_path / "lc.parquet"

    lc.to_parquet(path)
    loaded = LightCurve.from_parquet(path)

    assert loaded == lc
    assert loaded.meta == lc.meta
    assert loaded.time.dtype == np.float64
    assert loaded.quality.dtype == np.int32


def test_meta_is_stored_in_parquet_file_metadata(tmp_path: Path) -> None:
    path = tmp_path / "lc.parquet"
    make_lc().to_parquet(path)

    schema = pq.read_schema(path)

    assert schema.names == ["time", "value", "error", "quality"]
    assert schema.metadata is not None
    assert b"argelander" in schema.metadata


def test_to_parquet_creates_missing_cache_directories(tmp_path: Path) -> None:
    path = cache_path(tmp_path, "ztf", 1234567890123456789, "g")

    make_lc().to_parquet(path)

    assert path.is_file()


def test_equality_detects_a_changed_value() -> None:
    lc = make_lc()
    other = make_lc(value=np.array([14.21, 14.35, np.nan, 14.31]))

    assert lc != other


@pytest.mark.parametrize("field", ["value", "error", "quality"])
def test_mismatched_array_lengths_raise(field: str) -> None:
    with pytest.raises(ValueError, match="length"):
        make_lc(**{field: np.zeros(3)})


def test_unsorted_time_raises() -> None:
    with pytest.raises(ValueError, match="sorted"):
        make_lc(time=np.array([2459001.0, 2459000.0, 2459002.0, 2459003.0]))


@pytest.mark.parametrize("bad", [np.nan, np.inf])
def test_non_finite_time_raises(bad: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        make_lc(
            time=np.array([bad]),
            value=np.array([14.0]),
            error=np.array([0.01]),
            quality=np.array([0], dtype=np.int32),
        )


def test_non_float64_time_raises() -> None:
    with pytest.raises(ValueError, match="float64"):
        make_lc(time=np.array([2459000.0, 2459001.0, 2459002.0, 2459003.0], dtype=np.float32))


def test_big_endian_float64_time_is_accepted_and_stored_native() -> None:
    # astropy FITS tables (TESS via lightkurve, Gaia DataLink) return big-endian columns.
    time = np.array([2459000.5, 2459001.25, 2459003.0, 2459003.0], dtype=">f8")

    lc = make_lc(time=time)

    assert lc.time.dtype == np.float64
    assert lc.time.dtype.isnative


def test_arrays_are_read_only_copies() -> None:
    value = np.array([14.21, 14.35, 14.4, 14.30])
    lc = make_lc(value=value)

    value[0] = 99.0

    assert lc.value[0] == 14.21
    with pytest.raises(ValueError, match="read-only"):
        lc.time[0] = 0.0


def test_quality_is_stored_as_int32() -> None:
    lc = make_lc(quality=np.array([0, 0, 4, 0], dtype=np.int64))

    assert lc.quality.dtype == np.int32


def test_quality_overflowing_int32_raises() -> None:
    with pytest.raises(ValueError, match="int32"):
        make_lc(quality=np.array([0, 0, 2**40, 0], dtype=np.int64))


def test_float_quality_raises() -> None:
    with pytest.raises(ValueError, match="integer"):
        make_lc(quality=np.array([0.0, 0.0, 4.0, 0.0]))


def test_meta_that_json_cannot_hold_raises() -> None:
    with pytest.raises(ValueError, match="JSON"):
        make_lc(meta={"zero_point_offset": np.float32(0.01)})


def test_meta_is_json_normalised_so_round_trip_is_equal(tmp_path: Path) -> None:
    lc = make_lc(meta={"oids": ("633211200012345", "633211100054321")})
    path = tmp_path / "lc.parquet"

    lc.to_parquet(path)

    assert lc.meta == {"oids": ["633211200012345", "633211100054321"]}
    assert LightCurve.from_parquet(path) == lc


def test_unknown_value_kind_raises() -> None:
    with pytest.raises(ValueError, match="value_kind"):
        make_lc(value_kind="counts")


def test_clean_mask_selects_quality_zero() -> None:
    lc = make_lc()

    np.testing.assert_array_equal(lc.clean_mask(), [True, True, False, True])


def test_cache_path_matches_spec_layout() -> None:
    path = cache_path(Path("/mnt/ssd/cache"), "gaia", 1234567890123456789, "BP")

    assert path == Path("/mnt/ssd/cache/gaia/1234567890123456789/BP.parquet")


def test_core_package_imports_neither_pandas_nor_polars() -> None:
    banned = {"pandas", "polars"}
    offenders = []
    for py in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(py.read_text(), filename=str(py))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            offenders += [f"{py.name}: {n}" for n in names if n.split(".")[0] in banned]

    assert offenders == []
