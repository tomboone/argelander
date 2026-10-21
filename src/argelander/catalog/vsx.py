"""VSX entries for a Slice, from VizieR's weekly copy of VSX (`B/vsx/vsx`).

Spec §3.1; handover: data sources. VSX has no bulk API, so `fetch_vsx_slice` queries VizieR
for the VAR-typed entries whose magnitude at maximum lies in the Slice's range, inside the
constellation's `bounding_box`, then keeps those `in_constellation` that carry a Gaia DR3 ID.

Where the Gaia DR3 ID is (checked 2026-10-21, table version 2026-10-18): `B/vsx/vsx` has no
cross-identification column. Its columns are OID, Name, V, RAJ2000, DEJ2000, Type, the
magnitude fields (l_max, max, u_max, n_max, f_min, l_min, min, u_min, n_min), Epoch, u_Epoch,
l_Period, Period, u_Period, Sp and n_OID. The only Gaia DR3 ID in it is the Name of an entry
VSX imported from Gaia DR3, `Gaia DR3 <source_id>`; an entry named after another survey (ZTF,
ASAS-SN, LINEAR…) is left out even when VSX lists its Gaia ID among Other names, which the
VizieR copy does not carry. In Draco at 13–16 mag that is 88 of 518 VAR entries.

The Type constraint is the wildcard `VAR*`: VizieR's exact match (`=VAR`) takes one value,
and its list forms returned nothing. Rows are kept only when Type is exactly `VAR` or `VAR:`.
"""

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from argelander.catalog.constellation import bounding_box, in_constellation
from argelander.fetch.base import with_retries

CATALOG = "B/vsx/vsx"
COLUMNS = ("OID", "Name", "Type", "max", "n_max", "min", "f_min", "Period", "RAJ2000", "DEJ2000")
VAR_TYPES = frozenset({"VAR", "VAR:"})
GAIA_NAME = re.compile(r"Gaia DR3 (\d+)")


@dataclass(frozen=True)
class VsxEntry:
    """One VSX entry as the VizieR copy gives it, with the Gaia DR3 ID from its Name."""

    oid: int
    name: str
    ra: float  # J2000 degrees
    dec: float
    type: str  # "VAR" or "VAR:"
    period: float | None  # days
    mag_max: float | None  # magnitude at maximum (brightest)
    # Magnitude at minimum, or the amplitude when `mag_min_is_amplitude` (VSX's f_min flag).
    mag_min: float | None
    mag_min_is_amplitude: bool
    band: str | None  # passband of mag_max, a VSX passband code such as "G"
    gaia_source_id: int


def run_vizier_query(filters: dict[str, str]) -> Any:
    """Every `COLUMNS` row of `CATALOG` matching VizieR `filters`, as an astropy `Table`."""
    # Imported here: astroquery is slow to import, and only Slice selection needs it.
    from io import BytesIO

    from astropy.io.votable import parse
    from astropy.table import Table
    from astroquery.vizier import Vizier

    vizier = Vizier(columns=list(COLUMNS), column_filters=filters, row_limit=-1)
    response = vizier.query_constraints_async(catalog=CATALOG)
    # astroquery would read an HTTP error page as no result at all; raise it so it is retried.
    response.raise_for_status()
    tables = list(parse(BytesIO(response.content)).iter_tables())
    # VizieR answers a query that matches nothing with a VOTable holding no table.
    return tables[0].to_table() if tables else Table(names=COLUMNS)


async def fetch_vsx_slice(
    constellation: str,
    mag_min: float,
    mag_max: float,
    *,
    max_attempts: int,
    query: Callable[[dict[str, str]], Any] = run_vizier_query,
) -> list[VsxEntry]:
    """The VAR entries with a Gaia DR3 ID in `constellation` (an IAU abbreviation).

    `mag_min` and `mag_max` bound VSX's magnitude at maximum, inclusive, as a Slice's do.
    Sorted by OID.
    """
    box = bounding_box(constellation)
    entries: dict[int, VsxEntry] = {}
    # A box that wraps through RA 0h is two queries.
    for ra_low, ra_high in box.ra_ranges():
        filters = {
            "Type": "VAR*",
            "max": f"{mag_min}..{mag_max}",
            "RAJ2000": f"{ra_low}..{ra_high}",
            "DEJ2000": f"{box.dec_min}..{box.dec_max}",
        }
        table = await with_retries(
            lambda filters=filters: asyncio.to_thread(query, filters),
            survey="vizier",
            max_attempts=max_attempts,
        )
        entries.update((entry.oid, entry) for entry in entries_from_table(table, constellation))
    return [entries[oid] for oid in sorted(entries)]


def entries_from_table(table: Any, constellation: str) -> list[VsxEntry]:
    """The `VsxEntry`s in a VizieR result (an astropy `Table`) that belong to the Slice.

    Keeps rows typed exactly VAR or VAR:, named for a Gaia DR3 source, and inside
    `constellation`; drops the rest of what the bounding-box query returned.
    """
    if len(table) == 0:
        return []
    inside = in_constellation(
        np.asarray(table["RAJ2000"], dtype=float),
        np.asarray(table["DEJ2000"], dtype=float),
        constellation,
    )
    entries = []
    for row, member in zip(table, inside, strict=True):
        name = _str(row["Name"])
        gaia = GAIA_NAME.fullmatch(name or "")
        vsx_type = _str(row["Type"])
        if not member or gaia is None or vsx_type not in VAR_TYPES:
            continue
        entries.append(
            VsxEntry(
                oid=int(row["OID"]),
                name=gaia.string,
                ra=float(row["RAJ2000"]),
                dec=float(row["DEJ2000"]),
                type=vsx_type,
                period=_float(row["Period"]),
                mag_max=_float(row["max"]),
                mag_min=_float(row["min"]),
                mag_min_is_amplitude=_str(row["f_min"]) is not None,
                band=_str(row["n_max"]),
                gaia_source_id=int(gaia.group(1)),
            )
        )
    return entries


def _float(cell: Any) -> float | None:
    if np.ma.is_masked(cell):
        return None
    value = float(cell)
    return value if np.isfinite(value) else None


def _str(cell: Any) -> str | None:
    if np.ma.is_masked(cell):
        return None
    return str(cell).strip() or None
