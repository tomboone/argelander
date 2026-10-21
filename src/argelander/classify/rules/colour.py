"""Colour: is the Target's Gaia BP−RP the colour the type's spectral class gives (spec §7)?

Each type document's spectral class becomes a colour hint, blue (B–A), intermediate or red
(late K and M), bounded in `types.COLOUR_BOUNDS`. Interstellar reddening only ever makes a
star redder, so a star bluer than its type allows is firm evidence against it and one redder
weak evidence; a colour that fits is weak support, since a light curve's shape names a type
before its colour does. ROT takes BY's hint, as VSX points ROT at BY.
"""

from argelander.analysis.model import Analysis
from argelander.classify.rules.base import Evidence
from argelander.classify.types import (
    COLOUR_AGAINST_WEIGHT,
    COLOUR_BOUNDS,
    COLOUR_REDDENED_WEIGHT,
    COLOUR_SUPPORT_WEIGHT,
    GUIDANCE,
    TYPE_SPECS,
    ColourHint,
)
from argelander.db.models import Target


def _hint(code: str) -> ColourHint:
    return TYPE_SPECS[GUIDANCE.get(code, code)].colour_hint


class Colour:
    name = "colour"
    applies_to = frozenset(code for code in TYPE_SPECS if _hint(code) != "any")

    def evaluate(self, type_code: str, analysis: Analysis, target: Target) -> Evidence:
        bp_rp = analysis.cmd.bp_rp if analysis.cmd is not None else target.gaia_bp_rp
        if bp_rp is None:
            return Evidence(self.name, type_code, "neutral", 0.0, "Gaia gives no BP−RP colour.")
        hint = _hint(type_code)
        fits_from, fits_to, too_blue, too_red = COLOUR_BOUNDS[hint]
        stated = f"BP−RP of {bp_rp:.2f} is"
        wanted = f"the {hint} colour of {type_code}"
        if fits_from <= bp_rp <= fits_to:
            return Evidence(
                self.name, type_code, "supports", COLOUR_SUPPORT_WEIGHT, f"A {stated} {wanted}."
            )
        if bp_rp < too_blue:
            return Evidence(
                self.name,
                type_code,
                "against",
                COLOUR_AGAINST_WEIGHT,
                f"A {stated} bluer than {wanted}, and reddening cannot make a star bluer.",
            )
        if bp_rp > too_red:
            return Evidence(
                self.name,
                type_code,
                "against",
                COLOUR_REDDENED_WEIGHT,
                f"A {stated} redder than {wanted}, unless interstellar reddening explains it.",
            )
        return Evidence(
            self.name, type_code, "neutral", 0.0, f"A {stated} near the edge of {wanted}."
        )
