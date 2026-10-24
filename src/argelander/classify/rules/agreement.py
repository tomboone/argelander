"""Agreement: no periodic type without a period two sources agree on (spec §6 step 6, §7).

Without Agreement every periodic type is strongly opposed, so a Target whose sources disagree
is proposed as `VAR` with the reason in its evidence. With Agreement, L, which has no
periodicity, and CST are opposed instead; the period itself is judged by the range rules.
"""

from argelander.analysis.model import Analysis
from argelander.classify.rules.base import Evidence
from argelander.classify.types import (
    CONSTANT,
    HARD_WEIGHT,
    PERIODIC_TYPES,
    SOFT_WEIGHT,
)
from argelander.db.models import Target


class AgreementRule:
    name = "agreement"
    applies_to = PERIODIC_TYPES | {"L", CONSTANT}

    def evaluate(self, type_code: str, analysis: Analysis, target: Target) -> Evidence:
        found = analysis.agreement
        if found is None:
            if type_code in PERIODIC_TYPES:
                return Evidence(
                    self.name,
                    type_code,
                    "against",
                    HARD_WEIGHT,
                    f"No two sources agree on a period, so {type_code}, a periodic type, "
                    "cannot be proposed.",
                )
            if type_code == CONSTANT:
                return Evidence(
                    self.name,
                    type_code,
                    "supports",
                    SOFT_WEIGHT,
                    "No two sources agree on a period, as for a constant star.",
                )
            return Evidence(
                self.name, type_code, "neutral", 0.0, "No two sources agree on a period."
            )
        stated = (
            f"{len(found.agreed_sources)} sources ({', '.join(found.agreed_sources)}) agree on "
            f"a period of {found.agreed_period:.6g} d"
        )
        if type_code in PERIODIC_TYPES:
            return Evidence(self.name, type_code, "neutral", 0.0, f"{stated}.")
        unlike = "a constant star" if type_code == CONSTANT else "L, which has no periodicity"
        return Evidence(self.name, type_code, "against", HARD_WEIGHT, f"{stated}, unlike {unlike}.")
