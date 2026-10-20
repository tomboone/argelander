"""VSX type definitions and the fixed thresholds the classifier's rules read (spec §7).

Part of what `PIPELINE_VERSION` stamps. So far it holds the colour-magnitude regions that
`analysis/cmd.py` labels a Target with and the phase-plot layout per type; the `TypeSpec`
table follows with the rules.
"""

import re

from argelander.analysis.model import Region

# Phase-plot layout per the VSX FAQ (spec §8), with phase 0 at the Proposal epoch. Eclipsing
# binaries and ellipsoidal variables take their epoch at primary minimum and run from −0.25
# to 1.25, which puts the secondary eclipse at 0.5 in the middle of the axis. Pulsators take
# theirs at maximum and run from 0 to 2, which centres a maximum at 1.0; rotational and every
# other type use the same range.
ECLIPSER_PHASE_TYPES = frozenset({"EA", "EB", "EW", "ELL"})
ECLIPSER_PHASE_RANGE = (-0.25, 1.25)
DEFAULT_PHASE_RANGE = (0.0, 2.0)


def phase_range(vsx_type: str) -> tuple[float, float]:
    """The phase axis for a type. A compound or uncertain type (`EA+ELL`, `ELL|DSCT`, `EW:`,
    `EA/SD`) is laid out by its first code."""
    first = re.split(r"[+|/:]", vsx_type, maxsplit=1)[0]
    return ECLIPSER_PHASE_RANGE if first in ECLIPSER_PHASE_TYPES else DEFAULT_PHASE_RANGE


# Regions of Gaia's colour-magnitude diagram as polygons of (BP−RP, absolute G) vertices,
# drawn coarsely around the sequences of the Gaia DR2 HR diagram (Gaia Collaboration,
# Babusiaux et al. 2018) and the instability strip of its variable stars (Gaia Collaboration,
# Eyer et al. 2019). The colours are observed, not dereddened: Draco lies far from the
# Galactic plane, and a full extinction correction is a later improvement. The polygons
# overlap, and the first region that holds a point names it, so the order matters: the
# instability strip crosses the upper main sequence, where δ Scuti stars sit, and is tried
# before it. A point inside none of them is "unknown".
CMD_REGIONS: tuple[tuple[Region, tuple[tuple[float, float], ...]], ...] = (
    ("white_dwarf", ((-0.7, 8.0), (1.6, 13.0), (1.6, 17.5), (-0.7, 17.5))),
    (
        "instability_strip",
        # δ Scuti stars near M_G 3, RR Lyrae near 0.5, classical Cepheids up to −6.5; the
        # strip turns redder as it rises. Its red edge stays blue of BP−RP 0.5 where it
        # crosses the main sequence, so F-type dwarfs and W UMa binaries stay off it.
        (
            (0.15, 3.0),
            (0.45, 3.0),
            (0.6, 1.5),
            (1.0, 0.0),
            (1.45, -6.5),
            (0.75, -6.5),
            (0.35, 0.0),
        ),
    ),
    (
        "main_sequence",
        # Upper edge, blue to red, then lower edge back: wide enough above the ridge for
        # unresolved binaries and stars a little evolved.
        (
            (-0.5, -4.0),
            (0.0, 0.0),
            (0.5, 2.0),
            (1.0, 3.0),
            (1.5, 4.5),
            (2.0, 6.5),
            (3.5, 10.0),
            (3.5, 14.0),
            (2.0, 10.0),
            (1.2, 7.3),
            (0.8, 5.8),
            (0.4, 4.4),
            (0.0, 2.5),
            (-0.5, 0.0),
        ),
    ),
    (
        "giant_branch",
        # Subgiants, the red clump, the red giant and asymptotic giant branches.
        ((0.6, 3.0), (1.1, 4.3), (2.0, 2.5), (4.5, -1.5), (4.5, -6.0), (0.9, -6.0)),
    ),
)
