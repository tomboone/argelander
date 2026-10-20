"""The Agreement rule: a period is accepted only when two independent sources find it.

Spec §6 step 6. The sources are ZTF g, ZTF r, ASAS-SN, TESS and Gaia's published `vari_*`
period; each votes with the best period its alias test settled on. Two votes agree when
they are within `TOLERANCE` of each other, or when one is twice the other: an eclipsing
binary's two eclipses fold onto each other at half its period, so one survey can settle on
P/2 while another separates the eclipses at P. Such an Agreement is recorded as `2:1` and
takes the longer period, the one that separates them; the classifier sees the relation and
can weigh it (VSX's `ELL|DSCT` trap is the case where the shorter period is right).

Gaia epoch photometry never votes: tens of epochs rarely settle a period, so it cannot
settle one by agreeing with a single other source. ZTF i is not a source either. A survey
observed in two bands (ASAS-SN V and g) is one source, since one instrument's sampling
puts the same aliases into both.

When two periods are found by equally many sources the sources disagree, and nothing is
accepted. The rule of the bulk imports, taking the single highest periodogram peak, is
how VSX came by most of the wrong periods this pipeline exists to correct.
"""

from collections.abc import Iterable

from argelander.analysis.model import Agreement, AliasTest

TOLERANCE = 1e-3  # 0.1 %
SOURCES = ("ztf_g", "ztf_r", "asassn", "tess", "gaia_published")


def source(survey: str, band: str) -> str | None:
    """The source a band's Light Curve votes as, or None when it never counts."""
    if survey == "ztf":
        return f"ztf_{band}" if band in ("g", "r") else None
    if survey in ("asassn", "tess"):
        return survey
    return None


def agreement(tests: Iterable[AliasTest], gaia_period: float | None = None) -> Agreement | None:
    """The period most sources agree on, if at least two do and no other period ties it."""
    votes = [
        (name, test.best.period)
        for test in tests
        if test.best is not None and (name := source(test.survey, test.band)) is not None
    ]
    if gaia_period is not None:
        votes.append(("gaia_published", gaia_period))

    found: list[Agreement] = []
    # Every vote is tried as the shorter period of a pair and as the longer, and as the
    # lowest of the votes within `TOLERANCE` above it, so no two agreeing votes are further
    # apart than that.
    for short in sorted({p for _, p in votes} | {p / 2 for _, p in votes}):
        at_short = [(n, p) for n, p in votes if _above(p, short)]
        at_long = [(n, p) for n, p in votes if _above(p, 2 * short)]
        # A source whose bands fall at both scales votes at one: a scale that adds no
        # source is not evidence for a 2:1 relation.
        if {n for n, _ in at_long} <= {n for n, _ in at_short}:
            at_long = []
        elif {n for n, _ in at_short} <= {n for n, _ in at_long}:
            at_short = []
        names = {n for n, _ in at_short + at_long}
        if len(names) < 2:
            continue
        if at_short and at_long:
            periods = [2 * p for _, p in at_short] + [p for _, p in at_long]
        else:
            periods = [p for _, p in at_short or at_long]
        found.append(
            Agreement(
                agreed_period=sum(periods) / len(periods),
                agreed_sources=tuple(s for s in SOURCES if s in names),
                relation="2:1" if at_short and at_long else "1:1",
            )
        )
    if not found:
        return None
    most = max(len(a.agreed_sources) for a in found)
    best = [a for a in found if len(a.agreed_sources) == most]
    # The same votes are found from both of their periods; distinct periods are a conflict.
    if any(not _near(a.agreed_period, best[0].agreed_period) for a in best[1:]):
        return None
    return best[0]


def _above(period: float, lowest: float) -> bool:
    return 0.0 <= period / lowest - 1.0 <= TOLERANCE


def _near(period: float, reference: float) -> bool:
    return abs(period / reference - 1.0) <= TOLERANCE
