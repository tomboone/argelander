"""The rules the classifier runs, and which of them bear on each VSX type (spec §7)."""

from argelander.classify.rules.base import Rule

# Every rule, in the order its Evidence is listed. The initial rule set arrives with NEU-1720.
RULES: tuple[Rule, ...] = ()


def all_rules() -> tuple[Rule, ...]:
    return RULES


def rules_for(type_code: str) -> tuple[Rule, ...]:
    """The rules whose `applies_to` names `type_code`."""
    return tuple(rule for rule in RULES if type_code in rule.applies_to)
