"""Period and amplitude against each type's ranges in VSX's type designations (spec §7).

A value past a hard bound opposes the type outright and one past a soft bound ("usually")
about half as much; a value inside supports the type as strongly as its firmest bound. A
type VSX gives no range for is no evidence either way, weighed so that it is not confirmed
by fewer checks. ROT is scored against BY's ranges, as soft bounds, since VSX points there.
"""

from argelander.analysis.model import Analysis
from argelander.classify.rules.base import Evidence, Stance
from argelander.classify.types import (
    GUIDANCE,
    HARD_WEIGHT,
    PERIODIC_TYPES,
    SOFT_WEIGHT,
    TYPE_SPECS,
    UNSTATED_WEIGHT,
    Range,
)
from argelander.db.models import Target


class PeriodRange:
    name = "period_range"
    applies_to = PERIODIC_TYPES

    def evaluate(self, type_code: str, analysis: Analysis, target: Target) -> Evidence:
        if analysis.agreement is None:
            return Evidence(
                self.name,
                type_code,
                "neutral",
                0.0,
                f"No period was agreed to set against {type_code}'s range.",
            )
        period = analysis.agreement.agreed_period
        return _judge(self.name, type_code, "period", f"{period:.6g} d", period, "d")


class AmplitudeRange:
    name = "amplitude_range"
    applies_to = PERIODIC_TYPES

    def evaluate(self, type_code: str, analysis: Analysis, target: Target) -> Evidence:
        shape = analysis.shape
        if shape is None:
            return Evidence(
                self.name,
                type_code,
                "neutral",
                0.0,
                f"No folded light curve to measure an amplitude for {type_code}.",
            )
        measured = (
            f"{shape.amplitude_v:.2f} mag in V "
            f"({shape.amplitude:.2f} in {shape.survey.upper()} {shape.band})"
        )
        return _judge(self.name, type_code, "amplitude", measured, shape.amplitude_v, "mag")


def _judge(rule: str, code: str, what: str, measured: str, value: float, unit: str) -> Evidence:
    guide = GUIDANCE.get(code)
    spec = TYPE_SPECS[guide or code]
    bounds: Range = getattr(spec, f"{what}_range")
    stance, weight, inside = _stance(value, bounds, soft_only=guide is not None)
    if stance == "neutral":
        return Evidence(rule, code, stance, weight, f"VSX states no {what} range for {code}.")
    owner = (
        f"{guide}'s range, which VSX gives as guidance for {code}," if guide else f"{code}'s range"
    )
    where = "inside" if inside else "outside"
    return Evidence(
        rule,
        code,
        stance,
        weight,
        f"The {what} of {measured} is {where} {owner} of {_describe(bounds, unit)}.",
    )


def _stance(value: float, bounds: Range, *, soft_only: bool) -> tuple[Stance, float, bool]:
    """(stance, weight, inside every bound) for `value` against `bounds`."""
    hard_weight = SOFT_WEIGHT if soft_only else HARD_WEIGHT
    if not _within(value, bounds.low, bounds.high):
        return "against", hard_weight, False
    if not _within(value, bounds.soft_low, bounds.soft_high):
        return "against", SOFT_WEIGHT, False
    if bounds.low is not None or bounds.high is not None:
        return "supports", hard_weight, True
    if bounds.soft_low is not None or bounds.soft_high is not None:
        return "supports", SOFT_WEIGHT, True
    return "neutral", UNSTATED_WEIGHT, True


def _within(value: float, low: float | None, high: float | None) -> bool:
    return (low is None or value >= low) and (high is None or value <= high)


def _describe(bounds: Range, unit: str) -> str:
    """e.g. "0.3 to 1 d", "at most 0.3 mag, usually at most 0.1"."""
    hard = _span(bounds.low, bounds.high)
    soft = _span(bounds.soft_low, bounds.soft_high)
    if hard and soft:
        return f"{hard} {unit}, usually {soft}"
    return f"{hard} {unit}" if hard else f"usually {soft} {unit}"


def _span(low: float | None, high: float | None) -> str:
    if low is not None and high is not None:
        return f"{low:g} to {high:g}"
    if low is not None:
        return f"at least {low:g}"
    if high is not None:
        return f"at most {high:g}"
    return ""
