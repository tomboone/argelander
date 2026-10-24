"""Confidence: the Evidence for one type as a normalised weighted sum (spec §7)."""

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping

from argelander.classify.rules.base import Evidence


def confidence(evidence: Iterable[Evidence]) -> float:
    """(supporting − opposing weights) / all weights, clipped to 0–1; 0 without any weight.

    Neutral Evidence counts towards all weights, so it dilutes a score without opposing it.
    """
    supporting = opposing = total = 0.0
    for e in evidence:
        total += e.weight
        if e.verdict == "supports":
            supporting += e.weight
        elif e.verdict == "against":
            opposing += e.weight
    if total == 0.0:
        return 0.0
    return min(max((supporting - opposing) / total, 0.0), 1.0)


def scores(evidence: Iterable[Evidence]) -> dict[str, float]:
    """Confidence per type, for every type some Evidence bears on, in first-seen order."""
    by_type: defaultdict[str, list[Evidence]] = defaultdict(list)
    for e in evidence:
        by_type[e.type_code].append(e)
    return {code: confidence(items) for code, items in by_type.items()}


def best_type(
    type_scores: Mapping[str, float], gaia_types: frozenset[str] = frozenset()
) -> str | None:
    """The best-scoring type; a tie goes to a type Gaia's classifier gave (`types.gaia_types`),
    then to the first scored. None when nothing scored."""
    if not type_scores:
        return None
    top = max(type_scores.values())
    tied = [code for code, score in type_scores.items() if math.isclose(score, top)]
    return next((code for code in tied if code in gaia_types), tied[0])
