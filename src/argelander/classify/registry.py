"""The rules the classifier runs, and which of them bear on each VSX type (spec §7)."""

from argelander.analysis.model import Analysis
from argelander.classify.rules.agreement import AgreementRule
from argelander.classify.rules.base import Evidence, Rule
from argelander.classify.rules.cmd import CmdRegion
from argelander.classify.rules.colour import Colour
from argelander.classify.rules.eclipse import Contacts, EclipseWidth, SecondaryDepth
from argelander.classify.rules.gaia import GaiaClass
from argelander.classify.rules.luminosity import CepheidLuminosity
from argelander.classify.rules.pulsation import Asymmetry, FourierRatio, HadsSplit, Skewness
from argelander.classify.rules.ranges import AmplitudeRange, PeriodRange
from argelander.classify.rules.variability import VariabilityRule
from argelander.classify.types import SCORED_TYPES
from argelander.db.models import Target

# Every rule, in the order its Evidence is listed.
RULES: tuple[Rule, ...] = (
    AgreementRule(),
    PeriodRange(),
    AmplitudeRange(),
    EclipseWidth(),
    SecondaryDepth(),
    Contacts(),
    Asymmetry(),
    Skewness(),
    HadsSplit(),
    FourierRatio(),
    CepheidLuminosity(),
    CmdRegion(),
    Colour(),
    VariabilityRule(),
    GaiaClass(),
)


def all_rules() -> tuple[Rule, ...]:
    return RULES


def rules_for(type_code: str) -> tuple[Rule, ...]:
    """The rules whose `applies_to` names `type_code`."""
    return tuple(rule for rule in RULES if type_code in rule.applies_to)


def evaluate(analysis: Analysis, target: Target) -> list[Evidence]:
    """Every rule's Evidence for every type it bears on, type by type in `SCORED_TYPES`
    order, so that Confidence's first-scored tie-break favours the initial types."""
    return [
        rule.evaluate(code, analysis, target) for code in SCORED_TYPES for rule in rules_for(code)
    ]
