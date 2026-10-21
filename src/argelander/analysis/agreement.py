"""The Agreement rule: a period is accepted only when two independent sources find it.

Spec §6 step 6. The sources are ZTF g, ZTF r, ASAS-SN, TESS and Gaia's published `vari_*`
period; each votes with the best period its alias test settled on. Two votes agree when
they are within the tolerance of each other, or when one is twice the other: an eclipsing
binary's two eclipses fold onto each other at half its period, so one survey can settle on
P/2 while another separates the eclipses at P. Such an Agreement is recorded as `2:1` and
takes the longer period, the one that separates them; the classifier sees the relation and
can weigh it (VSX's `ELL|DSCT` trap is the case where the shorter period is right).

The tolerance is `TOLERANCE`, or a vote's period over its baseline when that is wider (NEU-1711).
A periodogram peak is about 1 / baseline wide in frequency, so two surveys can resolve a
period no more finely than P / baseline of it: over ZTF's 7.5 years that is under 0.1 % below
2.7 d, but 1.5 % at 40 d, where a fixed 0.1 % would turn the same peak found twice into a
disagreement. Gaia's published period was found over Gaia DR3's 34 months (`GAIA_BASELINE`).

A band whose best fold leaves more than `MAX_SCATTER` of the variance unexplained has found no
period and does not vote. Its alias test still ranks something first, and on a constant star
that is a sampling period: ZTF g and r observing the same nights both settle on the sidereal
half-day, and would agree on it.

Gaia epoch photometry never votes: tens of epochs rarely settle a period, so it cannot
settle one by agreeing with a single other source. ZTF i is not a source either. A survey
observed in two bands (ASAS-SN V and g) is one source, since one instrument's sampling
puts the same aliases into both.

A survey the blend checks found contaminated (spec §6 step 7) counts as half a source:
its period may be a neighbour's, so the sources at a period must weigh as much as two clean
ones: a contaminated source and one clean source are not enough.

When two periods are found by equally many sources the sources disagree, and nothing is
accepted. The rule of the bulk imports, taking the single highest periodogram peak, is how
VSX came by most of the wrong periods this pipeline exists to correct.
"""

from collections.abc import Collection, Iterable
from dataclasses import dataclass

from argelander.analysis.model import Agreement, AliasTest

TOLERANCE = 1e-3  # 0.1 %
# Days from the first to the last observation in Gaia DR3 (2014-07-25 to 2017-05-28).
GAIA_BASELINE = 1038.0
# Phase dispersion above which a band's best fold explains too little to be a period. Set on
# the prototype fixtures (NEU-1711): every positive's voting band folds at 0.71 or less, every
# constant star's ZTF bands at 0.82 or more.
MAX_SCATTER = 0.75
SOURCES = ("ztf_g", "ztf_r", "asassn", "tess", "gaia_published")
# The survey each source's photometry comes from, as in `LightCurve.survey`.
SURVEYS = {
    "ztf_g": "ztf",
    "ztf_r": "ztf",
    "asassn": "asassn",
    "tess": "tess",
    "gaia_published": "gaia",
}
MIN_WEIGHT = 2.0  # two clean sources
CONTAMINATED_WEIGHT = 0.5


def source(survey: str, band: str) -> str | None:
    """The source a band's Light Curve votes as, or None when it never counts."""
    if survey == "ztf":
        return f"ztf_{band}" if band in ("g", "r") else None
    if survey in ("asassn", "tess"):
        return survey
    return None


@dataclass(frozen=True)
class Vote:
    """One source's period, and the baseline that sets how finely it is resolved."""

    source: str  # one of `SOURCES`
    period: float  # days
    baseline: float  # days; 0 when unknown


def votes(tests: Iterable[AliasTest], gaia_period: float | None = None) -> list[Vote]:
    """The votes of the bands that count as a source and whose best fold explains enough of
    their variance, plus Gaia's published period."""
    found = [
        Vote(name, test.best.period, test.baseline)
        for test in tests
        if test.best is not None
        and test.best.scatter <= MAX_SCATTER
        and (name := source(test.survey, test.band)) is not None
    ]
    if gaia_period is not None:
        found.append(Vote("gaia_published", gaia_period, GAIA_BASELINE))
    return found


def agreement(
    tests: Iterable[AliasTest],
    gaia_period: float | None = None,
    contaminated: Collection[str] = (),
) -> Agreement | None:
    """The period most sources agree on, if at least two do and no other period ties it.

    `contaminated` names the surveys whose sources count at half weight (`Blend.surveys`).
    """

    def weight(names: set[str]) -> float:
        return sum(CONTAMINATED_WEIGHT if SURVEYS[n] in contaminated else 1.0 for n in names)

    cast = votes(tests, gaia_period)
    found: list[Agreement] = []
    # Every vote is tried as the shorter period of a pair and as the longer, and as the
    # lowest of the votes within the tolerance above it, so no two agreeing votes are further
    # apart than that. A vote joins at the coarser resolution of its own baseline and the
    # anchor's, so whether two votes agree does not depend on which is the lower.
    anchors = {(v.period, v.baseline) for v in cast} | {(v.period / 2, v.baseline) for v in cast}
    for short, anchor_baseline in sorted(anchors):
        at_short = [v for v in cast if _above(v, short, anchor_baseline)]
        at_long = [v for v in cast if _above(v, 2 * short, anchor_baseline)]
        # A source whose bands fall at both scales votes at one: a scale that adds no
        # source is not evidence for a 2:1 relation.
        if {v.source for v in at_long} <= {v.source for v in at_short}:
            at_long = []
        elif {v.source for v in at_short} <= {v.source for v in at_long}:
            at_short = []
        names = {v.source for v in at_short + at_long}
        if weight(names) < MIN_WEIGHT:
            continue
        if at_short and at_long:
            periods = [2 * v.period for v in at_short] + [v.period for v in at_long]
        else:
            periods = [v.period for v in at_short or at_long]
        agreed = sum(periods) / len(periods)
        baseline = min([anchor_baseline] + [v.baseline for v in at_short + at_long])
        found.append(
            Agreement(
                agreed_period=agreed,
                agreed_sources=tuple(s for s in SOURCES if s in names),
                relation="2:1" if at_short and at_long else "1:1",
                tolerance=tolerance(agreed, baseline),
            )
        )
    if not found:
        return None
    most = max(weight(set(a.agreed_sources)) for a in found)
    best = [a for a in found if weight(set(a.agreed_sources)) == most]
    # The same votes are found from both of their periods; distinct periods are a conflict.
    reference = best[0].agreed_period
    if any(abs(a.agreed_period / reference - 1.0) > best[0].tolerance for a in best[1:]):
        return None
    return best[0]


def tolerance(period: float, baseline: float) -> float:
    """How far apart, as a fraction, two votes at `period` may be: `TOLERANCE`, or the
    period resolution of a `baseline`-day Light Curve when that is coarser."""
    return max(TOLERANCE, period / baseline) if baseline > 0 else TOLERANCE


def _above(vote: Vote, lowest: float, anchor_baseline: float) -> bool:
    """Whether `vote` lies at or above `lowest`, within the coarser of its own and the
    anchor's resolution."""
    baseline = min(vote.baseline, anchor_baseline)
    return 0.0 <= vote.period / lowest - 1.0 <= tolerance(lowest, baseline)
