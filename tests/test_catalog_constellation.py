"""Constellation membership and the bounding box a Slice's catalog query uses (spec §3.1)."""

import numpy as np
import pytest

from argelander.catalog.constellation import SkyBox, bounding_box, in_constellation

# Gaia DR3 1666776962473690112, a VSX VAR entry in Ursa Major 0.15° south of Draco's
# boundary: inside Draco's bounding box, outside Draco.
JUST_OUTSIDE_DRACO = (214.97375, 62.31790)


def in_box(box: SkyBox, ra: np.ndarray, dec: np.ndarray) -> np.ndarray:
    in_ra = np.zeros(ra.shape, dtype=bool)
    for low, high in box.ra_ranges():
        in_ra |= (ra >= low) & (ra <= high)
    return in_ra & (dec >= box.dec_min) & (dec <= box.dec_max)


def test_an_entry_just_outside_dracos_boundary_is_not_in_draco() -> None:
    ra, dec = JUST_OUTSIDE_DRACO
    box = bounding_box("Dra")

    assert in_box(box, np.array([ra]), np.array([dec]))[0]
    # 0.15° north is across the boundary, inside Draco.
    assert list(in_constellation(np.array([ra, ra]), np.array([dec, dec + 0.15]), "Dra")) == [
        False,
        True,
    ]
    assert in_constellation(np.array([ra]), np.array([dec]), "UMa")[0]


def test_dracos_box_covers_its_published_extent_and_no_more_than_needed() -> None:
    # Draco runs from about 9h20m to 21h in RA and +47° to +86° in Dec. Its RA pad is wide
    # (about 9°) because the box's north edge is near the pole.
    box = bounding_box("Dra")

    assert box.ra_ranges() == [(box.ra_min, box.ra_max)]
    assert 125.0 < box.ra_min < 140.0
    assert 314.0 < box.ra_max < 330.0
    assert box.dec_min == pytest.approx(47.0, abs=1.0)
    assert box.dec_max == pytest.approx(87.0, abs=1.0)


@pytest.mark.parametrize(
    ("constellation", "ra_span", "dec_span", "step"),
    [
        ("Dra", (120.0, 340.0), (40.0, 90.0), 0.1),
        ("Peg", (-50.0, 20.0), (-5.0, 45.0), 0.1),
        # Near the pole Cepheus's boundary swings through degrees of RA between two of the
        # box grid's Dec rows; a fixed RA pad missed members around RA 135°, Dec 87.5°.
        ("Cep", (120.0, 150.0), (85.0, 89.5), 0.01),
    ],
)
def test_the_box_contains_every_point_of_the_constellation(
    constellation: str, ra_span: tuple[float, float], dec_span: tuple[float, float], step: float
) -> None:
    # A grid finer than the one the box is built from, offset from it.
    ra, dec = np.meshgrid(
        np.arange(*ra_span, step) + step / 3, np.arange(*dec_span, step) + step / 3
    )
    ra, dec = ra.ravel() % 360.0, dec.ravel()
    member = in_constellation(ra, dec, constellation)

    assert member.any()
    assert in_box(bounding_box(constellation), ra[member], dec[member]).all()


def test_a_box_across_ra_zero_is_two_ra_ranges() -> None:
    box = bounding_box("Peg")

    assert box.ra_min > box.ra_max
    assert box.ra_ranges() == [(box.ra_min, 360.0), (0.0, box.ra_max)]


def test_a_box_around_a_pole_spans_every_ra() -> None:
    box = bounding_box("UMi")

    assert (box.ra_min, box.ra_max, box.dec_max) == (0.0, 360.0, 90.0)


def test_an_unknown_constellation_is_an_error() -> None:
    with pytest.raises(ValueError, match="IAU abbreviation"):
        bounding_box("Draco")
