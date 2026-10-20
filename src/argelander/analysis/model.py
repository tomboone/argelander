"""The analyzer's results as plain dataclasses; M4 maps `Analysis` onto the `analyses` table.

Spec §4 fixes the row's columns and §6 the steps that fill them. Each step adds its own
field as it lands, so a field here is one the analyzer can already produce.
"""

from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from argelander.catalog.gaia import Neighbour

Method = Literal["lomb_scargle", "bls"]
Relation = Literal["1:1", "2:1"]
BlendFlag = Literal["blend_suspect", "asassn_contaminated", "tess_contaminated"]
Region = Literal["main_sequence", "giant_branch", "instability_strip", "white_dwarf", "unknown"]


@dataclass(frozen=True)
class Peak:
    period: float  # days
    power: float  # in the method's own units; not comparable across methods
    false_alarm_probability: float | None  # None for BLS, which has no analytic FAP


# eq=False: the generated __eq__ would compare arrays elementwise and fail on truthiness.
@dataclass(frozen=True, eq=False)
class PeriodogramResult:
    """One method's period search over one band's cleaned Light Curve."""

    survey: str
    band: str
    method: Method
    n_points: int  # after cleaning
    frequency: np.ndarray  # 1/d, ascending; empty when insufficient
    power: np.ndarray
    peaks: tuple[Peak, ...]  # the five highest local maxima, strongest first
    gaia: Peak | None  # the power at Gaia's published period, whenever one was supplied
    insufficient: bool  # under clean.MIN_POINTS points survived cleaning, or no baseline

    @property
    def best(self) -> Peak | None:
        return self.peaks[0] if self.peaks else None


@dataclass(frozen=True)
class Hypothesis:
    """One period a band's Light Curve was folded at: a candidate, or one of its aliases."""

    period: float  # days
    candidate: float  # the periodogram peak or Gaia period it was derived from
    relation: str  # how `period` follows from `candidate`: "P", "2P", "P/2", "1d+", "1d-", ...
    bins: int  # phase bins the fold was cut into
    scatter: float  # within-bin variance as a fraction of the total variance
    score: float  # the scatter penalised by `bins` (BIC); the ranking key, lower is better


@dataclass(frozen=True)
class AliasTest:
    """Every candidate of one band and its aliases, ranked by how cleanly each folds."""

    survey: str
    band: str
    ranking: tuple[Hypothesis, ...]  # lowest score first; empty when insufficient

    @property
    def best(self) -> Hypothesis | None:
        return self.ranking[0] if self.ranking else None


@dataclass(frozen=True)
class Agreement:
    """A period at least two independent sources found, after alias tests."""

    agreed_period: float  # days
    agreed_sources: tuple[str, ...]  # in `agreement.SOURCES` order
    relation: Relation  # "2:1" when some sources found half `agreed_period`


@dataclass(frozen=True)
class Contamination:
    """One survey's photometry of a Target is contaminated by sources inside its aperture."""

    survey: str  # "ztf" | "gaia" | "asassn" | "tess", as in `LightCurve.survey`
    flag: BlendFlag  # the flag that covers it; `blend_suspect` covers both ZTF and Gaia
    # The Gaia neighbours that set the flag, nearest first. Empty for TESS, whose aperture
    # reaches past the 20″ neighbour list, and for ZTF flagged by its zero-point offsets alone.
    neighbours: tuple[Neighbour, ...]
    # The share of the light measured in the aperture that comes from other sources, for a
    # COMBLEND-style range correction; None when it cannot be estimated.
    flux_fraction: float | None
    remark: str  # one sentence for VSX's Remarks, in the shape the VSX FAQ gives


@dataclass(frozen=True)
class Blend:
    """The per-survey blend checks for one Target (spec §6 step 7)."""

    contaminations: tuple[Contamination, ...]  # one per contaminated survey

    @property
    def flags(self) -> frozenset[BlendFlag]:
        return frozenset(c.flag for c in self.contaminations)

    @property
    def surveys(self) -> frozenset[str]:
        """The contaminated surveys, which count at half weight in Agreement."""
        return frozenset(c.survey for c in self.contaminations)

    def remark(self, survey: str) -> str | None:
        return next((c.remark for c in self.contaminations if c.survey == survey), None)


@dataclass(frozen=True)
class CmdPosition:
    """Where a Target sits on Gaia's colour-magnitude diagram (spec §6 step 8)."""

    bp_rp: float | None
    abs_g: float | None  # None unless the parallax is significant
    distance_pc: float | None  # 1000 / parallax, under the same condition
    region: Region


@dataclass
class Analysis:
    """One Analysis per Target per Pipeline Version (spec §4 `analyses`)."""

    target_id: int
    pipeline_version: str
    periods: list[PeriodogramResult] = field(default_factory=list)
    alias_tests: list[AliasTest] = field(default_factory=list)
    agreement: Agreement | None = None  # None when no two sources agree
    blend: Blend | None = None
    cmd: CmdPosition | None = None
