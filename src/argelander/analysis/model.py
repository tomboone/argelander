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
    # Lowest score first; empty when insufficient. After a ZTF band is re-aligned
    # (`aliases.refold`) the best stays first, scored on the re-aligned curve, and the rest
    # keep their scores from the curve before it.
    ranking: tuple[Hypothesis, ...]
    # Days from the cleaned Light Curve's first point to its last, which sets how finely it
    # resolves a period; 0 when unknown, and then only the fixed tolerance applies.
    baseline: float = 0.0

    @property
    def best(self) -> Hypothesis | None:
        return self.ranking[0] if self.ranking else None


@dataclass(frozen=True)
class Agreement:
    """A period at least two independent sources found, after alias tests."""

    agreed_period: float  # days
    agreed_sources: tuple[str, ...]  # in `agreement.SOURCES` order
    relation: Relation  # "2:1" when some sources found half `agreed_period`
    # How far apart, as a fraction, the votes at `agreed_period` were allowed to be: 0.1 %,
    # or the period over the shortest voting baseline when that is wider.
    tolerance: float


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
class ZeroPointOffsets:
    """One ZTF band's oid zero points compared at equal phase (spec §6 step 7)."""

    band: str
    period: float  # the band's alias-test best period, which the oids were folded at
    bins: int  # phase bins, as the alias test cut the fold
    reference_oid: str  # the fetcher's reference oid, which defines zero
    # Non-reference oid → the magnitudes that align its raw points with the band's other oids
    # at the same phase, in the sign of the fetcher's `meta["zero_point_offsets"]`. With two
    # oids that is its zero point relative to the reference; with more, the others' template
    # carries their own median shifts, so it is relative to them as merged.
    offsets: dict[str, float]
    # Measured oid → the magnitudes the analyzer adds to its points to re-align the band
    # (NEU-1802): its offset less the fetcher's shift, which is how wrong that shift was at
    # equal phase. Unmeasured oids keep the fetcher's shift and are not listed.
    realignment: dict[str, float]
    unmeasured: tuple[str, ...]  # oids with too few points in bins the others populate
    suspect: bool  # any |offset| above `blend.BLEND_OFFSET_MAG`


@dataclass(frozen=True)
class Blend:
    """The per-survey blend checks for one Target (spec §6 step 7)."""

    contaminations: tuple[Contamination, ...]  # one per contaminated survey
    # One per ZTF band whose zero points could be compared, flagged or not.
    zero_points: tuple[ZeroPointOffsets, ...] = ()

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


@dataclass(frozen=True)
class Shape:
    """The light curve's shape, folded at the agreed period in one band, for the classifier's
    shape rules (spec §7). Magnitudes throughout; a flux band is converted first.

    Phases are fractions of the cycle. The eclipse measures come from the binned fold, which
    keeps a narrow eclipse's depth; the pulsation measures from a Fourier fit, which keeps a
    steep rise smooth.
    """

    survey: str
    band: str
    n_points: int
    period: float  # days, the Agreement's
    amplitude: float  # mag in `band`, faintest less brightest bin of the binned fold
    amplitude_v: float  # `amplitude` scaled to V (`types.AMPLITUDE_TO_V`), as VSX's ranges are
    snr: float  # `amplitude` over the scatter of the points about the binned fold
    rise: float  # minimum to maximum light, as a fraction of the cycle
    skewness: float  # of the fitted curve's magnitudes; negative when maxima are sharp
    r21: float  # Fourier amplitude ratio A2 / A1
    eclipse_width: float  # of the cycle around primary minimum fainter than half its depth
    secondary_depth: float  # deepest point 0.3–0.7 of a cycle after primary minimum / its depth
    flat_fraction: float  # of the cycle within a tenth of the amplitude of maximum light


@dataclass(frozen=True)
class Variability:
    """How far one band's spread exceeds its noise, period or not (spec §7, `CST` and `L`)."""

    survey: str
    band: str
    n_points: int
    spread: float  # mag, the 5th to the 95th percentile
    noise: float  # mag, the median error
    excess: float  # `spread` over the spread noise alone would give, 3.29 × `noise`


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
    shape: Shape | None = None  # None without Agreement or a band to fold
    variability: Variability | None = None  # None without an uncontaminated band
