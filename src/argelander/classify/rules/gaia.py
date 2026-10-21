"""Gaia's classifier: weak support for the types its class covers (spec §7).

Gaia DR3's `vari_classifier_result` is the kind of bulk classification this pipeline exists
to check, so it never opposes a type and its support is weak. Its weights were not tuned on
Gaia's labels either, which would import the very classifications under review.
"""

from argelander.analysis.model import Analysis
from argelander.classify.rules.base import Evidence
from argelander.classify.types import (
    GAIA_CLASS_TYPES,
    GAIA_SUBCLASS_TYPES,
    GAIA_WEIGHT,
    gaia_types,
)
from argelander.db.models import Target


def target_gaia_types(target: Target) -> frozenset[str]:
    """The VSX types Gaia's classifier gave `target`."""
    detail = target.gaia_vari_detail or {}
    return gaia_types(target.gaia_vari_class, detail.get("vari_subclass"))


class GaiaClass:
    name = "gaia_class"
    applies_to = frozenset().union(*GAIA_CLASS_TYPES.values(), *GAIA_SUBCLASS_TYPES.values())

    def evaluate(self, type_code: str, analysis: Analysis, target: Target) -> Evidence:
        given = target.gaia_vari_class
        if given is None:
            return Evidence(
                self.name, type_code, "neutral", 0.0, "Gaia's classifier gave no class."
            )
        detail = target.gaia_vari_detail or {}
        subclass = detail.get("vari_subclass")
        named = f"{given} ({subclass})" if subclass else given
        if type_code in target_gaia_types(target):
            return Evidence(
                self.name,
                type_code,
                "supports",
                GAIA_WEIGHT,
                f"Gaia's classifier gave {named}, which covers {type_code}.",
            )
        return Evidence(
            self.name,
            type_code,
            "neutral",
            0.0,
            f"Gaia's classifier gave {named}, not {type_code}.",
        )
