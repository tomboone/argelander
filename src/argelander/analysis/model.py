"""The analyzer's results as plain dataclasses; M4 maps `Analysis` onto the `analyses` table.

Spec §4 fixes the row's columns and §6 the steps that fill them. Each step adds its own
field as it lands, so a field here is one the analyzer can already produce.
"""

from dataclasses import dataclass, field
from typing import Literal

import numpy as np

Method = Literal["lomb_scargle", "bls"]


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


@dataclass
class Analysis:
    """One Analysis per Target per Pipeline Version (spec §4 `analyses`)."""

    target_id: int
    pipeline_version: str
    periods: list[PeriodogramResult] = field(default_factory=list)
