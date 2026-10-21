"""VSX type definitions and the fixed thresholds the classifier's rules read (spec §7).

Part of what `PIPELINE_VERSION` stamps. It holds the revision form's type and passband
vocabularies, one `TypeSpec` per type VSX's type designations give numbers for
(`docs/reference/vsx-type-ranges.md`), the colour-magnitude regions that `analysis/cmd.py`
labels a Target with, and the phase-plot layout per type.
"""

import re
from dataclasses import dataclass
from typing import Literal

from argelander.analysis.model import Region

# The revision form's `varType` dropdown as captured 2026-10-19
# (docs/reference/vsx-revision-form.md), so a Proposal can never name a type VSX will not
# accept. Compound types (`EA+ELL`) go in the custom-type box instead.
TYPE_VOCABULARY = frozenset(
    {
        *("*", "ACEP", "ACEPS", "ACV", "ACYG", "AGN", "AM", "BCEP", "BCEPS", "BE", "BLAP"),
        *("BLLAC", "BXCIR", "BY", "CBSS", "CBSS/V", "cPNB[e]", "CPV", "CST", "CTTS"),
        *("CTTS/ROT", "CV", "CWA", "CWB", "DCEP", "DCEP(B)", "DCEPS", "DCEPS(B)", "DIP"),
        *("DPV", "DQ", "DSCT", "DWLYN", "DYPer", "E-DO", "EA", "EB", "EC", "ELL", "EP"),
        *("EW", "EXOR", "FF", "FKCOM", "FSCMa", "FUOR", "GCAS", "GDOR", "HADS", "HADS(B)"),
        *("HB", "HMXB", "IBWD", "L", "LB", "LC", "LERI", "LFBOT", "LMXB", "LPV", "M"),
        *("Microlens", "N", "NA", "NB", "NC", "NL", "NL/VY", "NR", "PPN", "PSR", "PVTEL"),
        *("PVTELI", "PVTELII", "PVTELIII", "QSO", "R", "RCB", "roAm", "roAp", "ROT"),
        *("RRAB", "RRC", "RRD", "RS", "RV", "RVA", "RVB", "S", "SDOR", "SN", "SN I"),
        *("SN Ia", "SN Ia-86G-like", "SN Iax", "SN Ib", "SN Ic", "SN Ien", "SN II"),
        *("SN II-L", "SN II-P", "SN IIa", "SN IIb", "SN IId", "SN IIn", "SN-pec", "SPB"),
        *("SR", "SRA", "SRB", "SRC", "SRD", "SRS", "SXARI", "SXPHE", "SXPHE(B)"),
        *("Transient", "TTS", "TTS/ROT", "UG", "UGER", "UGSS", "UGSU", "UGWZ", "UGZ", "UV"),
        *("UVN", "UXOR", "V1093HER", "V361HYA", "V838MON", "VAR", "VBD", "VY", "WCO/E"),
        *("WCP/E", "WR", "WTTS", "WTTS/ROT", "WWC", "WWC/E", "X", "YHG", "YSO", "ZAND"),
        *("ZZ", "ZZA", "ZZA/O", "ZZB", "ZZLep", "ZZO"),
    }
)

# The `maxband` / `minband` dropdown. TESS is not in it and goes in the custom passband box.
PASSBAND_VOCABULARY = frozenset(
    {
        "U",
        "B",
        "V",
        "Rc",
        "Ic",
        "u",
        "g",
        "r",
        "i",
        "z",
        "J",
        "H",
        "Ks",
        "R1",
        "G",
        "Kp",
        "CV",
        "CR",
    }
)

# The types the initial rule set can propose (spec §7). A best-scoring type outside them is
# proposed as `UNSPECIFIED` with its evidence attached; `CONSTANT` is "not variable".
INITIAL_TYPES = frozenset(
    {"EW", "EA", "EB", "RRAB", "RRC", "DSCT", "HADS", "DCEP", "SR", "L", "M", "ROT", "ELL"}
)
CONSTANT = "CST"
UNSPECIFIED = "VAR"

# A Proposal whose Confidence is below this is marked uncertain: the form's `varTypeUnc`
# checkbox, VSX's `:` suffix.
UNCERTAIN_BELOW = 0.7

SecondaryMinimum = Literal["absent-ok", "required-shallower", "equal"]
ColourHint = Literal["blue", "intermediate", "red", "any"]
EpochAt = Literal["max", "min", "none"]
RiseDurationRule = Literal["eclipse", "rise", "none"]


@dataclass(frozen=True)
class Range:
    """A period (days) or amplitude (mag, V) range; None leaves that side open.

    Hard bounds are the type document's explicit ranges, and a value past one opposes the
    type outright. Soft bounds are its "usually" and "mainly", which weigh about half.
    """

    low: float | None = None
    high: float | None = None
    soft_low: float | None = None
    soft_high: float | None = None


@dataclass(frozen=True)
class TypeSpec:
    """What VSX's type designations say about one type, for the rules to score against."""

    code: str
    period_range: Range = Range()
    amplitude_range: Range = Range()
    asymmetric: bool | None = None  # True a steep rise, False (near-)symmetric, None either
    secondary_minimum: SecondaryMinimum | None = None  # eclipsers only
    colour_hint: ColourHint = "any"
    # None for the confusion classes the reference gives no convention for: they are scored,
    # never proposed, so no Proposal needs their epoch or rise duration.
    epoch_at: EpochAt | None = None
    rise_duration_rule: RiseDurationRule | None = None


TYPE_SPECS: dict[str, TypeSpec] = {
    spec.code: spec
    for spec in (
        # The thirteen target types, CST and VAR.
        TypeSpec(
            "EA",
            period_range=Range(low=0.2),
            secondary_minimum="absent-ok",
            epoch_at="min",
            rise_duration_rule="eclipse",
        ),
        TypeSpec(
            "EB",
            period_range=Range(soft_low=0.5),
            amplitude_range=Range(soft_high=2.0),
            secondary_minimum="required-shallower",
            colour_hint="blue",
            epoch_at="min",
            rise_duration_rule="none",
        ),
        TypeSpec(
            "EW",
            period_range=Range(soft_high=1.0),
            amplitude_range=Range(soft_high=0.8),
            secondary_minimum="equal",
            colour_hint="any",  # "F–G and later" reaches red K and M contact binaries
            epoch_at="min",
            rise_duration_rule="none",
        ),
        TypeSpec(
            "ELL",
            amplitude_range=Range(high=0.3, soft_high=0.1),
            epoch_at="min",
            rise_duration_rule="none",
        ),
        # VSX states no ranges for ROT and points at BY's as guidance.
        TypeSpec("ROT", epoch_at="min", rise_duration_rule="none"),
        TypeSpec(
            "RRAB",
            period_range=Range(low=0.3, high=1.0),
            amplitude_range=Range(low=0.5, high=2.0),
            asymmetric=True,
            colour_hint="intermediate",
            epoch_at="max",
            rise_duration_rule="rise",
        ),
        TypeSpec(
            "RRC",
            period_range=Range(low=0.16, high=0.5),
            amplitude_range=Range(high=0.8),
            asymmetric=False,
            colour_hint="intermediate",
            epoch_at="max",
            rise_duration_rule="rise",
        ),
        TypeSpec(
            "DSCT",
            period_range=Range(low=0.01, high=0.2),
            amplitude_range=Range(low=0.003, high=0.9),
            colour_hint="intermediate",
            epoch_at="max",
            rise_duration_rule="none",
        ),
        TypeSpec(
            "HADS",
            period_range=Range(low=0.01, high=0.2),
            amplitude_range=Range(low=0.15),
            asymmetric=True,
            colour_hint="intermediate",
            epoch_at="max",
            rise_duration_rule="rise",
        ),
        TypeSpec(
            "DCEP",
            period_range=Range(low=1.0, high=135.0),
            amplitude_range=Range(high=2.0),
            colour_hint="intermediate",
            epoch_at="max",
            rise_duration_rule="rise",
        ),
        TypeSpec(
            "SR",
            period_range=Range(low=20.0),
            amplitude_range=Range(soft_low=1.0, soft_high=2.0),
            colour_hint="red",
            epoch_at="max",
            rise_duration_rule="none",
        ),
        TypeSpec("L", colour_hint="red", epoch_at="none", rise_duration_rule="none"),
        TypeSpec(
            "M",
            period_range=Range(low=80.0, high=1000.0),
            amplitude_range=Range(low=2.5, high=11.0),
            colour_hint="red",
            epoch_at="max",
            rise_duration_rule="none",
        ),
        TypeSpec(CONSTANT, epoch_at="none", rise_duration_rule="none"),
        TypeSpec(UNSPECIFIED, epoch_at="none", rise_duration_rule="none"),
        # Pulsating confusion classes that share the target types' period range.
        TypeSpec(
            "DCEPS",
            period_range=Range(high=7.0),
            amplitude_range=Range(high=0.5),
            asymmetric=False,
            colour_hint="intermediate",
            epoch_at="max",
            rise_duration_rule="rise",
        ),
        # Type II Cepheids: CW spans 0.8 to 35 d, split at 8 d.
        TypeSpec(
            "CWB",
            period_range=Range(low=0.8, high=8.0),
            amplitude_range=Range(low=0.3, high=1.2),
            colour_hint="intermediate",
        ),
        TypeSpec(
            "CWA",
            period_range=Range(low=8.0, high=35.0),
            amplitude_range=Range(low=0.3, high=1.2),
            colour_hint="intermediate",
        ),
        TypeSpec(
            "ACEP",
            period_range=Range(low=0.6, high=3.0),
            asymmetric=True,
            colour_hint="intermediate",
        ),
        # HAGDOR, high-amplitude GDOR, reaches 0.4 mag.
        TypeSpec(
            "GDOR",
            period_range=Range(low=0.25, high=4.0),
            amplitude_range=Range(high=0.4, soft_high=0.1),
            colour_hint="intermediate",
        ),
        TypeSpec(
            "SPB",
            period_range=Range(low=0.4, high=5.0),
            amplitude_range=Range(high=0.1),
            colour_hint="blue",
        ),
        TypeSpec(
            "BCEP",
            period_range=Range(low=0.1, high=0.6),
            amplitude_range=Range(low=0.01, high=0.3),
            colour_hint="blue",
        ),
        TypeSpec(
            "SXPHE",
            period_range=Range(low=0.04, high=0.08),
            amplitude_range=Range(high=0.7),
            colour_hint="intermediate",
        ),
        # Recognised by two periodogram peaks in a ratio of 0.74 (or 0.80) near P0 ≈ 0.5 d,
        # not by a range.
        TypeSpec("RRD", colour_hint="intermediate"),
        TypeSpec(
            "BLAP",
            period_range=Range(low=0.014, high=0.028),
            amplitude_range=Range(low=0.2, high=0.4),
            asymmetric=True,
            colour_hint="blue",
        ),
        # Rotating and eclipsing confusion classes.
        TypeSpec(
            "BY",
            period_range=Range(high=120.0),
            amplitude_range=Range(high=0.5),
            colour_hint="red",
        ),
        TypeSpec("RS", amplitude_range=Range(high=0.5), colour_hint="red"),
        TypeSpec(
            "ACV",
            period_range=Range(low=0.5, high=160.0),
            amplitude_range=Range(low=0.01, high=0.1),
            colour_hint="blue",
        ),
        TypeSpec("HB", amplitude_range=Range(high=0.3, soft_high=0.01)),
        TypeSpec("EP", secondary_minimum="absent-ok"),
        TypeSpec("R", amplitude_range=Range(high=1.0)),
    )
}

# The VSX types each Gaia DR3 `vari_classifier_result.best_class_name` covers, and those its
# RR Lyrae or Cepheid subclass narrows them to. Confidence breaks ties toward these.
GAIA_CLASS_TYPES: dict[str, frozenset[str]] = {
    "ECL": frozenset({"EA", "EB", "EW"}),
    "ELL": frozenset({"ELL"}),
    "EP": frozenset({"EP"}),
    "RR": frozenset({"RRAB", "RRC", "RRD"}),
    "CEP": frozenset({"DCEP", "DCEPS", "CWA", "CWB", "ACEP"}),
    "DSCT|GDOR|SXPHE": frozenset({"DSCT", "HADS", "GDOR", "SXPHE"}),
    "LPV": frozenset({"SR", "L", "M"}),
    "SOLAR_LIKE": frozenset({"ROT", "BY"}),
    "RS": frozenset({"RS"}),
    "SPB": frozenset({"SPB"}),
    "BCEP": frozenset({"BCEP"}),
    "ACV|CP|MCP|ROAM|ROAP|SXARI": frozenset({"ACV"}),
}
GAIA_SUBCLASS_TYPES: dict[str, frozenset[str]] = {
    "RRab": frozenset({"RRAB"}),
    "RRc": frozenset({"RRC"}),
    "RRd": frozenset({"RRD"}),
    "DCEP": frozenset({"DCEP", "DCEPS"}),
    "T2CEP": frozenset({"CWA", "CWB"}),
    "ACEP": frozenset({"ACEP"}),
}


def gaia_types(vari_class: str | None, vari_subclass: str | None = None) -> frozenset[str]:
    """The VSX types Gaia's classifier gave a Target; empty when it gave none we score."""
    if vari_subclass in GAIA_SUBCLASS_TYPES:
        return GAIA_SUBCLASS_TYPES[vari_subclass]
    return GAIA_CLASS_TYPES.get(vari_class or "", frozenset())


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
