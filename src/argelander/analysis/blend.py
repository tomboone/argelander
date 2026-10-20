"""The blend checks: is a period or a range really the Target's, or a neighbour's?

Spec §6 step 7. A variable neighbour inside a survey's aperture produces a convincing period
that belongs to the wrong star, so each survey is judged against its own aperture, using the
contamination radii from VSX's submission notice (§5.1):

- `blend_suspect` covers ZTF and Gaia: a neighbour within `BLEND_RADIUS_ARCSEC` brighter
  than the Target's G + `BLEND_DELTA_G`, or (ZTF only) zero-point offsets between a band's
  oids above 0.05 mag, which the ZTF fetcher records as `meta["blend_suspect"]`;
- `asassn_contaminated`: a neighbour within `ASASSN_RADIUS_ARCSEC` brighter than G +
  `ASASSN_DELTA_G`;
- `tess_contaminated`: any source in the 120″ TESS field brighter than G + `TESS_DELTA_G`,
  or all of the field's sources together carrying more than `TESS_MAX_FLUX_RATIO` of the
  Target's flux. The field reaches past the 20″ neighbour list, so it is judged from the
  catalog join's count, brightest G and combined G, and names no single neighbour.

A contaminated survey still runs through the periodogram but counts at half weight in
Agreement. Each contamination carries the sentence VSX asks for in Remarks (VSX FAQ) and the
share of the aperture's light that is not the Target's, from every source inside the radius,
so a COMBLEND-style range correction can be added later. Deblending the photometry is out of
scope.
"""

from collections.abc import Iterable

from argelander.analysis.model import Blend, BlendFlag, Contamination
from argelander.catalog.gaia import TESS_FIELD_RADIUS_ARCSEC, GaiaRecord, Neighbour
from argelander.fetch.base import TessField
from argelander.lightcurve import LightCurve

# Wider than VSX's ~2″ for ZTF and ~0.7″ for Gaia G (§5.1): a margin over ZTF's seeing, and
# Gaia's BP and RP windows (3.5″ × 2.1″) take in light that G resolves.
BLEND_RADIUS_ARCSEC = 3.0
BLEND_DELTA_G = 2.0
ASASSN_RADIUS_ARCSEC = 17.0
ASASSN_DELTA_G = 2.0
TESS_DELTA_G = 1.0
TESS_MAX_FLUX_RATIO = 0.5

# How each survey is named in Remarks.
SURVEY_NAMES = {"ztf": "ZTF", "gaia": "Gaia", "asassn": "ASAS-SN", "tess": "TESS"}
NOT_CORRECTED = "Range not corrected."


def blend(record: GaiaRecord, curves: Iterable[LightCurve] = ()) -> Blend:
    """Every survey whose photometry of `record`'s Target is contaminated.

    `curves` are the Target's Light Curves; only ZTF's `blend_suspect` meta is read from them.
    A Target without a G magnitude is judged by the ZTF offsets alone.
    """
    g = record.g
    near = wide = ()
    if g is not None:
        near = _offending(record.neighbours, g, BLEND_RADIUS_ARCSEC, BLEND_DELTA_G)
        wide = _offending(record.neighbours, g, ASASSN_RADIUS_ARCSEC, ASASSN_DELTA_G)

    contaminations: list[Contamination] = []
    if g is not None and near:
        contaminations += [
            _from_neighbours(
                record.neighbours, g, survey, "blend_suspect", near, BLEND_RADIUS_ARCSEC
            )
            for survey in ("ztf", "gaia")
        ]
    elif any(c.survey == "ztf" and c.meta.get("blend_suspect") for c in curves):
        contaminations.append(
            Contamination(
                survey="ztf",
                flag="blend_suspect",
                neighbours=(),
                flux_fraction=None,
                remark=(
                    "ZTF magnitudes possibly contaminated: the zero points of its fields "
                    f"differ by more than 0.05 mag. {NOT_CORRECTED}"
                ),
            )
        )
    if g is not None and wide:
        contaminations.append(
            _from_neighbours(
                record.neighbours, g, "asassn", "asassn_contaminated", wide, ASASSN_RADIUS_ARCSEC
            )
        )
    if g is not None and (tess := _from_tess_field(g, record.tess_field)) is not None:
        contaminations.append(tess)
    return Blend(contaminations=tuple(contaminations))


def _offending(
    neighbours: tuple[Neighbour, ...], g: float, radius: float, delta_g: float
) -> tuple[Neighbour, ...]:
    """The neighbours within `radius` brighter than the Target's `g` + `delta_g`."""
    return tuple(
        n for n in neighbours if n.separation <= radius and n.g is not None and n.g < g + delta_g
    )


def _from_neighbours(
    neighbours: tuple[Neighbour, ...],
    g: float,
    survey: str,
    flag: BlendFlag,
    offending: tuple[Neighbour, ...],
    radius: float,
) -> Contamination:
    inside = [n.g for n in neighbours if n.separation <= radius and n.g is not None]
    named = " and ".join(
        f'Gaia DR3 {n.source_id} (G= {n.g:.1f}; sep. {n.separation:.1f}")' for n in offending
    )
    return Contamination(
        survey=survey,
        flag=flag,
        neighbours=offending,
        flux_fraction=_flux_fraction(g, sum(_flux(m) for m in inside)),
        remark=f"{SURVEY_NAMES[survey]} magnitudes contaminated by {named}. {NOT_CORRECTED}",
    )


def _from_tess_field(g: float, field: TessField) -> Contamination | None:
    """TESS's contamination by the sources in its field, or None when they are too faint."""
    brightest, combined = field.brightest_g, field.combined_g
    if brightest is None or combined is None:
        return None
    if brightest >= g + TESS_DELTA_G and _flux(combined) <= TESS_MAX_FLUX_RATIO * _flux(g):
        return None
    sources = "source" if field.n_sources == 1 else "sources"
    return Contamination(
        survey="tess",
        flag="tess_contaminated",
        neighbours=(),
        flux_fraction=_flux_fraction(g, _flux(combined)),
        remark=(
            f"TESS magnitudes contaminated by {field.n_sources} Gaia DR3 {sources} within "
            f'{TESS_FIELD_RADIUS_ARCSEC:.0f}" (brightest G= {brightest:.1f}; '
            f"combined G= {combined:.1f}). {NOT_CORRECTED}"
        ),
    )


def _flux(g: float) -> float:
    """G flux relative to a G 0 source."""
    return 10.0 ** (-0.4 * g)


def _flux_fraction(target_g: float, other_flux: float) -> float:
    return other_flux / (_flux(target_g) + other_flux)
