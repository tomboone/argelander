"""`classify/types.py` against VSX's own documents (spec §7): the type and passband
vocabularies match the revision form as captured, and every number in the `TypeSpec` table
matches the type designations, both read from `docs/reference/` at test time.
"""

import re
from dataclasses import astuple
from pathlib import Path

import pytest

from argelander.classify.types import (
    CONSTANT,
    GAIA_CLASS_TYPES,
    GAIA_SUBCLASS_TYPES,
    INITIAL_TYPES,
    PASSBAND_VOCABULARY,
    TYPE_SPECS,
    TYPE_VOCABULARY,
    UNSPECIFIED,
    Range,
    gaia_types,
)
from argelander.submission.form import PASSBANDS

REFERENCE = Path(__file__).parents[1] / "docs" / "reference"
FORM = (REFERENCE / "vsx-revision-form.md").read_text()
TYPE_RANGES = (REFERENCE / "vsx-type-ranges.md").read_text()
CODE = re.compile(r"`([^`]+)`")
NUMBER = re.compile(r"\d+(?:\.\d+)?")
# A cell whose numbers are not bounds, and why.
NOT_BOUNDS = {("RRD", "period"): "a typical fundamental period and the mode ratio"}


def first_paragraph_after(heading: str, text: str) -> str:
    body = text.split(f"\n{heading}\n", 1)[1]
    return body.strip().split("\n\n", 1)[0]


def type_rows() -> list[tuple[tuple[str, ...], str, str]]:
    """(codes, period cell, amplitude cell) for every row of the reference's type tables."""
    rows = []
    for line in TYPE_RANGES.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        codes = tuple(CODE.findall(cells[0]))
        # The thirteen-types table has a Group column before the period; the others do not.
        period, amplitude = (cells[2], cells[3]) if len(cells) == 5 else (cells[1], cells[2])
        rows.append((codes, period, amplitude))
    return rows


def bounds_in(cell: str) -> set[tuple[str, float]]:
    """The bounds a cell states, as (`Range` field, value). Each clause (split at commas,
    brackets and slashes) holds a range "A to B" or "A–B", or one bound: a lower one after
    ">", else an upper one. A clause that says "usually" or "mainly" is soft. "to > 2000" and
    "to ≥ 10 000" leave the range open above, and BLAP's minutes repeat its days."""
    cell = re.sub(r"to\s*[>≥]\s*[\d ]+", "upwards", cell)
    cell = re.sub(r"[\d.]+\s*[–-]\s*[\d.]+\s*min\b", "", cell)
    bounds = set()
    for clause in re.split(r"[,(/]", cell):
        numbers = [float(n) for n in NUMBER.findall(clause)]
        prefix = "soft_" if re.search(r"usually|mainly", clause) else ""
        if len(numbers) == 2:
            sides = ["low", "high"]
        elif re.search(r">|upwards", clause):
            sides = ["low"]
        else:
            sides = ["high"]
        bounds.update((prefix + side, n) for side, n in zip(sides, numbers, strict=False))
    return bounds


def spec_bounds(ranges: list[Range]) -> set[tuple[str, float]]:
    return {(side, value) for r in ranges for side, value in vars(r).items() if value is not None}


def test_the_type_vocabulary_is_the_forms_dropdown() -> None:
    dropdown = first_paragraph_after("## Variability type vocabulary (`varType`)", FORM)
    assert set(CODE.findall(dropdown)) == TYPE_VOCABULARY


def test_the_passband_vocabulary_is_the_forms_dropdown() -> None:
    dropdown = first_paragraph_after("## Passband vocabulary (`maxband` / `minband`)", FORM)
    assert set(CODE.findall(dropdown)) == PASSBAND_VOCABULARY


def test_every_survey_band_maps_to_a_listed_passband_or_the_custom_tess() -> None:
    assert {code for code in PASSBANDS.values() if code != "TESS"} <= PASSBAND_VOCABULARY
    assert "TESS" not in PASSBAND_VOCABULARY


def test_the_initial_rule_set_constant_and_unspecified_are_in_the_vocabulary() -> None:
    assert INITIAL_TYPES == {
        *("EW", "EA", "EB", "RRAB", "RRC", "DSCT", "HADS"),
        *("DCEP", "SR", "L", "M", "ROT", "ELL"),
    }
    assert INITIAL_TYPES | {CONSTANT, UNSPECIFIED} <= TYPE_VOCABULARY
    assert (CONSTANT, UNSPECIFIED) == ("CST", "VAR")


def test_there_is_one_type_spec_per_code_in_the_reference_and_each_is_a_form_type() -> None:
    codes = {code for row in type_rows() for code in row[0]}
    assert set(TYPE_SPECS) == codes
    assert all(code == spec.code for code, spec in TYPE_SPECS.items())
    assert set(TYPE_SPECS) <= TYPE_VOCABULARY


@pytest.mark.parametrize("row", type_rows(), ids=lambda row: "/".join(row[0]))
def test_every_period_and_amplitude_bound_matches_the_reference(
    row: tuple[tuple[str, ...], str, str],
) -> None:
    codes, period, amplitude = row
    specs = [TYPE_SPECS[code] for code in codes]
    for dimension, cell in (("period", period), ("amplitude", amplitude)):
        ranges = [getattr(spec, f"{dimension}_range") for spec in specs]
        if any((code, dimension) in NOT_BOUNDS for code in codes):
            assert all(astuple(r) == astuple(Range()) for r in ranges)
        else:
            assert spec_bounds(ranges) == bounds_in(cell), f"{codes} {dimension}: {cell}"


def test_epoch_and_rise_duration_follow_the_reference_conventions() -> None:
    table = TYPE_RANGES.split("## Epoch and rise-duration conventions by target type")[1]
    covered = set()
    for line in table.split("\n## ")[0].splitlines():
        if not line.startswith("| ") or line.startswith(("| Code", "| ---")):
            continue
        codes, epoch, rise = (cell.strip() for cell in line.strip("|").split("|"))
        expected_epoch = "min" if "minimum" in epoch else "max" if "maximum" in epoch else "none"
        expected_rise = (
            "eclipse"
            if rise.startswith("eclipse")
            else "rise"
            if rise.startswith("rise")
            else "none"
        )
        for code in codes.split(", "):
            covered.add(code)
            assert TYPE_SPECS[code].epoch_at == expected_epoch, code
            assert TYPE_SPECS[code].rise_duration_rule == expected_rise, code
    assert INITIAL_TYPES | {CONSTANT, UNSPECIFIED} <= covered
    for code in set(TYPE_SPECS) - covered:
        assert TYPE_SPECS[code].epoch_at is None, f"{code} has no convention in the reference"


def test_bounds_are_ordered() -> None:
    for spec in TYPE_SPECS.values():
        for r in (spec.period_range, spec.amplitude_range):
            if r.low is not None and r.high is not None:
                assert r.low < r.high, spec.code
            if r.soft_low is not None and r.soft_high is not None:
                assert r.soft_low < r.soft_high, spec.code


def test_gaia_classes_map_to_scored_types() -> None:
    mapped = set().union(*GAIA_CLASS_TYPES.values(), *GAIA_SUBCLASS_TYPES.values())
    assert mapped <= set(TYPE_SPECS)


@pytest.mark.parametrize(
    ("vari_class", "vari_subclass", "types"),
    [
        ("ECL", None, {"EA", "EB", "EW"}),
        ("RR", "RRab", {"RRAB"}),
        ("RR", None, {"RRAB", "RRC", "RRD"}),
        ("CEP", "T2CEP", {"CWA", "CWB"}),
        ("DSCT|GDOR|SXPHE", None, {"DSCT", "HADS", "GDOR", "SXPHE"}),
        ("AGN", None, set()),
        (None, None, set()),
    ],
)
def test_gaia_types_narrow_by_subclass(
    vari_class: str | None, vari_subclass: str | None, types: set[str]
) -> None:
    assert gaia_types(vari_class, vari_subclass) == types
