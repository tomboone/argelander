"""Constellation membership and bounds for a Slice (spec §3.1).

A Slice is one constellation, so membership is astropy's `get_constellation` (the IAU
boundaries of Roman 1987), not a box. A catalog query still needs a box to send to the
server: `bounding_box` derives one by evaluating `get_constellation` on a `GRID_STEP` grid
over the whole sky and padding the members' extent by `PAD`, so the box always contains the
constellation and the exact filter runs locally on what comes back.
"""

import functools
from dataclasses import dataclass

import numpy as np

GRID_STEP = 0.25  # degrees
# More than a grid step, so a boundary between two samples still falls inside the box.
PAD = 0.5  # degrees


@dataclass(frozen=True)
class SkyBox:
    """An ICRS RA/Dec box in degrees. `ra_min > ra_max` means it wraps through RA 0h."""

    ra_min: float
    ra_max: float
    dec_min: float
    dec_max: float

    def ra_ranges(self) -> list[tuple[float, float]]:
        """The box's RA extent as one or two non-wrapping `(low, high)` ranges."""
        if self.ra_min <= self.ra_max:
            return [(self.ra_min, self.ra_max)]
        return [(self.ra_min, 360.0), (0.0, self.ra_max)]


def in_constellation(ra: np.ndarray, dec: np.ndarray, constellation: str) -> np.ndarray:
    """Whether each ICRS position (degrees) lies in `constellation`, an IAU abbreviation."""
    return _constellation_of(ra, dec) == constellation


@functools.cache
def bounding_box(constellation: str) -> SkyBox:
    """A box containing all of `constellation` (an IAU abbreviation such as "Dra")."""
    ra, dec = np.meshgrid(
        np.arange(0.0, 360.0, GRID_STEP), np.arange(-90.0 + GRID_STEP / 2, 90.0, GRID_STEP)
    )
    member = in_constellation(ra.ravel(), dec.ravel(), constellation)
    if not member.any():
        raise ValueError(f"unknown constellation {constellation!r}; use its IAU abbreviation")
    member_ra, member_dec = ra.ravel()[member], dec.ravel()[member]
    dec_min = max(-90.0, float(member_dec.min()) - PAD)
    dec_max = min(90.0, float(member_dec.max()) + PAD)

    # The RA extent is the complement of the widest gap between members' RAs, which may
    # wrap through 0h. A box that reaches a pole spans every RA.
    if abs(dec_min) == 90.0 or abs(dec_max) == 90.0:
        return SkyBox(0.0, 360.0, dec_min, dec_max)
    # PAD is an angle on the sky; in RA it is PAD / cos(Dec), largest at the box's most polar
    # edge, where a boundary between two Dec rows can swing through degrees of RA.
    ra_pad = PAD / np.cos(np.radians(max(abs(dec_min), abs(dec_max))))
    columns = np.unique(member_ra)
    gaps = np.diff(np.append(columns, columns[0] + 360.0))
    widest = int(np.argmax(gaps))
    if gaps[widest] - 2 * ra_pad <= GRID_STEP:
        return SkyBox(0.0, 360.0, dec_min, dec_max)
    start = float(columns[(widest + 1) % len(columns)])
    end = float(columns[widest])
    return SkyBox(float((start - ra_pad) % 360.0), float((end + ra_pad) % 360.0), dec_min, dec_max)


def _constellation_of(ra: np.ndarray, dec: np.ndarray) -> np.ndarray:
    # Imported here: astropy.coordinates is slow to import, and only Slice selection needs it.
    import astropy.units as u
    from astropy.coordinates import SkyCoord, get_constellation

    coords = SkyCoord(np.asarray(ra) * u.deg, np.asarray(dec) * u.deg, frame="icrs")
    return np.asarray(get_constellation(coords, short_name=True))
