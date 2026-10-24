"""The prototype gate (spec §11, NEU-1711): does each known star's Analysis say what its
published values say?

The stars are the fixture manifest's (`tests/fixtures/manifest.yaml`), passed in as the
plain mappings YAML gives. `judge` applies the pass criterion to one star, so the gate test
and the prototype notebook hold the pipeline to the same rule:

- a positive reaches Agreement at its published period, within the Agreement's own
  tolerance (0.1 %, wider at long periods; `agreement.tolerance`) or at twice or half it
  (`2:1`, recorded as such);
- an alias trap reaches no Agreement at its published period, the alias; when it reaches
  one at all, it is at `true_period`, the period the published one is an alias of;
- a blend or a constant star reaches no Agreement, or is flagged `blend_suspect`.

A survey with no Light Curve for a star (ASAS-SN until NEU-1796) is a coverage gap: the star
is judged on the sources it has.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from argelander.analysis.agreement import TOLERANCE
from argelander.analysis.model import Analysis, Relation


@dataclass(frozen=True)
class GateResult:
    """One star's result at the gate."""

    star: str
    passed: bool
    # How the agreed period stands to the published one: "1:1", "2:1", or None when there is
    # no Agreement or it is at neither.
    relation: Relation | None
    reason: str  # one sentence, shown in the notebook and in a failing test's message


def relation(period: float, reference: float, tolerance: float = TOLERANCE) -> Relation | None:
    """ "1:1" when `period` is `reference` within `tolerance`, "2:1" when it is twice or half
    it, else None."""
    if abs(period / reference - 1.0) <= tolerance:
        return "1:1"
    if any(abs(period / (reference * k) - 1.0) <= tolerance for k in (2.0, 0.5)):
        return "2:1"
    return None


def judge(star: Mapping[str, Any], analysis: Analysis) -> GateResult:
    """Apply the pass criterion for `star`'s role to its Analysis."""
    name, found = star["star"], analysis.agreement

    def at(reference: float | None) -> Relation | None:
        if found is None or reference is None:
            return None
        return relation(found.agreed_period, reference, found.tolerance)

    published = star["published_period"]
    if star["role"] == "positive":
        if found is None:
            return GateResult(name, False, None, "No Agreement.")
        agreed = found.agreed_period
        if at(published) is None:
            return GateResult(
                name, False, None, f"Agreed {agreed:.7g} d, not the published {published:.7g} d."
            )
        return GateResult(name, True, at(published), f"Agreed {agreed:.7g} d ({at(published)}).")

    if star["negative_kind"] == "alias_trap":
        if found is None:
            return GateResult(name, True, None, "No Agreement.")
        agreed, true = found.agreed_period, star["true_period"]
        if at(published) is not None:
            return GateResult(
                name, False, at(published), f"Agreed {agreed:.7g} d, the published alias."
            )
        if at(true) is None:
            return GateResult(
                name, False, None, f"Agreed {agreed:.7g} d, neither the alias nor {true:.7g} d."
            )
        return GateResult(name, True, None, f"Agreed the true period, {agreed:.7g} d ({at(true)}).")

    flags = analysis.blend.flags if analysis.blend is not None else frozenset()
    if "blend_suspect" in flags:
        return GateResult(name, True, at(published), "Flagged blend_suspect.")
    if found is None:
        return GateResult(name, True, None, "No Agreement.")
    return GateResult(
        name,
        False,
        at(published),
        f"Agreed {found.agreed_period:.7g} d and not flagged blend_suspect.",
    )
