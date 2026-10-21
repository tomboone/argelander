"""Cepheid luminosity: classical or Type II, from the period–Wesenheit relation (spec §7).

A classical Cepheid (DCEP, DCEPS) and a Type II one (CWA, CWB) share periods, but a Type II
Cepheid is 0.7–2 mag fainter at the same period. Gaia's Wesenheit magnitude,
W = G − 1.90 (BP − RP), is free of reddening, so with a significant parallax its distance
below the classical relation tells them apart: DCEP's own colour-magnitude evidence suffers
from reddening that W does not. A star far fainter or brighter than either relation is no
Cepheid. A first-overtone period is fundamentalised before it is compared.
"""

import math

from argelander.analysis.model import Analysis
from argelander.classify.rules.base import Evidence
from argelander.classify.types import (
    CW_FAINTER,
    DCEP_MAX_FAINTER,
    DCEP_PW,
    HARD_WEIGHT,
    NOT_CEPHEID_BRIGHTER,
    NOT_CEPHEID_FAINTER,
    OVERTONE_RATIO,
    WESENHEIT_COLOUR,
)
from argelander.db.models import Target

CLASSICAL = frozenset({"DCEP", "DCEPS"})
TYPE_II = frozenset({"CWA", "CWB"})


class CepheidLuminosity:
    name = "cepheid_luminosity"
    applies_to = CLASSICAL | TYPE_II

    def evaluate(self, type_code: str, analysis: Analysis, target: Target) -> Evidence:
        cmd, agreement = analysis.cmd, analysis.agreement
        if agreement is None or cmd is None or cmd.abs_g is None or cmd.bp_rp is None:
            return Evidence(
                self.name,
                type_code,
                "neutral",
                0.0,
                "Without an agreed period, a significant parallax and a colour there is no "
                "Wesenheit magnitude to compare.",
            )
        period = agreement.agreed_period
        if type_code == "DCEPS":
            period /= OVERTONE_RATIO
        intercept, slope = DCEP_PW
        fainter = (
            cmd.abs_g - WESENHEIT_COLOUR * cmd.bp_rp - (intercept + slope * math.log10(period))
        )
        stated = (
            f"The Wesenheit magnitude is {fainter:+.2f} mag from the classical-Cepheid "
            f"relation at {period:.4g} d"
        )
        if not NOT_CEPHEID_BRIGHTER <= fainter <= NOT_CEPHEID_FAINTER:
            return Evidence(
                self.name, type_code, "against", HARD_WEIGHT, f"{stated}, too far for any Cepheid."
            )
        low, high = CW_FAINTER
        classical = fainter <= DCEP_MAX_FAINTER
        type_ii = low <= fainter <= high
        if not classical and not type_ii:
            return Evidence(
                self.name, type_code, "neutral", 0.0, f"{stated}, between the two relations."
            )
        kind = (
            "a classical Cepheid's"
            if classical
            else f"a Type II Cepheid's, {low:g}–{high:g} mag fainter"
        )
        fits = classical if type_code in CLASSICAL else type_ii
        return Evidence(
            self.name,
            type_code,
            "supports" if fits else "against",
            HARD_WEIGHT,
            f"{stated}, {kind}.",
        )
