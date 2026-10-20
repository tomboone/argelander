"""Gaia DR3 catalog data for a whole Slice: table-upload ADQL joins, never per-star queries.

Spec §5.1. Every Target needs Gaia's colour and parallax, Gaia's own variability class and
period (the hypothesis the analyzer tests first), its neighbours for the blend checks, and the
2MASS designation VSX prefers in Other names. `fetch_catalog` uploads a Slice's source IDs
`CHUNK_SIZE` at a time and runs four queries on each chunk:

- `CATALOG_QUERY`: `gaia_source` left-joined to `vari_summary`, `vari_classifier_result` and
  the four period tables (`vari_rrlyrae`, `vari_cepheid`, `vari_eclipsing_binary`,
  `vari_short_timescale`);
- `CROSS_ID_QUERY`: the 2MASS PSC designation, through `tmass_psc_xsc_best_neighbour` and
  `tmass_psc_xsc_join`;
- `NEIGHBOUR_QUERY`: every source within 20″ (ASAS-SN's ~17″ aperture is the widest judged
  source by source);
- `TESS_FIELD_QUERY`: the count, brightest G and combined G of the sources within 120″, for
  TESS's aperture.

They are separate because of how the archive behaves (measured 2026-10-20, anonymously):

- An async job is aborted after 60 s. The 2MASS tables left-joined into the catalog query
  stall it even for 100 IDs; inner-joined on their own they take seconds.
- A cone join must put the circle on the archive's side, `CONTAINS(POINT(target),
  CIRCLE(neighbour))`, which the archive compiles to a `q3c_join` that uses `gaia_source`'s
  index. The other way round scans it and is aborted even for 20 IDs.
- Results are cut at 50,000 rows with no warning, whatever was uploaded (50,000 IDs upload
  fine). The 120″ field is therefore aggregated in ADQL rather than returned source by source,
  and a result that reaches `ROW_CAP` is an error rather than silently short.

At `CHUNK_SIZE` IDs each query took 5–20 s in Draco. The 2MASS route through
`gaiadr1.tmass_original_valid` was dropped: `tmass_psc_xsc_join` already carries the PSC
designation, and joining the original table on it times out. No Gaia-hosted table maps DR3
sources to UCAC4 (only `gaiadr1.ucac4_best_neighbour`, keyed by DR1 IDs), so there is no UCAC4
id.
"""

import asyncio
import itertools
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import numpy as np

from argelander.fetch.base import TessField, with_retries
from argelander.fetch.gaia_epoch import gaia_http_errors

CHUNK_SIZE = 1_000
ROW_CAP = 50_000
NEIGHBOUR_RADIUS_ARCSEC = 20.0
TESS_FIELD_RADIUS_ARCSEC = 120.0

UPLOAD_TABLE = "targets"

CATALOG_QUERY = f"""
SELECT s.source_id, s.ra, s.dec, s.phot_g_mean_mag, s.bp_rp, s.parallax, s.parallax_error,
  s.ruwe, v.source_id AS vari_summary_id, v.trimmed_range_mag_g_fov,
  c.best_class_name, c.best_class_score,
  rr.pf AS rr_pf, rr.p1_o AS rr_p1_o, rr.best_classification AS rr_class,
  cep.pf AS cep_pf, cep.p1_o AS cep_p1_o, cep.p2_o AS cep_p2_o,
  cep.type_best_classification AS cep_type,
  eb.frequency AS eb_frequency, st.frequency AS st_frequency
FROM tap_upload.{UPLOAD_TABLE} AS u
JOIN gaiadr3.gaia_source AS s ON s.source_id = u.source_id
LEFT JOIN gaiadr3.vari_summary AS v ON v.source_id = u.source_id
LEFT JOIN gaiadr3.vari_classifier_result AS c ON c.source_id = u.source_id
LEFT JOIN gaiadr3.vari_rrlyrae AS rr ON rr.source_id = u.source_id
LEFT JOIN gaiadr3.vari_cepheid AS cep ON cep.source_id = u.source_id
LEFT JOIN gaiadr3.vari_eclipsing_binary AS eb ON eb.source_id = u.source_id
LEFT JOIN gaiadr3.vari_short_timescale AS st ON st.source_id = u.source_id
"""

CROSS_ID_QUERY = f"""
SELECT u.source_id, j.original_psc_source_id
FROM tap_upload.{UPLOAD_TABLE} AS u
JOIN gaiadr3.tmass_psc_xsc_best_neighbour AS b ON b.source_id = u.source_id
JOIN gaiadr3.tmass_psc_xsc_join AS j ON j.clean_tmass_psc_xsc_oid = b.clean_tmass_psc_xsc_oid
"""

NEIGHBOUR_QUERY = f"""
SELECT s.source_id, n.source_id AS neighbour_id,
  DISTANCE(POINT('ICRS', s.ra, s.dec), POINT('ICRS', n.ra, n.dec)) * 3600.0 AS separation,
  n.phot_g_mean_mag
FROM tap_upload.{UPLOAD_TABLE} AS u
JOIN gaiadr3.gaia_source AS s ON s.source_id = u.source_id
JOIN gaiadr3.gaia_source AS n ON 1 = CONTAINS(
  POINT('ICRS', s.ra, s.dec), CIRCLE('ICRS', n.ra, n.dec, {NEIGHBOUR_RADIUS_ARCSEC} / 3600.0))
WHERE n.source_id <> s.source_id
"""

TESS_FIELD_QUERY = f"""
SELECT s.source_id, COUNT(*) AS n_sources, MIN(n.phot_g_mean_mag) AS brightest_g,
  -2.5 * LOG10(SUM(POWER(10.0, -0.4 * n.phot_g_mean_mag))) AS combined_g
FROM tap_upload.{UPLOAD_TABLE} AS u
JOIN gaiadr3.gaia_source AS s ON s.source_id = u.source_id
JOIN gaiadr3.gaia_source AS n ON 1 = CONTAINS(
  POINT('ICRS', s.ra, s.dec), CIRCLE('ICRS', n.ra, n.dec, {TESS_FIELD_RADIUS_ARCSEC} / 3600.0))
WHERE n.source_id <> s.source_id
GROUP BY s.source_id
"""

# Gaia's period tables, most trusted first: the RR Lyrae and Cepheid pipelines are dedicated
# studies with their own validation, the eclipsing-binary model fits the whole light curve,
# and the short-timescale frequency is a by-product of a variogram search.
VARI_TABLES = ("rrlyrae", "cepheid", "eclipsing_binary", "short_timescale")


@dataclass(frozen=True)
class Neighbour:
    """A Gaia source within `NEIGHBOUR_RADIUS_ARCSEC` of a Target, other than the Target."""

    source_id: int
    separation: float  # arcsec, at Gaia's epoch (J2016.0)
    g: float | None  # None for a source without G photometry


@dataclass(frozen=True)
class GaiaRecord:
    """What Gaia DR3 says about one Target, for the analyzer, the classifier and VSX's form."""

    source_id: int
    ra: float  # ICRS degrees, J2016.0
    dec: float
    g: float | None  # phot_g_mean_mag
    bp_rp: float | None
    parallax: float | None  # mas
    parallax_error: float | None
    ruwe: float | None
    in_vari_summary: bool  # Gaia classed the source as variable at all
    vari_g_trimmed_range: float | None  # vari_summary's trimmed G range, mag
    vari_class: str | None  # vari_classifier_result.best_class_name, e.g. "RR", "ECL"
    vari_class_score: float | None
    # RR Lyrae best_classification ("RRab", "RRc", "RRd") or Cepheid type ("DCEP", "T2CEP",
    # "ACEP"); None outside those tables.
    vari_subclass: str | None
    # Days, keyed by the `VARI_TABLES` name of each table that has one for this source.
    vari_periods: dict[str, float]
    neighbours: tuple[Neighbour, ...]  # nearest first
    tess_field: TessField
    # Other names VSX prefers over Gaia's (Manual §IV.a, §V.b), e.g. {"2MASS": "2MASS J…"}.
    cross_ids: dict[str, str]

    @property
    def vari_period(self) -> float | None:
        """Gaia's published period in days from its most trusted table; None if it has none."""
        return next((self.vari_periods[t] for t in VARI_TABLES if t in self.vari_periods), None)


class GaiaQueryError(Exception):
    """An archive job that ended without results, for example aborted at the 60 s limit."""


def run_upload_query(adql: str, source_ids: list[int]) -> Any:
    """Run `adql` as an async job with `source_ids` uploaded as `tap_upload.targets`.

    Returns the result as an astropy `Table`.
    """
    # Imported here: importing astroquery.gaia builds a client that fetches the archive's
    # status messages over HTTP, which the offline tests must not do.
    from astropy.table import Table
    from astroquery.gaia import GaiaClass

    gaia = GaiaClass(show_server_messages=False)
    upload = Table({"source_id": np.asarray(source_ids, dtype=np.int64)})
    with gaia_http_errors():
        job = gaia.launch_job_async(
            adql, upload_resource=upload, upload_table_name=UPLOAD_TABLE, background=True
        )
        _, phase = job.wait_for_job_end()
        # astroquery reads an aborted job's missing results as a bare HTTP 500, which would
        # be retried; the archive's own reason is in the job's error document.
        if phase != "COMPLETED":
            raise GaiaQueryError(f"Gaia job {job.jobid} ended {phase}: {job.get_error()}")
        return job.get_results()


async def fetch_catalog(
    source_ids: Iterable[int],
    *,
    max_attempts: int,
    query: Callable[[str, list[int]], Any] = run_upload_query,
    chunk_size: int = CHUNK_SIZE,
) -> dict[int, GaiaRecord]:
    """A `GaiaRecord` for every source ID that Gaia DR3 has; unknown IDs are left out.

    The queries run one at a time: they are few and large, and the archive is shared.
    """
    records: dict[int, GaiaRecord] = {}
    for chunk in itertools.batched(dict.fromkeys(source_ids), chunk_size):
        ids = list(chunk)
        tables = []
        for adql in (CATALOG_QUERY, CROSS_ID_QUERY, NEIGHBOUR_QUERY, TESS_FIELD_QUERY):
            table = await with_retries(
                lambda adql=adql, ids=ids: asyncio.to_thread(query, adql, ids),
                survey="gaia",
                max_attempts=max_attempts,
            )
            if len(table) >= ROW_CAP:
                raise ValueError(
                    f"a Gaia query returned {len(table)} rows, the archive's cap, so it may be "
                    f"cut short; lower chunk_size (now {chunk_size})"
                )
            tables.append(table)
        fetched = records_from_tables(*tables)
        unexpected = sorted(set(fetched) - set(ids))
        if unexpected:
            raise ValueError(f"Gaia returned sources not requested: {unexpected}")
        records.update(fetched)
    return records


def records_from_tables(
    catalog: Any, cross_ids: Any, neighbours: Any, tess_fields: Any
) -> dict[int, GaiaRecord]:
    """Assemble `GaiaRecord`s from the four query results for one chunk (astropy `Table`s)."""
    tmass = {
        source_id: f"2MASS J{designation}"
        for source_id, row in _rows_by_source(cross_ids).items()
        if (designation := _str(row["original_psc_source_id"]))
    }
    near: dict[int, list[Neighbour]] = {}
    for row in neighbours:
        near.setdefault(int(row["source_id"]), []).append(
            Neighbour(
                source_id=int(row["neighbour_id"]),
                separation=float(row["separation"]),
                g=_float(row["phot_g_mean_mag"]),
            )
        )
    fields = {
        source_id: TessField(
            n_sources=int(row["n_sources"]),
            brightest_g=_float(row["brightest_g"]),
            combined_g=_float(row["combined_g"]),
        )
        for source_id, row in _rows_by_source(tess_fields).items()
    }

    records = {}
    for source_id, row in _rows_by_source(catalog).items():
        rr_class = _str(row["rr_class"])
        records[source_id] = GaiaRecord(
            source_id=source_id,
            ra=float(row["ra"]),
            dec=float(row["dec"]),
            g=_float(row["phot_g_mean_mag"]),
            bp_rp=_float(row["bp_rp"]),
            parallax=_float(row["parallax"]),
            parallax_error=_float(row["parallax_error"]),
            ruwe=_float(row["ruwe"]),
            in_vari_summary=not np.ma.is_masked(row["vari_summary_id"]),
            vari_g_trimmed_range=_float(row["trimmed_range_mag_g_fov"]),
            vari_class=_str(row["best_class_name"]),
            vari_class_score=_float(row["best_class_score"]),
            vari_subclass=rr_class if rr_class is not None else _str(row["cep_type"]),
            vari_periods=_vari_periods(row),
            neighbours=tuple(sorted(near.get(source_id, []), key=lambda n: n.separation)),
            tess_field=fields.get(source_id, TessField(0, None, None)),
            cross_ids={"2MASS": tmass[source_id]} if source_id in tmass else {},
        )
    return records


def _vari_periods(row: Any) -> dict[str, float]:
    # A fundamental-mode period when there is one, else the first overtone's, and so on.
    candidates = {
        "rrlyrae": (_float(row["rr_pf"]), _float(row["rr_p1_o"])),
        "cepheid": (_float(row["cep_pf"]), _float(row["cep_p1_o"]), _float(row["cep_p2_o"])),
        "eclipsing_binary": (_period(row["eb_frequency"]),),
        "short_timescale": (_period(row["st_frequency"]),),
    }
    periods = {}
    for table, values in candidates.items():
        period = next((p for p in values if p is not None), None)
        if period is not None:
            periods[table] = period
    return periods


def _rows_by_source(table: Any) -> dict[int, Any]:
    rows = {}
    for row in table:
        source_id = int(row["source_id"])
        if source_id in rows:
            raise ValueError(f"Gaia returned source {source_id} more than once")
        rows[source_id] = row
    return rows


def _float(cell: Any) -> float | None:
    if np.ma.is_masked(cell):
        return None
    value = float(cell)
    return value if math.isfinite(value) else None


def _period(frequency: Any) -> float | None:
    # The tables publish frequencies in 1/d.
    value = _float(frequency)
    return 1.0 / value if value is not None and value > 0 else None


def _str(cell: Any) -> str | None:
    if np.ma.is_masked(cell):
        return None
    return str(cell).strip() or None
