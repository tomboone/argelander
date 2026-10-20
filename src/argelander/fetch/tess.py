"""TESS Light Curves through lightkurve and MAST: a prebuilt product when one covers the
Target, else simple aperture photometry on TESScut cutouts.

Spec §5.1. Prebuilt light curves come from several pipelines with different reach, so the
search takes the first author in `AUTHORS` that has the Target and downloads every sector it
covers. Which one wins for a 13–16 mag star is not known yet (spec §14), so the winner is
recorded as `meta["source"]` for the prototype to tally by magnitude. Only when nothing
prebuilt covers the Target are cutouts made; always cutting out, for uniformity, was rejected
as too slow and too large.

Each pipeline's systematics-corrected flux becomes `value` and its raw aperture flux is kept
in `meta["sap"]`, point for point, because VSX warns that PDCSAP sometimes removes real
features or mean-level changes; the analyzer falls back to SAP when it has. Only SPOC's
PDCSAP is a correction of that kind. QLP's KSPSAP and TGLC's `cal_*` fluxes are detrended
(high-pass filtered), which would erase most variable-star signals, so QLP's `value` is its
SAP flux too and TGLC's is its raw PSF flux, which Gaia-based forward modelling has already
decontaminated. Both are normalised by their own median per sector before the sectors are
stitched. Only `quality == 0` cadences are kept.

Every file is downloaded into a temporary directory that is removed when `fetch` returns or
raises, cutouts and prebuilt products alike: a sector's cutouts run to tens of MB, and the
Parquet Cache is the only copy kept (handover: Storage budget).
"""

import tempfile
import threading
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import astropy.units as u
import lightkurve as lk
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.table import Table

from argelander.analysis.time import TESS_BTJD_ZERO, tess_btjd_to_bjd_tdb
from argelander.fetch.base import ArchiveLimits, TargetRef, TessField, is_transient, with_retries
from argelander.lightcurve import LightCurve

# lightkurve's `download_all` silences itself by swapping `sys.stdout` for a devnull file,
# closing it, and putting back what it found. Two calls overlapping in `ArchiveLimits.run`'s
# threads can each find the other's devnull and put back a closed file, which leaves the
# whole process's stdout closed and fails every later print, lightkurve's own included. The
# searches stay concurrent; only the downloads take turns.
_DOWNLOAD_LOCK = threading.Lock()

# Most preferred first. SPOC is the 2-minute target-pixel pipeline; TESS-SPOC is the same
# pipeline run on full-frame images.
AUTHORS = ("QLP", "TGLC", "SPOC", "TESS-SPOC")
# Prebuilt products sit at catalogue positions: TGLC at Gaia DR3's (epoch 2016), QLP and SPOC
# at the TIC's (epoch 2000). 3″ covers 16 years of proper motion for nearly every Target and
# stays well inside one 21″ TESS pixel.
MATCH_RADIUS_ARCSEC = 3.0
# Pixels per side. Odd, so the Target sits in the centre pixel.
CUTOUT_SIZE = 11
# A cutout aperture is the region of pixels this many sigma above the cutout's median.
APERTURE_THRESHOLD = 3.0


@dataclass(frozen=True)
class _Columns:
    """Which columns of one author's product hold what."""

    value: str  # systematics-corrected flux
    sap: str  # raw aperture flux
    # (uncertainty, the flux it is the uncertainty of) pairs; the first pair a product has
    # wins. Empty: the author publishes no per-point errors.
    errors: tuple[tuple[str, str], ...]


_SPOC_COLUMNS = _Columns(
    value="pdcsap_flux", sap="sap_flux", errors=(("pdcsap_flux_err", "pdcsap_flux"),)
)
PRODUCT_COLUMNS = {
    # QLP renamed its detrended columns from kspsap_ to det_ in sector 56.
    "QLP": _Columns(
        value="sap_flux",
        sap="sap_flux",
        errors=(("kspsap_flux_err", "kspsap_flux"), ("det_flux_err", "det_flux")),
    ),
    "TGLC": _Columns(value="psf_flux", sap="aperture_flux", errors=()),
    "SPOC": _SPOC_COLUMNS,
    "TESS-SPOC": _SPOC_COLUMNS,
}
CUTOUT_SOURCE = "TESScut"


@dataclass(frozen=True)
class _Sector:
    """One sector's cadences before cleaning and normalisation."""

    number: int
    btjd: np.ndarray
    value: np.ndarray
    sap: np.ndarray
    # Per-point uncertainty as a fraction of the flux it belongs to; NaN when unpublished.
    fractional_error: np.ndarray
    quality: np.ndarray


def choose_products(table: Table) -> list[int]:
    """Rows of a `search_lightcurve` result table to download.

    The first author in `AUTHORS` with any rows wins; within it, the target nearest the
    search position, and one product per sector, the longest exposure where there are several
    (SPOC publishes 20 s beside 120 s). Returned in sector order; `[]` when no row qualifies.
    """
    for author in AUTHORS:
        rows = [i for i in range(len(table)) if table["author"][i] == author]
        if not rows:
            continue
        nearest = min(
            rows, key=lambda i: (float(table["distance"][i]), str(table["target_name"][i]))
        )
        target = table["target_name"][nearest]
        by_sector: dict[int, int] = {}
        for i in rows:
            if table["target_name"][i] != target:
                continue
            sector = int(table["sequence_number"][i])
            best = by_sector.get(sector)
            if best is None or float(table["exptime"][i]) > float(table["exptime"][best]):
                by_sector[sector] = i
        return [by_sector[sector] for sector in sorted(by_sector)]
    return []


def download_prebuilt(ra: float, dec: float, directory: Path) -> list[Any]:
    """Every sector of the preferred prebuilt light curve for the Target, as lightkurve
    `LightCurve`s read from files saved under `directory`; `[]` when none covers it."""
    # memoization's stub makes ty read lightkurve's bare `@cached` as the decorator-factory
    # overload, whose first parameter would swallow the target.
    radius = MATCH_RADIUS_ARCSEC * u.arcsec
    result = lk.search_lightcurve(_icrs(ra, dec), radius=radius, author=AUTHORS)  # ty: ignore[parameter-already-assigned]
    rows = choose_products(result.table)
    if not rows:
        return []
    with _DOWNLOAD_LOCK:
        products = result[rows].download_all(quality_bitmask="none", download_dir=str(directory))
    return list(products)


def download_cutouts(ra: float, dec: float, directory: Path) -> list[Any]:
    """A `CUTOUT_SIZE`-pixel TESScut cutout centred on the Target for every sector, as
    lightkurve `TargetPixelFile`s read from files saved under `directory`."""
    result = lk.search_tesscut(_icrs(ra, dec))
    if len(result) == 0:
        return []
    try:
        with _DOWNLOAD_LOCK:
            cutouts = result.download_all(
                cutout_size=CUTOUT_SIZE, quality_bitmask="none", download_dir=str(directory)
            )
    except Exception as error:
        # lightkurve re-raises any cutout failure as its own error without the HTTP status.
        # Surface a transient original so `with_retries` retries it instead of giving up.
        original = error.__context__
        if original is not None and is_transient(original):
            raise original from None
        raise
    return list(cutouts)


def light_curve_from_products(
    products: Sequence[Any], *, tess_field: TessField | None, fetched_at: str
) -> LightCurve | None:
    """Stitch one author's prebuilt sectors into one Light Curve; None if no cadence is good."""
    authors = {product.meta["AUTHOR"] for product in products}
    if len(authors) != 1:
        raise ValueError(f"expected one author's products, got {sorted(authors)}")
    (author,) = authors
    if author not in PRODUCT_COLUMNS:
        raise ValueError(f"no column mapping for TESS author {author!r}")
    columns = PRODUCT_COLUMNS[author]
    sectors = [_product_sector(product, columns) for product in products]
    source_key = "+".join(sorted({str(product.meta["OBJECT"]) for product in products}))
    return _stitch(
        sectors,
        source=author,
        source_key=source_key,
        columns={"flux_column": columns.value, "sap_column": columns.sap},
        tess_field=tess_field,
        fetched_at=fetched_at,
    )


def light_curve_from_cutouts(
    cutouts: Sequence[Any], *, tess_field: TessField | None, fetched_at: str
) -> LightCurve | None:
    """Simple aperture photometry on each sector's cutout, stitched into one Light Curve;
    None if no cadence is good. `value` and `meta["sap"]` hold the same flux."""
    # A cutout has no survey-native id; it is named by where it was centred.
    centres = sorted({f"{CUTOUT_SOURCE} {cutout.ra:.6f} {cutout.dec:+.6f}" for cutout in cutouts})
    return _stitch(
        [_cutout_sector(cutout) for cutout in cutouts],
        source=CUTOUT_SOURCE,
        source_key="+".join(centres),
        columns={"flux_column": "aperture", "sap_column": "aperture"},
        tess_field=tess_field,
        fetched_at=fetched_at,
    )


def _product_sector(product: Any, columns: _Columns) -> _Sector:
    value = _column(product, columns.value)
    fractional_error = np.full(len(value), np.nan)
    for error, error_of in columns.errors:
        if error in product.colnames and error_of in product.colnames:
            fractional_error = _column(product, error) / _column(product, error_of)
            break
    return _Sector(
        number=int(product.meta["SECTOR"]),
        btjd=np.asarray(product.time.btjd, dtype=np.float64),
        value=value,
        sap=_column(product, columns.sap),
        fractional_error=fractional_error,
        quality=_column(product, "quality"),
    )


def _cutout_sector(cutout: Any) -> _Sector:
    """Sum the aperture pixels and subtract a per-cadence background.

    The aperture is the `APERTURE_THRESHOLD`-sigma region containing the centre pixel, where
    the Target sits; if that pixel is not bright enough to be in one, the 3×3 block around it.
    The background per pixel is the median, cadence by cadence, of the pixels outside the
    aperture that are fainter than the cutout's median. No PSF fitting or crowding correction.
    """
    flux = _filled(cutout.flux)  # (cadence, row, column)
    flux_err = _filled(cutout.flux_err)
    _, rows, cols = flux.shape
    centre = (rows // 2, cols // 2)

    aperture = np.asarray(
        cutout.create_threshold_mask(threshold=APERTURE_THRESHOLD, reference_pixel="center"),
        dtype=bool,
    )
    if not aperture[centre]:
        aperture = np.zeros((rows, cols), dtype=bool)
        aperture[centre[0] - 1 : centre[0] + 2, centre[1] - 1 : centre[1] + 2] = True
    image = np.nanmedian(flux, axis=0)
    background = ~aperture & (image <= np.nanmedian(image))

    raw = np.nansum(flux[:, aperture], axis=1)
    sky = np.nanmedian(flux[:, background], axis=1) * aperture.sum()
    value = raw - sky
    error = np.sqrt(np.nansum(flux_err[:, aperture] ** 2, axis=1))
    return _Sector(
        number=int(cutout.meta["SECTOR"]),
        btjd=np.asarray(cutout.time.btjd, dtype=np.float64),
        value=value,
        sap=value,
        fractional_error=error / value,
        quality=np.asarray(cutout.quality, dtype=np.float64),
    )


def _stitch(
    sectors: Sequence[_Sector],
    *,
    source: str,
    source_key: str,
    columns: dict[str, str],
    tess_field: TessField | None,
    fetched_at: str,
) -> LightCurve | None:
    times, values, errors, saps = [], [], [], []
    used, dropped = [], []
    for sector in sorted(sectors, key=lambda s: s.number):
        keep = (
            (sector.quality == 0)
            & np.isfinite(sector.btjd)
            & np.isfinite(sector.value)
            & np.isfinite(sector.sap)
        )
        value_median = np.median(sector.value[keep]) if keep.any() else np.nan
        sap_median = np.median(sector.sap[keep]) if keep.any() else np.nan
        # A sector with no good cadence, or a faint cutout whose background-subtracted flux
        # centres on zero or below, cannot be normalised.
        if not (value_median > 0 and sap_median > 0):
            dropped.append(sector.number)
            continue
        value = sector.value[keep] / value_median
        times.append(sector.btjd[keep])
        values.append(value)
        errors.append(np.abs(sector.fractional_error[keep] * value))
        saps.append(sector.sap[keep] / sap_median)
        used.append(sector.number)
    if not used:
        return None

    btjd = np.concatenate(times)
    order = np.argsort(btjd, kind="stable")
    return LightCurve(
        survey="tess",
        source_key=source_key,
        band="T",
        time=tess_btjd_to_bjd_tdb(btjd[order]),
        value=np.concatenate(values)[order],
        error=np.concatenate(errors)[order],
        quality=np.zeros(len(btjd), dtype=np.int32),
        value_kind="flux",
        meta={
            # Which pipeline won for this Target, for the per-magnitude policy (spec §14).
            "source": source,
            "sectors": used,
            # Sectors downloaded but left out: no quality-0 cadence, or no positive median.
            "dropped_sectors": dropped,
            **columns,
            # Raw aperture flux, aligned point for point with `value`, normalised the same way.
            "sap": np.concatenate(saps)[order].tolist(),
            "normalisation": "each sector divided by its own median",
            "tess_field": asdict(tess_field) if tess_field is not None else None,
            "time_system": f"BTJD (BJD_TDB - {TESS_BTJD_ZERO})",
            "fetched_at": fetched_at,
        },
    )


def _column(table: Any, name: str) -> np.ndarray:
    """A lightkurve column as float64, masked entries as NaN."""
    return _filled(table[name])


def _filled(data: Any) -> np.ndarray:
    data = getattr(data, "value", data)  # Quantity → array; a plain array has no .value
    if hasattr(data, "filled"):  # numpy and astropy masked arrays
        data = data.filled(np.nan)
    return np.asarray(data, dtype=np.float64)


def _icrs(ra: float, dec: float) -> SkyCoord:
    return SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame="icrs")


class TessFetcher:
    """The TESS `Fetcher`: prebuilt products if any cover the Target, else cutouts, each
    download one MAST call under the MAST semaphore, with retries."""

    survey: str = "tess"

    def __init__(
        self,
        limits: ArchiveLimits,
        *,
        max_attempts: int,
        prebuilt: Callable[[float, float, Path], list[Any]] = download_prebuilt,
        cutouts: Callable[[float, float, Path], list[Any]] = download_cutouts,
    ) -> None:
        self.limits = limits
        self.max_attempts = max_attempts
        self.prebuilt = prebuilt
        self.cutouts = cutouts

    async def fetch(self, target: TargetRef) -> list[LightCurve]:
        fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
        # Removed on return and on any exception, with every file downloaded into it.
        with tempfile.TemporaryDirectory(prefix="argelander-tess-") as tmp:
            directory = Path(tmp)
            products = await self._download(self.prebuilt, target, directory)
            if products:
                lc = light_curve_from_products(
                    products, tess_field=target.tess_field, fetched_at=fetched_at
                )
            else:
                cutouts = await self._download(self.cutouts, target, directory)
                lc = (
                    light_curve_from_cutouts(
                        cutouts, tess_field=target.tess_field, fetched_at=fetched_at
                    )
                    if cutouts
                    else None
                )
        return [lc] if lc is not None else []

    async def _download(
        self,
        download: Callable[[float, float, Path], list[Any]],
        target: TargetRef,
        directory: Path,
    ) -> list[Any]:
        return await with_retries(
            lambda: self.limits.run("mast", download, target.ra, target.dec, directory),
            survey=self.survey,
            max_attempts=self.max_attempts,
        )
