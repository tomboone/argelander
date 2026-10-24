"""Colour-magnitude region: does the Target sit where the type belongs (spec §7)?

An RR Lyrae star belongs in the instability strip, a W UMa binary on the main sequence, a
semiregular or Mira variable on the giant branch (`types.EXPECTED_REGIONS`). The regions are
coarse polygons in observed colours, so the evidence weighs half. Without a significant
parallax the region is unknown and no evidence.
"""

from argelander.analysis.model import Analysis
from argelander.classify.rules.base import Evidence
from argelander.classify.types import CMD_WEIGHT, EXPECTED_REGIONS
from argelander.db.models import Target


class CmdRegion:
    name = "cmd_region"
    applies_to = frozenset(EXPECTED_REGIONS)

    def evaluate(self, type_code: str, analysis: Analysis, target: Target) -> Evidence:
        cmd = analysis.cmd
        if cmd is None or cmd.region == "unknown" or cmd.abs_g is None or cmd.bp_rp is None:
            return Evidence(
                self.name,
                type_code,
                "neutral",
                0.0,
                "Without a significant parallax and a colour the Target has no place on the "
                "colour-magnitude diagram.",
            )
        expected = EXPECTED_REGIONS[type_code]
        place = (
            f"The Target sits in the {_label(cmd.region)} region of Gaia's colour-magnitude "
            f"diagram (BP−RP {cmd.bp_rp:.2f}, M_G {cmd.abs_g:.2f})"
        )
        if cmd.region in expected:
            return Evidence(
                self.name, type_code, "supports", CMD_WEIGHT, f"{place}, where {type_code} belongs."
            )
        wanted = " or ".join(sorted(_label(r) for r in expected))
        return Evidence(
            self.name,
            type_code,
            "against",
            CMD_WEIGHT,
            f"{place}, not the {wanted} of {type_code}.",
        )


def _label(region: str) -> str:
    return region.replace("_", " ")
