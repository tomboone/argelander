"""Variability: does the Target vary beyond its errors at all (spec §7, `CST` and `L`)?

What separates a constant star from a slow irregular variable, when neither has a period,
is whether its magnitudes spread further than their errors explain. Only surveys the blend
checks found clean are measured: a contaminated survey's spread could be a neighbour's.
"""

from argelander.analysis.model import Analysis
from argelander.classify.rules.base import Evidence
from argelander.classify.types import (
    CONSTANT,
    CONSTANT_EXCESS,
    HARD_WEIGHT,
    SOFT_WEIGHT,
    VARIABLE_EXCESS,
)
from argelander.db.models import Target


class VariabilityRule:
    name = "variability"
    applies_to = frozenset({"L", CONSTANT})

    def evaluate(self, type_code: str, analysis: Analysis, target: Target) -> Evidence:
        measured = analysis.variability
        if measured is None:
            # L is defined by its variability alone, so unmeasured it is unconfirmed: the
            # weight dilutes whatever its colour and place on the diagram say for it.
            return Evidence(
                self.name,
                type_code,
                "neutral",
                HARD_WEIGHT if type_code == "L" else 0.0,
                "Every survey is contaminated, so the Target's own variability cannot be measured.",
            )
        stated = (
            f"The {measured.survey.upper()} {measured.band} magnitudes spread "
            f"{measured.spread:.3f} mag, {measured.excess:.1f} times what their errors give"
        )
        if measured.excess >= VARIABLE_EXCESS:
            if type_code == CONSTANT:
                return Evidence(
                    self.name, type_code, "against", HARD_WEIGHT, f"{stated}: the Target varies."
                )
            return Evidence(
                self.name, type_code, "supports", SOFT_WEIGHT, f"{stated}: the Target varies."
            )
        if measured.excess <= CONSTANT_EXCESS:
            if type_code == CONSTANT:
                return Evidence(
                    self.name,
                    type_code,
                    "supports",
                    SOFT_WEIGHT,
                    f"{stated}: constant within its errors.",
                )
            return Evidence(
                self.name,
                type_code,
                "against",
                HARD_WEIGHT,
                f"{stated}: constant within its errors.",
            )
        return Evidence(self.name, type_code, "neutral", 0.0, f"{stated}.")
