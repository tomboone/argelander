"""The rule registry and Confidence (spec §7)."""

import pytest

from argelander.analysis.model import Analysis
from argelander.classify import registry
from argelander.classify.confidence import best_type, confidence, scores
from argelander.classify.rules.base import Evidence, Stance
from argelander.classify.types import TYPE_SPECS
from argelander.db.models import Target


def evidence(verdict: Stance, weight: float, type_code: str = "EW") -> Evidence:
    return Evidence(
        rule="test", type_code=type_code, verdict=verdict, weight=weight, explanation="."
    )


class ContactBinaryRule:
    name = "contact_binary"
    applies_to = frozenset({"EW"})

    def evaluate(self, type_code: str, analysis: Analysis, target: Target) -> Evidence:
        return evidence("supports", 1.0, type_code)


def test_all_supporting_evidence_is_full_confidence() -> None:
    assert confidence([evidence("supports", 1.0), evidence("supports", 0.5)]) == 1.0


def test_all_opposing_evidence_is_no_confidence() -> None:
    assert confidence([evidence("against", 1.0), evidence("against", 0.5)]) == 0.0


def test_mixed_evidence_is_proportional() -> None:
    # (1.0 − 0.5) / (1.0 + 0.5 + 0.5): neutral evidence dilutes but does not oppose.
    mixed = [evidence("supports", 1.0), evidence("against", 0.5), evidence("neutral", 0.5)]
    assert confidence(mixed) == pytest.approx(0.25)


def test_no_weight_is_no_confidence() -> None:
    assert confidence([]) == 0.0
    assert confidence([evidence("supports", 0.0)]) == 0.0


def test_evidence_weight_is_between_zero_and_one() -> None:
    with pytest.raises(ValueError, match="weight"):
        evidence("supports", 1.5)


def test_scores_are_per_type() -> None:
    scored = scores(
        [evidence("supports", 1.0, "EW"), evidence("against", 1.0, "EA"), evidence("supports", 1.0)]
    )
    assert scored == {"EW": 1.0, "EA": 0.0}


def test_best_type_is_the_highest_score() -> None:
    assert best_type({"EW": 0.4, "EB": 0.9, "EA": 0.1}) == "EB"
    assert best_type({}) is None


def test_a_tie_breaks_toward_gaias_type() -> None:
    assert best_type({"EW": 0.8, "EB": 0.8}) == "EW"
    assert best_type({"EW": 0.8, "EB": 0.8}, frozenset({"EB"})) == "EB"
    assert best_type({"EW": 0.8, "EB": 0.7}, frozenset({"EB"})) == "EW"


def test_a_rule_registered_for_ew_is_returned_for_ew_only(monkeypatch: pytest.MonkeyPatch) -> None:
    rule = ContactBinaryRule()
    monkeypatch.setattr(registry, "RULES", (rule,))
    assert registry.rules_for("EW") == (rule,)
    assert registry.rules_for("RRAB") == ()
    assert registry.all_rules() == (rule,)


def test_registered_rules_name_scored_types_and_unique_names() -> None:
    rules = registry.all_rules()
    assert all(rule.applies_to <= set(TYPE_SPECS) for rule in rules)
    assert len({rule.name for rule in rules}) == len(rules)
