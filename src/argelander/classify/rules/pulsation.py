"""Pulsation shape: a steep rise or a near-sinusoid, the RR Lyrae and δ Scuti splits, and a
Cepheid's Fourier amplitude ratio (spec §7, docs/reference/vsx-type-ranges.md).

RRAB rises steeply and RRC is nearly symmetric, which the rise time and the skewness of the
curve both show. A δ Scuti star with an amplitude over 0.15 mag and an asymmetric curve is
HADS by VSX's definition. A fundamental-mode Cepheid's curve carries a strong second harmonic
(R21) where a first-overtone one is nearly sinusoidal. None of this is judged on a minimum
narrow enough to be an eclipse, whose shape says nothing about pulsation.
"""

from argelander.analysis.model import Analysis, Shape
from argelander.classify.rules.base import Evidence
from argelander.classify.types import (
    ASYMMETRIC_RISE,
    DCEP_LOW_R21,
    DCEP_MIN_R21,
    DCEPS_HIGH_R21,
    DCEPS_MAX_R21,
    ECLIPSE_MAX_WIDTH,
    HADS_MIN_AMPLITUDE,
    HARD_WEIGHT,
    RRAB_MAX_SKEWNESS,
    RRC_MIN_SKEWNESS,
    SOFT_WEIGHT,
    SYMMETRIC_RISE,
    TYPE_SPECS,
)
from argelander.db.models import Target


class Asymmetry:
    name = "asymmetry"
    applies_to = frozenset(code for code, spec in TYPE_SPECS.items() if spec.asymmetric is not None)

    def evaluate(self, type_code: str, analysis: Analysis, target: Target) -> Evidence:
        shape = analysis.shape
        if shape is None or _is_eclipse(shape):
            return _unjudged(self.name, type_code, shape)
        rise = shape.rise
        stated = f"The light rises from minimum to maximum in {rise:.2f} of the cycle"
        steep = TYPE_SPECS[type_code].asymmetric
        expected = "a steep rise" if steep else "a near-symmetric curve"
        if rise <= ASYMMETRIC_RISE:
            stance = "supports" if steep else "against"
            return Evidence(
                self.name,
                type_code,
                stance,
                HARD_WEIGHT,
                f"{stated}, a steep rise, where {type_code} has {expected}.",
            )
        if rise >= SYMMETRIC_RISE:
            stance = "against" if steep else "supports"
            return Evidence(
                self.name,
                type_code,
                stance,
                HARD_WEIGHT,
                f"{stated}, near-symmetric, where {type_code} has {expected}.",
            )
        return Evidence(
            self.name, type_code, "neutral", 0.0, f"{stated}, neither steep nor symmetric."
        )


class Skewness:
    name = "skewness"
    applies_to = frozenset({"RRAB", "RRC"})

    def evaluate(self, type_code: str, analysis: Analysis, target: Target) -> Evidence:
        shape = analysis.shape
        if shape is None or _is_eclipse(shape):
            return _unjudged(self.name, type_code, shape)
        skew = shape.skewness
        sharp = skew <= RRAB_MAX_SKEWNESS
        rounded = skew > RRC_MIN_SKEWNESS
        stated = f"The curve's skewness is {skew:+.2f}"
        if not sharp and not rounded:
            return Evidence(self.name, type_code, "neutral", 0.0, f"{stated}.")
        verdict = "sharp maxima, as RRAB's are" if sharp else "rounded maxima, as RRC's are"
        fits = sharp if type_code == "RRAB" else rounded
        return Evidence(
            self.name,
            type_code,
            "supports" if fits else "against",
            SOFT_WEIGHT,
            f"{stated}: {verdict}.",
        )


class HadsSplit:
    name = "hads_split"
    applies_to = frozenset({"DSCT"})

    def evaluate(self, type_code: str, analysis: Analysis, target: Target) -> Evidence:
        shape = analysis.shape
        if shape is None:
            return _unjudged(self.name, type_code, shape)
        amplitude = shape.amplitude_v
        if amplitude <= HADS_MIN_AMPLITUDE:
            return Evidence(
                self.name,
                type_code,
                "supports",
                SOFT_WEIGHT,
                f"The V amplitude of {amplitude:.2f} mag is under HADS's {HADS_MIN_AMPLITUDE} mag, "
                "so a δ Scuti star here is DSCT.",
            )
        if shape.rise <= ASYMMETRIC_RISE and not _is_eclipse(shape):
            return Evidence(
                self.name,
                type_code,
                "against",
                HARD_WEIGHT,
                f"A V amplitude of {amplitude:.2f} mag with a rise in {shape.rise:.2f} of a cycle "
                "is HADS, not DSCT, by VSX's definition.",
            )
        return Evidence(
            self.name,
            type_code,
            "neutral",
            0.0,
            f"The V amplitude of {amplitude:.2f} mag is HADS's, "
            "but the curve is not clearly asymmetric.",
        )


class FourierRatio:
    name = "fourier_r21"
    applies_to = frozenset({"DCEP", "DCEPS"})

    def evaluate(self, type_code: str, analysis: Analysis, target: Target) -> Evidence:
        shape = analysis.shape
        if shape is None or _is_eclipse(shape):
            return _unjudged(self.name, type_code, shape)
        r21 = shape.r21
        stated = f"The Fourier amplitude ratio R21 is {r21:.2f}"
        strong = "a strong second harmonic, as a fundamental-mode Cepheid's"
        weak = "a near-sinusoid, as a first-overtone Cepheid's"
        if type_code == "DCEP":
            if r21 >= DCEP_MIN_R21:
                return Evidence(
                    self.name, type_code, "supports", SOFT_WEIGHT, f"{stated}: {strong}."
                )
            if r21 < DCEP_LOW_R21:
                return Evidence(self.name, type_code, "against", SOFT_WEIGHT, f"{stated}: {weak}.")
        else:
            if r21 <= DCEPS_MAX_R21:
                return Evidence(self.name, type_code, "supports", SOFT_WEIGHT, f"{stated}: {weak}.")
            if r21 > DCEPS_HIGH_R21:
                return Evidence(
                    self.name, type_code, "against", SOFT_WEIGHT, f"{stated}: {strong}."
                )
        return Evidence(self.name, type_code, "neutral", 0.0, f"{stated}.")


def _is_eclipse(shape: Shape) -> bool:
    return shape.eclipse_width <= ECLIPSE_MAX_WIDTH


def _unjudged(rule: str, code: str, shape: Shape | None) -> Evidence:
    if shape is None:
        return Evidence(
            rule, code, "neutral", 0.0, f"No folded light curve to judge {code}'s shape by."
        )
    return Evidence(
        rule,
        code,
        "neutral",
        0.0,
        f"The primary minimum is {shape.eclipse_width:.2f} of the cycle wide, "
        "an eclipse, not a pulsation.",
    )
