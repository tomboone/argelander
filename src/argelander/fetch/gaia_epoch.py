"""Gaia DR3 epoch photometry through the archive's DataLink service, fetched in batches.

Spec §5.1. Gaia's few dozen transits per star are sparse but free of ground-based aliases,
and G is the only band in which a Submission may state Gaia's magnitude range. Asking for one
star at a time would be impolite to the archive, so `fetch_many` requests a Slice's source
IDs `BATCH_SIZE` at a time, one batch at a time under the Gaia semaphore; `fetch` is a batch
of one, for the `Fetcher` protocol.

The request names `DATAMODEL_GAIA`, one row per source with every transit in arrays. The
archive refuses astroquery's default structure (`INDIVIDUAL`, since its release 3.15), and
`DATAMODEL_STANDARD`, the per-transit replacement, carries a single `rejected_by_photometry`
column that covers G only: BP and RP rejections are hidden in its opaque `*_other_flags`,
and some rejected BP and RP transits still carry a magnitude. `DATAMODEL_GAIA` has the flag
for each band. Per band, a transit is dropped when

- it was rejected by photometry: for G its flux, and so its magnitude, is null (the
  archive's own `rejected_by_photometry` is exactly that), for BP and RP
  `photometry_flag_{bp,rp}_reject`;
- it was rejected by variability: `variability_flag_{g,bp,rp}_reject`;
- it has no time, magnitude or positive flux signal-to-noise in that band.

Times are barycentric JD in TCB minus 2455197.5 (the votable's TIMESYS) and become BJD_TDB
here. Magnitudes are Vega; the archive publishes flux errors only, so a magnitude error is
2.5 / ln 10 over the flux signal-to-noise.
"""

import itertools
import math
import re
from collections.abc import Callable, Generator, Iterable, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import numpy as np
import requests

from argelander.analysis.time import gaia_tcb_to_bjd_tdb
from argelander.fetch.base import ArchiveLimits, TargetRef, with_retries
from argelander.lightcurve import LightCurve

DATA_RELEASE = "Gaia DR3"
BATCH_SIZE = 100

_MAG_ERROR_PER_INVERSE_SNR = 2.5 / math.log(10.0)


# (band, time column, magnitude column, flux signal-to-noise column, photometry flag column).
# G has no photometry flag: a G transit rejected by photometry has a null flux.
_BANDS = (
    ("G", "g_transit_time", "g_transit_mag", "g_transit_flux_over_error", None),
    ("BP", "bp_obs_time", "bp_mag", "bp_flux_over_error", "photometry_flag_bp_reject"),
    ("RP", "rp_obs_time", "rp_mag", "rp_flux_over_error", "photometry_flag_rp_reject"),
)

# astroquery raises a bare HTTPError whose message starts "Error 503:".
_STATUS_IN_MESSAGE = re.compile(r"^Error (\d{3})\b")


class GaiaArchiveError(Exception):
    """An HTTP error from the Gaia archive, with its status as `code` for `with_retries`."""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


@contextmanager
def gaia_http_errors() -> Generator[None]:
    """Re-raise astroquery's status-less HTTP errors as `GaiaArchiveError`.

    astroquery's TAP client puts the status only in the message, so without this an HTTP
    429 or 5xx from the archive would never be retried.
    """
    try:
        yield
    except requests.exceptions.HTTPError as error:
        match = _STATUS_IN_MESSAGE.match(str(error))
        if error.response is not None or match is None:
            raise
        raise GaiaArchiveError(str(error), int(match.group(1))) from error


def query_epoch_photometry(source_ids: list[int]) -> list[Any]:
    """The epoch-photometry tables (astropy `Table`s) DataLink returns for `source_ids`.

    Sources without epoch photometry are absent; if none has any, the list is empty.
    astroquery unpacks the products into a temporary directory under the working directory
    and removes it before returning.
    """
    # Imported here: importing astroquery.gaia builds a client that fetches the archive's
    # status messages over HTTP, which the offline tests must not do.
    from astroquery.gaia import GaiaClass

    gaia = GaiaClass(show_server_messages=False)
    with gaia_http_errors():
        products = gaia.load_data(
            ids=source_ids,
            data_release=DATA_RELEASE,
            retrieval_type="EPOCH_PHOTOMETRY",
            data_structure="DATAMODEL_GAIA",
            format="votable",
        )
    # The product names change with the batch, so every table is read whatever its name.
    return [element.to_table() for elements in products.values() for element in elements]


def light_curves_from_epoch_tables(
    tables: Iterable[Any], *, fetched_at: str
) -> dict[int, list[LightCurve]]:
    """One Light Curve per band with good transits, for every source in the DataLink tables."""
    curves: dict[int, list[LightCurve]] = {}
    for table in tables:
        for row in table:
            source_id = int(row["source_id"])
            if source_id in curves:
                raise ValueError(f"Gaia DataLink returned source {source_id} more than once")
            curves[source_id] = _source_light_curves(source_id, row, fetched_at)
    return curves


def _source_light_curves(source_id: int, row: Any, fetched_at: str) -> list[LightCurve]:
    curves = []
    for band, time_column, mag_column, snr_column, photometry_flag in _BANDS:
        time = _floats(row[time_column])
        mag = _floats(row[mag_column])
        snr = _floats(row[snr_column])
        good = np.isfinite(time) & np.isfinite(mag) & np.isfinite(snr) & (snr > 0)
        if photometry_flag is not None:
            good &= ~_flags(row[photometry_flag])
        good &= ~_flags(row[f"variability_flag_{band.lower()}_reject"])
        if not good.any():
            continue

        bjd = gaia_tcb_to_bjd_tdb(time[good])
        order = np.argsort(bjd, kind="stable")
        curves.append(
            LightCurve(
                survey="gaia",
                source_key=str(source_id),
                band=band,
                time=bjd[order],
                value=mag[good][order],
                error=_MAG_ERROR_PER_INVERSE_SNR / snr[good][order],
                quality=np.zeros(int(good.sum()), dtype=np.int32),
                value_kind="mag",
                meta={
                    "data_release": DATA_RELEASE,
                    # Transits dropped as rejected by photometry or variability, or null.
                    "n_rejected": int((~good).sum()),
                    "time_system": "BJD TCB - 2455197.5",
                    "fetched_at": fetched_at,
                },
            )
        )
    return curves


def _floats(cell: Any) -> np.ndarray:
    return np.ma.filled(np.ma.asarray(cell, dtype=np.float64), np.nan)


def _flags(cell: Any) -> np.ndarray:
    return np.ma.filled(np.ma.asarray(cell, dtype=bool), False)


class GaiaEpochFetcher:
    """The Gaia epoch-photometry `Fetcher`: batched DataLink requests under the Gaia semaphore."""

    survey: str = "gaia"

    def __init__(
        self,
        limits: ArchiveLimits,
        *,
        max_attempts: int,
        query: Callable[[list[int]], list[Any]] = query_epoch_photometry,
        batch_size: int = BATCH_SIZE,
    ) -> None:
        self.limits = limits
        self.max_attempts = max_attempts
        self.query = query
        self.batch_size = batch_size

    async def fetch(self, target: TargetRef) -> list[LightCurve]:
        return (await self.fetch_many([target]))[target.gaia_source_id]

    async def fetch_many(self, targets: Sequence[TargetRef]) -> dict[int, list[LightCurve]]:
        """Every Target's Light Curves by Gaia source ID; `[]` for a source with no epochs."""
        source_ids = list(dict.fromkeys(target.gaia_source_id for target in targets))
        curves: dict[int, list[LightCurve]] = {source_id: [] for source_id in source_ids}
        for batch in itertools.batched(source_ids, self.batch_size):
            tables = await with_retries(
                lambda ids=list(batch): self.limits.run("gaia", self.query, ids),
                survey=self.survey,
                max_attempts=self.max_attempts,
            )
            fetched = light_curves_from_epoch_tables(
                tables, fetched_at=datetime.now(UTC).isoformat(timespec="seconds")
            )
            unexpected = sorted(set(fetched) - set(batch))
            if unexpected:
                raise ValueError(f"Gaia DataLink returned sources not requested: {unexpected}")
            curves.update(fetched)
        return curves
