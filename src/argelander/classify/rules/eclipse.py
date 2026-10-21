"""Eclipse shape: is the minimum an eclipse, how deep is the secondary, are the contacts
readable (spec §7, docs/reference/vsx-type-ranges.md "EW vs EB vs EA").

An eclipse is a narrow minimum, at most `ECLIPSE_MAX_WIDTH` of the cycle wide at half its
depth; a pulsating or spotted star's minimum is broad. EW's minima are of almost equal depth,
EB's secondary is always present and considerably shallower, and EA's light is nearly
constant between eclipses, so its contacts can be read. The secondary and contact rules
judge any minimum not too broad for an eclipse, under `BROAD_MIN_WIDTH`; the pulsation rules
judge any not narrow enough to be one, so a minimum between the two widths is weighed as
both.
"""

from argelander.analysis.model import Analysis, Shape
from argelander.classify.rules.base import Evidence
from argelander.classify.types import (
    ABSENT_SECONDARY,
    BROAD_MIN_WIDTH,
    CONTINUOUS_LIGHT,
    ECLIPSE_MAX_WIDTH,
    EQUAL_MINIMA,
    FLAT_MAXIMA,
    HARD_WEIGHT,
    NEAR_EQUAL_SECONDARY,
    PERIODIC_TYPES,
    SHALLOWER_SECONDARY,
    SOFT_WEIGHT,
    TYPE_SPECS,
    UNEQUAL_MINIMA,
)
from argelander.db.models import Target

ECLIPSING_TYPES = frozenset({"EA", "EB", "EW", "EP"})
# RS stars are often eclipsing and a heartbeat star's periastron spike is narrow, so their
# minima say nothing.
NON_ECLIPSING_TYPES = PERIODIC_TYPES - ECLIPSING_TYPES - {"RS", "HB"}


class EclipseWidth:
    name = "eclipse_width"
    applies_to = ECLIPSING_TYPES | NON_ECLIPSING_TYPES

    def evaluate(self, type_code: str, analysis: Analysis, target: Target) -> Evidence:
        shape = analysis.shape
        if shape is None:
            return _unmeasured(self.name, type_code)
        width = shape.eclipse_width
        stated = f"The primary minimum is {width:.2f} of the cycle wide at half its depth"
        eclipsing = type_code in ECLIPSING_TYPES
        if width <= ECLIPSE_MAX_WIDTH:
            if eclipsing:
                return Evidence(
                    self.name, type_code, "supports", HARD_WEIGHT, f"{stated}, an eclipse."
                )
            return Evidence(
                self.name,
                type_code,
                "against",
                HARD_WEIGHT,
                f"{stated}, an eclipse, which {type_code} does not have.",
            )
        if width >= BROAD_MIN_WIDTH and eclipsing:
            return Evidence(
                self.name, type_code, "against", HARD_WEIGHT, f"{stated}, too broad for an eclipse."
            )
        return Evidence(self.name, type_code, "neutral", 0.0, f"{stated}.")


class SecondaryDepth:
    """`TypeSpec.secondary_minimum`: near-equal minima, or a secondary always present and
    considerably shallower. A type whose secondary may be absent is not judged."""

    name = "secondary_minimum"
    applies_to = frozenset(
        code
        for code, spec in TYPE_SPECS.items()
        if spec.secondary_minimum in ("equal", "required-shallower")
    )

    def evaluate(self, type_code: str, analysis: Analysis, target: Target) -> Evidence:
        shape = analysis.shape
        if shape is None or not _could_be_eclipse(shape):
            return _no_eclipse(self.name, type_code, shape)
        depth = shape.secondary_depth
        stated = f"The secondary minimum is {depth:.2f} of the primary's depth"
        if TYPE_SPECS[type_code].secondary_minimum == "equal":
            if depth >= EQUAL_MINIMA:
                return Evidence(
                    self.name,
                    type_code,
                    "supports",
                    HARD_WEIGHT,
                    f"{stated}: near-equal minima, as {type_code}'s are.",
                )
            if depth < UNEQUAL_MINIMA:
                return Evidence(
                    self.name,
                    type_code,
                    "against",
                    HARD_WEIGHT,
                    f"{stated}, too unequal for {type_code}.",
                )
            return Evidence(self.name, type_code, "neutral", 0.0, f"{stated}.")
        low, high = SHALLOWER_SECONDARY
        if depth < ABSENT_SECONDARY:
            return Evidence(
                self.name,
                type_code,
                "against",
                HARD_WEIGHT,
                f"{stated}: none, and {type_code} always has one.",
            )
        if depth > NEAR_EQUAL_SECONDARY:
            return Evidence(
                self.name,
                type_code,
                "against",
                HARD_WEIGHT,
                f"{stated}: near-equal minima, as EW's are, not {type_code}'s.",
            )
        if low <= depth <= high:
            return Evidence(
                self.name,
                type_code,
                "supports",
                HARD_WEIGHT,
                f"{stated}: present and considerably shallower, as {type_code}'s is.",
            )
        return Evidence(self.name, type_code, "neutral", 0.0, f"{stated}.")


class Contacts:
    name = "eclipse_contacts"
    applies_to = frozenset({"EA", "EB", "EW"})

    def evaluate(self, type_code: str, analysis: Analysis, target: Target) -> Evidence:
        shape = analysis.shape
        if shape is None or not _could_be_eclipse(shape):
            return _no_eclipse(self.name, type_code, shape)
        flat = shape.flat_fraction
        stated = (
            f"The light is within a tenth of the amplitude of maximum for {flat:.2f} of the cycle"
        )
        constant = f"{stated}, nearly constant between eclipses with readable contacts"
        continuous = f"{stated}, varying continuously between eclipses"
        if type_code == "EA":
            if flat >= FLAT_MAXIMA:
                return Evidence(
                    self.name, type_code, "supports", HARD_WEIGHT, f"{constant}, as EA's is."
                )
            weight = HARD_WEIGHT if flat < CONTINUOUS_LIGHT else SOFT_WEIGHT
            return Evidence(
                self.name, type_code, "against", weight, f"{continuous}, which EA does not."
            )
        if flat < CONTINUOUS_LIGHT:
            return Evidence(
                self.name,
                type_code,
                "supports",
                SOFT_WEIGHT,
                f"{continuous}, as {type_code}'s does.",
            )
        if flat >= FLAT_MAXIMA:
            return Evidence(
                self.name, type_code, "against", HARD_WEIGHT, f"{constant}, as EA's is."
            )
        return Evidence(self.name, type_code, "neutral", 0.0, f"{stated}.")


def _could_be_eclipse(shape: Shape) -> bool:
    return shape.eclipse_width < BROAD_MIN_WIDTH


def _unmeasured(rule: str, code: str) -> Evidence:
    return Evidence(
        rule, code, "neutral", 0.0, f"No folded light curve to judge {code}'s minima by."
    )


def _no_eclipse(rule: str, code: str, shape: Shape | None) -> Evidence:
    if shape is None:
        return _unmeasured(rule, code)
    return Evidence(
        rule,
        code,
        "neutral",
        0.0,
        f"The primary minimum is {shape.eclipse_width:.2f} of the cycle wide, no eclipse to judge.",
    )
