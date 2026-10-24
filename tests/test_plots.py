"""Evidence plots (spec §8), rendered from synthetic Light Curves sampled the way surveys sample.

The suite runs in the `app` container, which has no display: rendering there is the check that
the plots need none.
"""

from pathlib import Path

import numpy as np
import pytest
from matplotlib.figure import Figure

from argelander.analysis.aliases import aliases_of
from argelander.analysis.model import (
    Analysis,
    Blend,
    CmdPosition,
    Contamination,
    Peak,
    PeriodogramResult,
)
from argelander.classify.types import phase_range
from argelander.lightcurve import LightCurve
from argelander.plots import plot_dir, render_all
from argelander.plots.cmd import background, cmd_figure
from argelander.plots.combined import combined_figure
from argelander.plots.periodogram import periodogram_figure
from argelander.plots.phase import MIN_BINS, phase_figure
from argelander.plots.render import SUBMISSION_MAX_BYTES, lead_curve, submission_figure
from argelander.submission.form import format_period

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
START = 2458300.5  # BJD_TDB
PERIOD = 0.5123456  # days
EPOCH = START + 0.1234


def curve(
    survey: str, band: str, time: np.ndarray, value: np.ndarray, kind: str = "mag"
) -> LightCurve:
    return LightCurve(
        survey=survey,
        source_key="1",
        band=band,
        time=time,
        value=value,
        error=np.full(time.size, 0.02),
        quality=np.zeros(time.size, dtype=np.int32),
        value_kind=kind,
        meta={},
    )


def nightly(rng: np.random.Generator, n: int, years: float = 5.0) -> np.ndarray:
    """`n` epochs at random nights over `years`, a few hours into each night."""
    nights = rng.integers(0, int(365 * years), n)
    return np.sort(START + nights + rng.uniform(0.15, 0.4, n))


def tess_sectors(sectors: int) -> np.ndarray:
    """120 s cadence over `sectors` consecutive 27-day sectors."""
    one = np.arange(0.0, 27.0, 120.0 / 86400.0)
    return np.concatenate([START + 1500.0 + 27.0 * s + one for s in range(sectors)])


def eclipser(phase: np.ndarray, level: float) -> np.ndarray:
    """An EA light curve: primary eclipse 0.6 mag deep at phase 0, secondary 0.2 at 0.5."""
    dist_primary = np.minimum(phase, 1.0 - phase)
    dist_secondary = np.abs(phase - 0.5)
    return (
        level
        + 0.6 * np.exp(-0.5 * (dist_primary / 0.02) ** 2)
        + 0.2 * np.exp(-0.5 * (dist_secondary / 0.02) ** 2)
    )


def rrab(phase: np.ndarray, level: float) -> np.ndarray:
    """An RRAB sawtooth with maximum at phase 0: a slow decline, then a fast rise."""
    rise = 0.15
    decline = np.where(phase < 1 - rise, phase / (1 - rise), (1 - phase) / rise)
    return level + 0.8 * decline


def phases(time: np.ndarray) -> np.ndarray:
    return ((time - EPOCH) / PERIOD) % 1.0


def periodogram_result(survey: str, band: str, n_points: int) -> PeriodogramResult:
    frequency = np.linspace(0.01, 20.0, 4000)
    power = np.exp(-0.5 * ((frequency - 1 / PERIOD) / 0.002) ** 2)
    return PeriodogramResult(
        survey=survey,
        band=band,
        method="lomb_scargle",
        n_points=n_points,
        frequency=frequency,
        power=power,
        peaks=(Peak(period=PERIOD, power=1.0, false_alarm_probability=1e-12),),
        gaia=Peak(period=PERIOD * 2, power=0.1, false_alarm_probability=0.5),
        insufficient=False,
    )


def eclipsing_target(rng: np.random.Generator, tess_sector_count: int = 1) -> list[LightCurve]:
    """An eclipsing-binary Target seen by ZTF g and r, TESS, ASAS-SN V and a few Gaia transits."""
    ztf_g, ztf_r, asassn = nightly(rng, 800), nightly(rng, 900), nightly(rng, 1500)
    tess = tess_sectors(tess_sector_count)
    gaia = nightly(rng, 20)  # too few to search: in the combined plot, no phase plot
    flux = 10 ** (-0.4 * (eclipser(phases(tess), 0.0)))
    return [
        curve("ztf", "g", ztf_g, eclipser(phases(ztf_g), 15.2) + rng.normal(0, 0.03, 800)),
        curve("ztf", "r", ztf_r, eclipser(phases(ztf_r), 14.9) + rng.normal(0, 0.03, 900)),
        curve("tess", "T", tess, flux + rng.normal(0, 0.02, tess.size), kind="flux"),
        curve("asassn", "V", asassn, eclipser(phases(asassn), 15.1) + rng.normal(0, 0.1, 1500)),
        curve("gaia", "G", gaia, eclipser(phases(gaia), 15.0)),
    ]


def analysis_of(curves: list[LightCurve]) -> Analysis:
    return Analysis(
        target_id=1,
        pipeline_version="0.1.0",
        periods=[periodogram_result(lc.survey, lc.band, lc.time.size) for lc in curves[:4]],
        blend=Blend(
            contaminations=(
                Contamination(
                    survey="tess",
                    flag="tess_contaminated",
                    neighbours=(),
                    flux_fraction=0.6,
                    remark="TESS magnitudes contaminated. Range not corrected.",
                ),
            )
        ),
        cmd=CmdPosition(bp_rp=0.7, abs_g=4.2, distance_pc=800.0, region="main_sequence"),
    )


def test_render_all_writes_every_plot_kind_under_its_spec_name(tmp_path: Path) -> None:
    curves = eclipsing_target(np.random.default_rng(1709))
    out_dir = plot_dir(tmp_path, 1234567890, "0.1.0")

    paths = render_all(
        analysis_of(curves), curves, vsx_type="EA", period=PERIOD, epoch=EPOCH, out_dir=out_dir
    )

    expected = {
        "phase-ztf-g.png",
        "phase-ztf-r.png",
        "phase-tess-T.png",
        "phase-asassn-V.png",
        "periodogram.png",
        "cmd.png",
        "combined.png",
        "submission.png",
    }
    assert out_dir == tmp_path / "1234567890" / "0.1.0"
    assert {p.name for p in out_dir.iterdir()} == expected
    assert {kind: path.name for kind, path in paths.items()} == {
        name.removesuffix(".png"): name for name in expected
    }
    for path in paths.values():
        assert path.read_bytes().startswith(PNG_MAGIC)


def test_without_a_period_only_the_combined_light_curve_is_submitted(tmp_path: Path) -> None:
    curves = eclipsing_target(np.random.default_rng(1709))

    paths = render_all(
        analysis_of(curves), curves, vsx_type="VAR", period=None, epoch=None, out_dir=tmp_path
    )

    assert set(paths) == {"periodogram", "cmd", "combined", "submission"}


def test_submission_png_fits_the_revision_forms_limit_for_the_largest_target(
    tmp_path: Path,
) -> None:
    # Two full TESS sectors at 120 s, noisy, are the most points any Target carries. Re-check
    # against the largest real fixture once NEU-1711 commits them.
    curves = eclipsing_target(np.random.default_rng(1709), tess_sector_count=2)
    assert sum(lc.time.size for lc in curves) > 40_000

    paths = render_all(
        analysis_of(curves), curves, vsx_type="EA", period=PERIOD, epoch=EPOCH, out_dir=tmp_path
    )

    assert paths["submission"].stat().st_size < SUBMISSION_MAX_BYTES


def test_submission_title_gives_band_and_period_as_the_form_takes_them() -> None:
    curves = eclipsing_target(np.random.default_rng(1709))
    ztf_g = curves[0]
    baseline = 5 * 365.0

    fig = submission_figure(
        curves, lead=ztf_g, period=PERIOD, epoch=EPOCH, vsx_type="EA", baseline=baseline
    )

    title = fig.axes[0].get_title()
    assert "passband g" in title
    assert f"P = {format_period(PERIOD, baseline)} d" in title
    assert format_period(PERIOD, baseline) == "0.512346"


def test_tess_is_named_by_its_custom_passband_and_flagged_when_contaminated() -> None:
    tess = eclipsing_target(np.random.default_rng(1709))[2]

    fig = phase_figure(
        tess, period=PERIOD, epoch=EPOCH, vsx_type="EA", baseline=27.0, contaminated=True
    )

    assert fig.axes[0].get_title() == "TESS, passband TESS, P = 0.5123 d (contaminated)"


def test_submission_leads_with_the_band_with_the_most_points_unless_one_is_chosen() -> None:
    curves = eclipsing_target(np.random.default_rng(1709))
    searched = curves[:4]

    assert lead_curve(searched, None) is curves[2]  # TESS
    assert lead_curve(searched, ("ztf", "r")) is curves[1]
    with pytest.raises(ValueError, match="gaia"):
        lead_curve(searched, ("gaia", "G"))


def binned_line(fig: Figure) -> tuple[np.ndarray, np.ndarray]:
    line = fig.axes[0].lines[0]
    return np.asarray(line.get_xdata()), np.asarray(line.get_ydata())


def test_eclipser_is_centred_on_its_secondary_eclipse() -> None:
    rng = np.random.default_rng(1709)
    time = nightly(rng, 2000)
    lc = curve("ztf", "g", time, eclipser(phases(time), 15.0) + rng.normal(0, 0.01, time.size))

    fig = phase_figure(lc, period=PERIOD, epoch=EPOCH, vsx_type="EA", baseline=1825.0)

    low, high = fig.axes[0].get_xlim()
    assert (low, high) == (-0.25, 1.25)
    assert (low + high) / 2 == 0.5
    x, y = binned_line(fig)
    middle = (x > 0.25) & (x < 0.75)
    secondary = x[middle][np.argmax(y[middle])]  # faintest: magnitudes grow fainter
    assert abs(secondary - 0.5) <= 1 / MIN_BINS
    assert y[middle].max() > np.median(y) + 0.05
    assert fig.axes[0].yaxis_inverted()


def test_pulsator_is_centred_on_its_maximum() -> None:
    rng = np.random.default_rng(1709)
    time = nightly(rng, 2000)
    lc = curve("ztf", "g", time, rrab(phases(time), 15.0) + rng.normal(0, 0.01, time.size))

    fig = phase_figure(lc, period=PERIOD, epoch=EPOCH, vsx_type="RRAB", baseline=1825.0)

    low, high = fig.axes[0].get_xlim()
    assert (low, high) == (0.0, 2.0)
    x, y = binned_line(fig)
    middle = (x > 0.5) & (x < 1.5)
    maximum = x[middle][np.argmin(y[middle])]  # brightest
    assert abs(maximum - 1.0) <= 1 / MIN_BINS


@pytest.mark.parametrize(
    ("vsx_type", "expected"),
    [
        ("EA", (-0.25, 1.25)),
        ("EW:", (-0.25, 1.25)),
        ("EA+ELL", (-0.25, 1.25)),
        ("ELL|DSCT", (-0.25, 1.25)),
        ("EA/SD", (-0.25, 1.25)),
        ("RRAB", (0.0, 2.0)),
        ("DSCT", (0.0, 2.0)),
        ("ROT", (0.0, 2.0)),
        ("VAR", (0.0, 2.0)),
    ],
)
def test_phase_range_follows_the_first_code_of_the_type(
    vsx_type: str, expected: tuple[float, float]
) -> None:
    assert phase_range(vsx_type) == expected


def test_combined_legend_names_each_survey_band_and_its_contamination() -> None:
    curves = eclipsing_target(np.random.default_rng(1709))

    fig = combined_figure(curves, contaminated={"tess"})

    ax = fig.axes[0]
    assert ax.yaxis_inverted()
    legend = ax.get_legend()
    assert legend is not None
    assert [t.get_text() for t in legend.get_texts()] == [
        "ZTF g",
        "ZTF r",
        "TESS T (contaminated), Δmag from median",
        "ASAS-SN V",
        "Gaia G",
    ]


def test_periodogram_marks_the_period_and_every_tested_alias() -> None:
    result = periodogram_result("ztf", "g", 800)

    fig = periodogram_figure([result], period=PERIOD)

    marked = sorted(
        float(np.asarray(line.get_xdata())[0])
        for line in fig.axes[0].lines[1:]  # the first line is the periodogram itself
    )
    expected = sorted([PERIOD, PERIOD * 2] + [alias for _, alias in aliases_of(PERIOD, "ztf")])
    # The Gaia period happens to equal 2P here, the alias test's own 2P hypothesis.
    assert marked == pytest.approx(expected)


def test_cmd_places_the_target_on_the_committed_background() -> None:
    stars = background()
    assert stars.shape == (6000, 2)

    placed = cmd_figure(CmdPosition(bp_rp=0.4, abs_g=0.6, distance_pc=900.0, region="unknown"))
    unplaced = cmd_figure(None)

    assert len(placed.axes[0].collections) == 2  # background, Target
    assert len(unplaced.axes[0].collections) == 1
    assert placed.axes[0].yaxis_inverted()
