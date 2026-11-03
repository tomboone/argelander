"""Analysis → Proposal (spec §7), with the rerun semantics of spec §4 and the plots of §8.

`elements` reads the Proposal's fields off one Analysis: the rules' best type, the agreed
period, the epoch and the magnitude range from the Light Curves, and the full Evidence list.
`propose` writes them as a `queued` Proposal in the Analysis's transaction, supersedes the
Target's older `queued` Proposals, and draws the plots into `analyses.plots`.

A type outside the initial set is proposed as `VAR` with its evidence attached, and so is a
periodic type without Agreement: no two sources settled a period, and the Agreement rule's
Evidence says so. `CST` and `L` need no period and are proposed as they score. A `VAR`
Proposal carries the Confidence of the type that scored best, so a Target whose sources
disagree shows up low in the queue with the reason in its evidence.

The range and the epoch come from two bands. The range band (`range_curve`) is ASAS-SN V,
as VSX prefers magnitudes in V, when V has enough points and ASAS-SN is not contaminated: a
neighbour inside its 17" aperture adds light to every point, and a contaminated V range is
worse than a clean one in another band. Otherwise it is the epoch band (`epoch_curve`): the
most points among magnitude bands with a VSX passband, TESS's relative flux next and Gaia's
few dozen epochs last. The epoch, and the eclipse duration BLS fits, come from the epoch band
whatever the range band is: more points and a later baseline pin the fold down better, and
ASAS-SN V stopped in 2018. A tie in points goes by `BAND_ORDER`, never by list order, so a
Proposal reads the same bands every run.

TESS is a last resort for the range, since VSX takes a calibrated range in one band over a
mean and an amplitude; when it is all there is, `mag_max` is Gaia's mean G, `mag_min` the
TESS amplitude in magnitudes, and `min_is_amplitude` is set. Folded at the period, a band's
binned median is the curve the phase plot draws (`plots.phase.binned_median`): the range
band's brightest and faintest bins are the range, the epoch band's set the epoch.
`submission.png` leads with the range band, so the moderator sees the curve the range was
read from.

The durations are measured, not read off those bins, whose width (4 % of the cycle at 25
bins) is a third of an RR Lyrae star's rise: EA's eclipse is the box BLS fits at the period,
and a pulsator's rise is `Analysis.shape.rise`, from the Fourier fit the pulsation rules
already judged.

Rerun semantics: a rerun at the same Pipeline Version replaces the Analysis's own `queued`
Proposal in place, as the Analysis row itself is replaced. Any other `queued` Proposal for the
Target becomes `superseded`, pointing at the new one. An `approved` or `rejected` Proposal, or
one a Submission references, is never touched; the new one is queued beside it.
"""

from collections.abc import Collection
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from pathlib import Path

import numpy as np
from sqlalchemy import ColumnElement, exists, select, update
from sqlalchemy.orm import Session

from argelander.analysis.clean import sufficient
from argelander.analysis.model import Agreement, Analysis
from argelander.analysis.periodogram import eclipse_duration
from argelander.analysis.shape import magnitudes
from argelander.classify import registry
from argelander.classify.confidence import best_type, scores
from argelander.classify.rules.base import Evidence
from argelander.classify.rules.gaia import target_gaia_types
from argelander.classify.types import (
    APERIODIC_PERCENTILES,
    CONSTANT,
    CYCLE_MIN_POINTS,
    CYCLE_PERCENTILES,
    CYCLE_TO_CYCLE_TYPES,
    INITIAL_TYPES,
    PERIODIC_TYPES,
    STRONG_AGREEMENT_SOURCES,
    TYPE_SPECS,
    UNCERTAIN_BELOW,
    UNSPECIFIED,
)
from argelander.db.models import Analysis as AnalysisRow
from argelander.db.models import Proposal, ProposalStatus, Submission, Target, TargetStatus
from argelander.lightcurve import LightCurve
from argelander.plots import plot_dir, render_all
from argelander.plots.phase import binned_median, fold
from argelander.submission.form import PASSBANDS

# The band a Proposal's range is read from when it can be (spec §9: "preferably in V").
RANGE_BAND = ("asassn", "V")

# How a tie in points between candidate bands resolves, first wins: on trimmed fixtures every
# band can hold the same number of points, and list order would pick one by accident.
BAND_ORDER = {
    band: rank
    for rank, band in enumerate(
        [
            ("ztf", "g"),
            ("ztf", "r"),
            ("ztf", "i"),
            ("asassn", "g"),
            ("asassn", "V"),
            ("tess", "T"),
            ("gaia", "G"),
        ]
    )
}


@dataclass(frozen=True)
class Elements:
    """A Proposal's fields, as one Analysis and its Light Curves give them."""

    proposed_type: str
    confidence: float
    period: float | None  # days, the Agreement's
    epoch_bjd: float | None
    # The rise or eclipse duration as a fraction of the period (the form's `riseDur`): the
    # primary eclipse for EA, minimum to maximum for the asymmetric pulsators, else None.
    rise_or_eclipse: float | None
    mag_max: float | None
    mag_min: float | None  # an amplitude, not a magnitude, when `min_is_amplitude`
    min_is_amplitude: bool
    band: str | None  # the VSX passband of the range band, not of the epoch band
    evidence: tuple[Evidence, ...]

    @property
    def uncertain(self) -> bool:
        return self.confidence < UNCERTAIN_BELOW


def elements(analysis: Analysis, cleaned: list[LightCurve], target: Target) -> Elements:
    """The Proposal for one Analysis; `cleaned` are its cleaned, re-aligned Light Curves."""
    evidence = tuple(registry.evaluate(analysis, target))
    scored = scores(evidence)
    best = best_type(scored, target_gaia_types(target))
    code = proposed_type(best, analysis.agreement)
    agreement = analysis.agreement
    period = agreement.agreed_period if agreement and code not in {"L", CONSTANT} else None
    found = Elements(
        proposed_type=code,
        confidence=scored[best] if best is not None else 0.0,
        period=period,
        epoch_bjd=None,
        rise_or_eclipse=None,
        mag_max=None,
        mag_min=None,
        min_is_amplitude=False,
        band=None,
        evidence=evidence,
    )
    contaminated = analysis.blend.surveys if analysis.blend is not None else frozenset()
    ranged, timed = range_curve(cleaned, contaminated), epoch_curve(cleaned)
    if ranged is None or timed is None:
        return found
    return _read_curves(found, ranged, timed, analysis, target)


def proposed_type(best: str | None, agreement: Agreement | None) -> str:
    """The type a Proposal names: the best-scoring type when it is in the initial set, else
    `VAR`; and `VAR` for a periodic type without Agreement."""
    if best is None or best not in INITIAL_TYPES | {CONSTANT}:
        return UNSPECIFIED
    if agreement is None and best in PERIODIC_TYPES:
        return UNSPECIFIED
    return best


def range_curve(curves: list[LightCurve], contaminated: Collection[str]) -> LightCurve | None:
    """The band the range is read from and `submission.png` leads with: ASAS-SN V when it has
    enough points and ASAS-SN is not among the `contaminated` surveys, else `epoch_curve`."""
    if RANGE_BAND[0] not in contaminated:
        preferred = [lc for lc in _candidates(curves) if (lc.survey, lc.band) == RANGE_BAND]
        if preferred:
            return max(preferred, key=lambda lc: lc.time.size)
    return epoch_curve(curves)


def epoch_curve(curves: list[LightCurve]) -> LightCurve | None:
    """The band the epoch and eclipse duration are read from: the most points among bands with
    a VSX passband, magnitude bands before TESS flux, Gaia last, a tie by `BAND_ORDER`."""
    candidates = _candidates(curves)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda lc: (
            lc.survey != "gaia",
            lc.value_kind == "mag",
            lc.time.size,
            -BAND_ORDER[(lc.survey, lc.band)],
        ),
    )


def _candidates(curves: list[LightCurve]) -> list[LightCurve]:
    """The Light Curves a Proposal can read from: a VSX passband and enough points."""
    return [lc for lc in curves if (lc.survey, lc.band) in PASSBANDS and sufficient(lc)]


def propose(
    session: Session,
    row: AnalysisRow,
    analysis: Analysis,
    cleaned: list[LightCurve],
    target: Target,
    plots_dir: Path,
) -> Proposal:
    """Write the Analysis's Proposal, supersede the Target's older `queued` ones, move the
    Target to `proposed` and draw the plots. In the caller's transaction; does not commit."""
    found = elements(analysis, cleaned, target)
    proposal = session.scalars(
        select(Proposal).where(
            Proposal.analysis_id == row.id, Proposal.status == ProposalStatus.QUEUED, _unsubmitted()
        )
    ).first()
    if proposal is None:
        proposal = Proposal(analysis_id=row.id, target_id=target.id)
        session.add(proposal)
    proposal.pipeline_version = row.pipeline_version
    proposal.proposed_type = found.proposed_type
    proposal.uncertain = found.uncertain
    proposal.period = found.period
    proposal.epoch_bjd = found.epoch_bjd
    proposal.eclipse_duration_pct = (
        Decimal(f"{100 * found.rise_or_eclipse:.2f}") if found.rise_or_eclipse is not None else None
    )
    proposal.mag_max = found.mag_max
    proposal.mag_min = found.mag_min
    proposal.min_is_amplitude = found.min_is_amplitude
    proposal.band = found.band
    proposal.confidence = Decimal(f"{found.confidence:.3f}")
    proposal.evidence = [asdict(e) for e in found.evidence]
    proposal.status = ProposalStatus.QUEUED
    session.flush()

    session.execute(
        update(Proposal)
        .where(
            Proposal.target_id == target.id,
            Proposal.id != proposal.id,
            Proposal.status == ProposalStatus.QUEUED,
            _unsubmitted(),
        )
        .values(status=ProposalStatus.SUPERSEDED, superseded_by=proposal.id)
    )
    target.status = TargetStatus.PROPOSED
    draw(row, proposal, analysis, cleaned, target.gaia_source_id, plots_dir)
    return proposal


def draw(
    row: AnalysisRow,
    proposal: Proposal,
    analysis: Analysis,
    cleaned: list[LightCurve],
    gaia_source_id: int,
    plots_dir: Path,
) -> None:
    """Render every plot of the Analysis at its Proposal's type, period and epoch, and record
    each in `row.plots` by its path relative to `plots_dir`. `submission.png` leads with the
    range band."""
    contaminated = analysis.blend.surveys if analysis.blend is not None else frozenset()
    lead = range_curve(cleaned, contaminated)
    paths = render_all(
        analysis,
        cleaned,
        vsx_type=proposal.proposed_type,
        period=proposal.period,
        epoch=proposal.epoch_bjd,
        out_dir=plot_dir(plots_dir, gaia_source_id, row.pipeline_version),
        chosen=(lead.survey, lead.band) if lead is not None else None,
    )
    row.plots = {kind: str(path.relative_to(plots_dir)) for kind, path in paths.items()}


def _unsubmitted() -> ColumnElement[bool]:
    """No Submission references the Proposal, which would freeze it (spec §4)."""
    return ~exists().where(Submission.proposal_id == Proposal.id)


def _read_curves(
    found: Elements, ranged: LightCurve, timed: LightCurve, analysis: Analysis, target: Target
) -> Elements:
    """`found` with the range and band read off `ranged`, the range band, and the epoch and
    rise or eclipse duration off `timed`, the epoch band."""
    time, mag, _ = magnitudes(ranged)
    code, period, agreement = found.proposed_type, found.period, analysis.agreement
    epoch = rise_or_eclipse = None
    if period is None:
        mag_max, mag_min = (float(p) for p in np.percentile(mag, APERIODIC_PERCENTILES))
    else:
        _, medians = binned_median(fold(time, period, time[0]), mag)
        if code in CYCLE_TO_CYCLE_TYPES:
            mag_max, mag_min = _cycle_range(time, mag, period) or (medians.min(), medians.max())
        else:
            mag_max, mag_min = float(medians.min()), float(medians.max())

        spec = TYPE_SPECS[code]
        strong = agreement is not None and (
            len(agreement.agreed_sources) >= STRONG_AGREEMENT_SOURCES
        )
        if spec.epoch_at in ("max", "min") and (code not in CYCLE_TO_CYCLE_TYPES or strong):
            epoch = _epoch_at(timed, period, spec.epoch_at)
        if spec.rise_duration_rule == "eclipse":
            rise_or_eclipse = eclipse_duration(timed, period)
        elif spec.rise_duration_rule == "rise" and analysis.shape is not None:
            rise_or_eclipse = analysis.shape.rise

    min_is_amplitude = ranged.value_kind == "flux"
    if min_is_amplitude:
        mag_max, mag_min = target.gaia_g, mag_min - mag_max
    return replace(
        found,
        epoch_bjd=epoch,
        rise_or_eclipse=rise_or_eclipse,
        mag_max=float(mag_max) if mag_max is not None else None,
        mag_min=float(mag_min),
        min_is_amplitude=min_is_amplitude,
        band=PASSBANDS[(ranged.survey, ranged.band)],
    )


def _epoch_at(lc: LightCurve, period: float, at: str) -> float:
    """The epoch of `lc`'s maximum (`at` "max") or minimum ("min"): the brightest or faintest
    bin of its binned phase curve."""
    time, mag, _ = magnitudes(lc)
    centres, medians = binned_median(fold(time, period, time[0]), mag)
    # Brighter is a smaller magnitude: maximum light is the smallest median.
    phase = float(centres[medians.argmin() if at == "max" else medians.argmax()])
    return _epoch(time, period, phase)


def _cycle_range(time: np.ndarray, mag: np.ndarray, period: float) -> tuple[float, float] | None:
    """(magnitude at maximum of the brightest cycle, at minimum of the faintest), from every
    cycle with `CYCLE_MIN_POINTS` points; None when no cycle has that many."""
    cycle = np.floor((time - time[0]) / period).astype(np.intp)
    extremes = [
        np.percentile(mag[cycle == k], CYCLE_PERCENTILES)
        for k in np.unique(cycle)
        if np.count_nonzero(cycle == k) >= CYCLE_MIN_POINTS
    ]
    if not extremes:
        return None
    return float(min(e[0] for e in extremes)), float(max(e[1] for e in extremes))


def _epoch(time: np.ndarray, period: float, phase: float) -> float:
    """The time at `phase` of the fold from the first point, in the cycle nearest the middle
    of the Light Curve, where an error in the period shifts it least."""
    first = float(time[0]) + phase * period
    return first + round((float(np.median(time)) - first) / period) * period
