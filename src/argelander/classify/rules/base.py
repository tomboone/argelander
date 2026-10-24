"""The `Rule` protocol and the `Evidence` a rule weighs in with (spec §7).

A rule bears on the types in its `applies_to` and is evaluated once for each of them, so one
rule, a period-range check say, can weigh in on every type against that type's `TypeSpec`.
"""

from dataclasses import dataclass
from typing import Literal, Protocol

from argelander.analysis.model import Analysis
from argelander.db.models import Target

# Not CONTEXT.md's Verdict, which is a VSX moderator's answer to a Submission.
Stance = Literal["supports", "against", "neutral"]


@dataclass(frozen=True)
class Evidence:
    """One rule's verdict on one type for one Target, kept on the Proposal as it stands."""

    rule: str  # the `Rule.name` that produced it
    type_code: str  # the VSX type it bears on
    verdict: Stance
    weight: float  # 0 to 1: 1.0 for a hard bound, about 0.5 for a soft one
    explanation: str  # one sentence, shown verbatim in the review app

    def __post_init__(self) -> None:
        if not 0.0 <= self.weight <= 1.0:
            raise ValueError(f"Evidence weight must be between 0 and 1, got {self.weight}")


class Rule(Protocol):
    name: str
    applies_to: frozenset[str]  # the VSX type codes this rule bears on

    def evaluate(self, type_code: str, analysis: Analysis, target: Target) -> Evidence:
        """The rule's Evidence for `type_code`, one of `applies_to`."""
        ...
