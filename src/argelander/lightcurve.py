"""The Light Curve every fetcher produces and the analyzer consumes, and its Cache layout.

Spec §5. One `LightCurve` per (Target, survey, band), stored as Parquet at
`$CACHE_DIR/{survey}/{gaia_source_id}/{band}.parquet`. The four arrays are the columns; the
scalar fields and `meta` go in the Parquet file metadata. The Cache is re-downloadable and
never the system of record; `light_curve_files` indexes it.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

VALUE_KINDS = ("mag", "flux")

# The single Parquet file-metadata key that holds every non-array field.
_METADATA_KEY = b"argelander"
_ARRAYS = ("time", "value", "error", "quality")


# eq=False: the generated __eq__ would compare arrays elementwise and fail on truthiness.
@dataclass(frozen=True, eq=False)
class LightCurve:
    survey: str  # "ztf" | "tess" | "gaia" | "asassn"
    source_key: str  # survey-native id(s), e.g. ZTF oids joined by "+"
    band: str  # "g" | "r" | "i" | "T" | "G" | "BP" | "RP" | "V"
    time: np.ndarray  # BJD_TDB, float64, sorted
    value: np.ndarray  # magnitude, or normalised flux for TESS
    error: np.ndarray
    quality: np.ndarray  # survey-native flag, int32; 0 means good after clean()
    value_kind: str  # "mag" | "flux"
    meta: dict[str, Any]  # JSON-serialisable: zero-point offsets, original time system, fetch date

    def __post_init__(self) -> None:
        if self.value_kind not in VALUE_KINDS:
            raise ValueError(f"value_kind must be one of {VALUE_KINDS}, got {self.value_kind!r}")
        arrays = {name: np.asarray(getattr(self, name)) for name in _ARRAYS}
        for name, raw in arrays.items():
            if raw.ndim != 1:
                raise ValueError(f"{name} must be one-dimensional")
        lengths = {name: len(raw) for name, raw in arrays.items()}
        if len(set(lengths.values())) != 1:
            raise ValueError(f"array length mismatch: {lengths}")
        time = arrays["time"]
        # Either byte order: astropy FITS tables (TESS, Gaia) hand back big-endian ">f8".
        if time.dtype.kind != "f" or time.dtype.itemsize != 8:
            raise ValueError(f"time must be float64, got {time.dtype}")
        if not np.all(np.isfinite(time)):
            raise ValueError("time must be finite")
        if not np.all(np.diff(time) >= 0):
            raise ValueError("time must be sorted ascending")
        for name in ("value", "error"):
            if arrays[name].dtype.kind != "f":
                raise ValueError(f"{name} must be floating point, got {arrays[name].dtype}")
        if arrays["quality"].dtype.kind not in "iu":
            raise ValueError(f"quality must be integer, got {arrays['quality'].dtype}")

        # Store native-endian copies in the canonical dtypes, read-only, so a caller still
        # holding the input arrays cannot break the invariants checked above.
        for name, raw in arrays.items():
            frozen = raw.astype(np.int32 if name == "quality" else np.float64)
            if not np.array_equal(frozen, raw, equal_nan=True):
                raise ValueError(f"{name} does not fit in {frozen.dtype}")
            frozen.setflags(write=False)
            object.__setattr__(self, name, frozen)

        # Normalised through JSON now (tuples become lists, keys strings) so that a Parquet
        # round trip compares equal, and a value JSON cannot hold fails here, not at write.
        try:
            meta = json.loads(json.dumps(self.meta))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"meta must be JSON-serialisable: {exc}") from exc
        object.__setattr__(self, "meta", meta)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, LightCurve):
            return NotImplemented
        return (
            self.survey == other.survey
            and self.source_key == other.source_key
            and self.band == other.band
            and self.value_kind == other.value_kind
            and self.meta == other.meta
            and all(
                np.array_equal(getattr(self, name), getattr(other, name), equal_nan=True)
                for name in _ARRAYS
            )
        )

    __hash__ = None  # mutable arrays and dict: not hashable

    def clean_mask(self) -> np.ndarray:
        """Boolean selection of the points that passed survey quality cleaning."""
        return self.quality == 0

    def to_parquet(self, path: Path) -> None:
        """Write to `path`, creating its parent directories."""
        fields = {
            "survey": self.survey,
            "source_key": self.source_key,
            "band": self.band,
            "value_kind": self.value_kind,
            "meta": self.meta,
        }
        table = pa.table({name: getattr(self, name) for name in _ARRAYS}).replace_schema_metadata(
            {_METADATA_KEY: json.dumps(fields).encode()}
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, path)

    @classmethod
    def from_parquet(cls, path: Path) -> "LightCurve":
        table = pq.read_table(path)
        metadata = table.schema.metadata or {}
        if _METADATA_KEY not in metadata:
            raise ValueError(f"{path} is not an Argelander Light Curve: no metadata")
        fields = json.loads(metadata[_METADATA_KEY])
        arrays = {name: table.column(name).to_numpy() for name in _ARRAYS}
        return cls(**fields, **arrays)


def cache_path(cache_dir: Path, survey: str, gaia_source_id: int, band: str) -> Path:
    """Where the Cache keeps one Light Curve (spec §5).

    `{cache_dir}/{survey}/{gaia_source_id}/{band}.parquet`
    """
    return cache_dir / survey / str(gaia_source_id) / f"{band}.parquet"


def load_target(cache_dir: Path, gaia_source_id: int) -> list[LightCurve]:
    """Every Light Curve the Cache holds for one Target, ordered by survey then band."""
    return [
        LightCurve.from_parquet(path)
        for path in sorted(cache_dir.glob(f"*/{gaia_source_id}/*.parquet"))
    ]
