"""The initial rule set (spec §7, NEU-1720): every rule with a supporting and an opposing
case, and the DSCT/HADS and RRAB/RRC splits on synthetic light curves."""

import re
from dataclasses import replace

import pytest

from argelander.analysis.model import Agreement, Analysis, CmdPosition, Region, Shape
from argelander.analysis.shape import shape
from argelander.classify import registry
from argelander.classify.confidence import scores
from argelander.classify.rules.agreement import AgreementRule
from argelander.classify.rules.base import Evidence, Rule, Stance
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
from test_shape import agreed, curve, pulsation, sinusoid

SHAPE = Shape(
    survey="ztf",
    band="g",
    n_points=500,
    period=0.5,
    amplitude=0.6,
    amplitude_v=0.5,
    snr=30.0,
    rise=0.45,
    skewness=-0.2,
    r21=0.3,
    eclipse_width=0.5,
    secondary_depth=0.5,
    flat_fraction=0.1,
)


def analysis(
    period: float | None = 0.5,
    *,
    region: Region = "unknown",
    bp_rp: float | None = None,
    abs_g: float | None = None,
    **shape_fields: float,
) -> Analysis:
    """An Analysis agreed at `period` (none when None), shaped like `SHAPE` but for
    `shape_fields`."""
    a = Analysis(target_id=1, pipeline_version="test")
    if period is not None:
        a.agreement = Agreement(period, ("ztf_g", "ztf_r"), "1:1", 1e-3)
        a.shape = replace(SHAPE, period=period, **shape_fields)
    a.cmd = CmdPosition(bp_rp=bp_rp, abs_g=abs_g, distance_pc=None, region=region)
    return a


TARGET = Target(gaia_source_id=1)


def judged(rule: Rule, code: str, a: Analysis, target: Target = TARGET) -> tuple[Stance, float]:
    e = rule.evaluate(code, a, target)
    assert e.rule == rule.name and e.type_code == code
    return e.verdict, e.weight


@pytest.mark.parametrize(
    ("code", "a", "expected"),
    [
        ("RRAB", analysis(None), ("against", 1.0)),
        ("CST", analysis(None), ("supports", 0.5)),
        ("L", analysis(None), ("neutral", 0.0)),
        ("EW", analysis(0.3), ("neutral", 0.0)),
        ("L", analysis(0.3), ("against", 1.0)),
        ("CST", analysis(0.3), ("against", 1.0)),
    ],
)
def test_agreement(code: str, a: Analysis, expected: tuple[Stance, float]) -> None:
    assert judged(AgreementRule(), code, a) == expected


@pytest.mark.parametrize(
    ("code", "a", "expected"),
    [
        ("RRAB", analysis(0.5), ("supports", 1.0)),
        ("RRAB", analysis(0.1), ("against", 1.0)),
        ("EB", analysis(0.8), ("supports", 0.5)),  # only a soft bound
        ("EB", analysis(0.3), ("against", 0.5)),  # outside it
        ("ROT", analysis(5.0), ("supports", 0.5)),  # BY's range, as guidance
        ("ROT", analysis(200.0), ("against", 0.5)),
        ("RRD", analysis(0.5), ("neutral", 0.5)),  # VSX states none
        ("RRAB", analysis(None), ("neutral", 0.0)),
    ],
)
def test_period_range(code: str, a: Analysis, expected: tuple[Stance, float]) -> None:
    assert judged(PeriodRange(), code, a) == expected


@pytest.mark.parametrize(
    ("code", "a", "expected"),
    [
        ("RRC", analysis(amplitude_v=0.5), ("supports", 1.0)),
        ("RRC", analysis(amplitude_v=1.0), ("against", 1.0)),
        ("ELL", analysis(amplitude_v=0.05), ("supports", 1.0)),
        ("ELL", analysis(amplitude_v=0.2), ("against", 0.5)),  # past "usually ≤ 0.1"
        ("M", analysis(amplitude_v=1.0), ("against", 1.0)),
        ("RRC", analysis(None), ("neutral", 0.0)),
    ],
)
def test_amplitude_range(code: str, a: Analysis, expected: tuple[Stance, float]) -> None:
    assert judged(AmplitudeRange(), code, a) == expected


def test_range_explanations_carry_the_numbers() -> None:
    e = PeriodRange().evaluate("RRAB", analysis(0.172), TARGET)
    assert e.explanation == "The period of 0.172 d is outside RRAB's range of 0.3 to 1 d."
    e = AmplitudeRange().evaluate("ELL", analysis(amplitude_v=0.2, amplitude=0.24), TARGET)
    assert e.explanation == (
        "The amplitude of 0.20 mag in V (0.24 in ZTF g) is outside ELL's range of "
        "at most 0.3 mag, usually at most 0.1."
    )


@pytest.mark.parametrize(
    ("code", "a", "expected"),
    [
        ("EW", analysis(eclipse_width=0.15), ("supports", 1.0)),
        ("EW", analysis(eclipse_width=0.5), ("against", 1.0)),
        ("RRAB", analysis(eclipse_width=0.15), ("against", 1.0)),
        ("RRAB", analysis(eclipse_width=0.5), ("neutral", 0.0)),
        ("EW", analysis(eclipse_width=0.26), ("neutral", 0.0)),
    ],
)
def test_eclipse_width(code: str, a: Analysis, expected: tuple[Stance, float]) -> None:
    assert judged(EclipseWidth(), code, a) == expected


@pytest.mark.parametrize(
    ("code", "a", "expected"),
    [
        ("EW", analysis(eclipse_width=0.15, secondary_depth=0.85), ("supports", 1.0)),
        ("EW", analysis(eclipse_width=0.15, secondary_depth=0.4), ("against", 1.0)),
        ("EB", analysis(eclipse_width=0.15, secondary_depth=0.4), ("supports", 1.0)),
        ("EB", analysis(eclipse_width=0.15, secondary_depth=0.05), ("against", 1.0)),
        ("EB", analysis(eclipse_width=0.15, secondary_depth=0.9), ("against", 1.0)),
        ("EW", analysis(eclipse_width=0.5, secondary_depth=0.85), ("neutral", 0.0)),
    ],
)
def test_secondary_minimum(code: str, a: Analysis, expected: tuple[Stance, float]) -> None:
    assert judged(SecondaryDepth(), code, a) == expected


@pytest.mark.parametrize(
    ("code", "a", "expected"),
    [
        ("EA", analysis(eclipse_width=0.1, flat_fraction=0.7), ("supports", 1.0)),
        ("EA", analysis(eclipse_width=0.1, flat_fraction=0.3), ("against", 1.0)),
        ("EA", analysis(eclipse_width=0.1, flat_fraction=0.52), ("against", 0.5)),
        ("EW", analysis(eclipse_width=0.1, flat_fraction=0.3), ("supports", 0.5)),
        ("EB", analysis(eclipse_width=0.1, flat_fraction=0.7), ("against", 1.0)),
        ("EA", analysis(eclipse_width=0.5, flat_fraction=0.7), ("neutral", 0.0)),
    ],
)
def test_contacts(code: str, a: Analysis, expected: tuple[Stance, float]) -> None:
    assert judged(Contacts(), code, a) == expected


@pytest.mark.parametrize(
    ("code", "a", "expected"),
    [
        ("RRAB", analysis(rise=0.15), ("supports", 1.0)),
        ("RRAB", analysis(rise=0.45), ("against", 1.0)),
        ("RRC", analysis(rise=0.45), ("supports", 1.0)),
        ("RRC", analysis(rise=0.15), ("against", 1.0)),
        ("HADS", analysis(rise=0.35), ("neutral", 0.0)),
        ("RRAB", analysis(rise=0.15, eclipse_width=0.1), ("neutral", 0.0)),
    ],
)
def test_asymmetry(code: str, a: Analysis, expected: tuple[Stance, float]) -> None:
    assert judged(Asymmetry(), code, a) == expected


@pytest.mark.parametrize(
    ("code", "a", "expected"),
    [
        ("RRAB", analysis(skewness=-0.8), ("supports", 0.5)),
        ("RRAB", analysis(skewness=-0.1), ("against", 0.5)),
        ("RRC", analysis(skewness=-0.1), ("supports", 0.5)),
        ("RRC", analysis(skewness=-0.8), ("against", 0.5)),
    ],
)
def test_skewness(code: str, a: Analysis, expected: tuple[Stance, float]) -> None:
    assert judged(Skewness(), code, a) == expected


@pytest.mark.parametrize(
    ("a", "expected"),
    [
        (analysis(0.1, amplitude_v=0.05), ("supports", 0.5)),
        (analysis(0.1, amplitude_v=0.3, rise=0.2), ("against", 1.0)),
        (analysis(0.1, amplitude_v=0.3, rise=0.45), ("neutral", 0.0)),
    ],
)
def test_hads_split(a: Analysis, expected: tuple[Stance, float]) -> None:
    assert judged(HadsSplit(), "DSCT", a) == expected


@pytest.mark.parametrize(
    ("code", "a", "expected"),
    [
        ("DCEP", analysis(4.5, r21=0.35), ("supports", 0.5)),
        ("DCEP", analysis(4.5, r21=0.1), ("against", 0.5)),
        ("DCEPS", analysis(3.0, r21=0.1), ("supports", 0.5)),
        ("DCEPS", analysis(3.0, r21=0.35), ("against", 0.5)),
    ],
)
def test_fourier_ratio(code: str, a: Analysis, expected: tuple[Stance, float]) -> None:
    assert judged(FourierRatio(), code, a) == expected


# KO Cep: P 4.558 d, M_G −0.81, BP−RP 2.45, Wesenheit 0.69 mag above the classical relation.
CLASSICAL = analysis(4.558, abs_g=-0.81, bp_rp=2.452)
TYPE_II = analysis(4.558, abs_g=-1.58, bp_rp=1.0)  # 1.3 mag below it
DWARF = analysis(4.558, abs_g=5.0, bp_rp=1.0)


@pytest.mark.parametrize(
    ("code", "a", "expected"),
    [
        ("DCEP", CLASSICAL, ("supports", 1.0)),
        ("CWB", CLASSICAL, ("against", 1.0)),
        ("CWB", TYPE_II, ("supports", 1.0)),
        ("DCEP", TYPE_II, ("against", 1.0)),
        ("DCEP", DWARF, ("against", 1.0)),
        ("CWB", DWARF, ("against", 1.0)),
        ("DCEP", analysis(4.558), ("neutral", 0.0)),  # no significant parallax
    ],
)
def test_cepheid_luminosity(code: str, a: Analysis, expected: tuple[Stance, float]) -> None:
    assert judged(CepheidLuminosity(), code, a) == expected


@pytest.mark.parametrize(
    ("code", "a", "expected"),
    [
        ("RRAB", analysis(region="instability_strip", abs_g=0.5, bp_rp=0.6), ("supports", 0.5)),
        ("RRAB", analysis(region="main_sequence", abs_g=5.0, bp_rp=1.2), ("against", 0.5)),
        ("RRAB", analysis(), ("neutral", 0.0)),
    ],
)
def test_cmd_region(code: str, a: Analysis, expected: tuple[Stance, float]) -> None:
    assert judged(CmdRegion(), code, a) == expected


@pytest.mark.parametrize(
    ("code", "a", "expected"),
    [
        ("RRAB", analysis(bp_rp=0.5), ("supports", 0.25)),
        ("SR", analysis(bp_rp=0.3), ("against", 0.5)),  # bluer than any red giant
        ("DCEP", analysis(bp_rp=2.4), ("against", 0.25)),  # redder: maybe reddening
        ("ROT", analysis(bp_rp=1.3), ("supports", 0.25)),  # BY's red, as guidance
        ("RRAB", analysis(bp_rp=1.3), ("neutral", 0.0)),
        ("RRAB", analysis(), ("neutral", 0.0)),
    ],
)
def test_colour(code: str, a: Analysis, expected: tuple[Stance, float]) -> None:
    assert judged(Colour(), code, a) == expected


def varying(excess: float) -> Analysis:
    from argelander.analysis.model import Variability

    a = analysis(None)
    a.variability = Variability("ztf", "g", 500, 0.05 * excess, 0.05 / 3.29, excess)
    return a


@pytest.mark.parametrize(
    ("code", "a", "expected"),
    [
        ("L", varying(5.0), ("supports", 0.5)),
        ("CST", varying(5.0), ("against", 1.0)),
        ("CST", varying(1.2), ("supports", 0.5)),
        ("L", varying(1.2), ("against", 1.0)),
        ("L", varying(2.5), ("neutral", 0.0)),
        ("CST", analysis(None), ("neutral", 0.0)),  # every survey contaminated
        ("L", analysis(None), ("neutral", 1.0)),  # L is unconfirmed without it
    ],
)
def test_variability(code: str, a: Analysis, expected: tuple[Stance, float]) -> None:
    assert judged(VariabilityRule(), code, a) == expected


RR_LYRAE = Target(
    gaia_source_id=1, gaia_vari_class="RR", gaia_vari_detail={"vari_subclass": "RRab"}
)


@pytest.mark.parametrize(
    ("code", "target", "expected"),
    [
        ("RRAB", RR_LYRAE, ("supports", 0.25)),
        ("RRC", RR_LYRAE, ("neutral", 0.0)),  # the subclass narrows RR to RRab
        ("RRAB", TARGET, ("neutral", 0.0)),
    ],
)
def test_gaia_class(code: str, target: Target, expected: tuple[Stance, float]) -> None:
    assert judged(GaiaClass(), code, analysis(), target) == expected


def top(a: Analysis, *codes: str) -> list[str]:
    """`codes` ranked by Confidence, best first."""
    scored = scores(registry.evaluate(a, TARGET))
    return sorted(codes, key=lambda code: -scored[code])


def synthetic(profile, period: float) -> Analysis:  # noqa: ANN001
    """A Target whose only band is V, folded at `period`."""
    a = Analysis(target_id=1, pipeline_version="test")
    a.agreement = agreed(period)
    a.shape = shape([curve(profile, period=period, survey="asassn", band="V")], a.agreement, ())
    return a


def test_an_asymmetric_high_amplitude_delta_scuti_star_is_hads() -> None:
    assert top(synthetic(pulsation(0.3, 0.2), 0.12), "HADS", "DSCT") == ["HADS", "DSCT"]


def test_an_asymmetric_rr_lyrae_is_rrab() -> None:
    assert top(synthetic(pulsation(0.6, 0.15), 0.4), "RRAB", "RRC") == ["RRAB", "RRC"]


def test_a_symmetric_low_amplitude_rr_lyrae_is_rrc() -> None:
    assert top(synthetic(sinusoid(0.3), 0.35), "RRC", "RRAB") == ["RRC", "RRAB"]


def test_without_agreement_no_periodic_type_scores() -> None:
    scored = scores(
        registry.evaluate(analysis(None, region="giant_branch", bp_rp=1.5, abs_g=0.0), TARGET)
    )
    assert scored["CST"] == 1.0
    assert max(score for code, score in scored.items() if code != "CST") <= 0.5


def test_every_scored_type_gets_evidence_in_order() -> None:
    evidence = registry.evaluate(analysis(0.5), TARGET)
    seen = list(dict.fromkeys(e.type_code for e in evidence))
    assert seen == [code for code in SCORED_TYPES if registry.rules_for(code)]
    assert seen[:3] == ["EW", "EA", "EB"]


@pytest.mark.parametrize(
    "a",
    [
        analysis(None),
        analysis(0.5, region="instability_strip", abs_g=0.5, bp_rp=0.6, rise=0.15),
        analysis(1.3, eclipse_width=0.1, secondary_depth=0.2, flat_fraction=0.7),
        CLASSICAL,
        varying(5.0),
    ],
)
def test_every_explanation_is_one_sentence(a: Analysis) -> None:
    for e in registry.evaluate(a, TARGET):
        assert isinstance(e, Evidence)
        assert e.explanation.endswith("."), e
        assert not re.search(r"\.\s+[A-Z]", e.explanation), e


def test_a_low_amplitude_f_type_dwarf_fits_gdor_better_than_rot() -> None:
    a = analysis(1.2, region="main_sequence", abs_g=3.0, bp_rp=0.5, amplitude_v=0.05)
    assert top(a, "GDOR", "ROT") == ["GDOR", "ROT"]


def test_sr_and_m_split_at_two_and_a_half_magnitudes() -> None:
    giant = {"region": "giant_branch", "abs_g": -1.0, "bp_rp": 2.5}
    assert top(analysis(200.0, amplitude_v=3.5, **giant), "M", "SR") == ["M", "SR"]
    assert top(analysis(200.0, amplitude_v=1.5, **giant), "SR", "M") == ["SR", "M"]
