"""The Agreement rule (spec §6 step 6): which sources' alias-tested periods settle a period."""

import pytest

from argelander.analysis.agreement import GAIA_BASELINE, MAX_SCATTER, TOLERANCE, agreement
from argelander.analysis.model import AliasTest, Hypothesis

ZTF_BASELINE = 2750.0  # days, ZTF's span in the prototype fixtures


def settled(
    survey: str, band: str, period: float, *, scatter: float = 0.1, baseline: float = 0.0
) -> AliasTest:
    """A band whose alias test settled on `period`, folding with `scatter` over `baseline`
    days (0: unknown, so only the fixed tolerance applies)."""
    return AliasTest(
        survey=survey,
        band=band,
        ranking=(
            Hypothesis(
                period=period, candidate=period, relation="P", bins=20, scatter=scatter, score=0.0
            ),
        ),
        baseline=baseline,
    )


def test_two_sources_within_a_tenth_of_a_percent_agree() -> None:
    found = agreement([settled("ztf", "g", 0.6), settled("asassn", "V", 0.6004)])

    assert found is not None
    assert found.relation == "1:1"
    assert found.agreed_sources == ("ztf_g", "asassn")
    assert abs(found.agreed_period - 0.6002) < 1e-12
    assert found.tolerance == TOLERANCE


def test_periods_further_apart_do_not_agree() -> None:
    assert agreement([settled("ztf", "g", 0.6), settled("ztf", "r", 0.6012)]) is None


def test_ztf_g_at_p_and_tess_at_2p_agree_at_2_to_1() -> None:
    found = agreement([settled("ztf", "g", 1.3713), settled("tess", "T", 2 * 1.3713)])

    assert found is not None
    assert found.relation == "2:1"
    assert found.agreed_sources == ("ztf_g", "tess")
    assert abs(found.agreed_period - 2 * 1.3713) < 1e-12


def test_gaia_epoch_photometry_agreeing_with_nothing_else_settles_nothing() -> None:
    gaia_epoch = [settled("gaia", band, 0.6) for band in ("G", "BP", "RP")]

    assert agreement(gaia_epoch) is None
    assert agreement([*gaia_epoch, settled("ztf", "g", 0.6)]) is None


def test_gaia_published_period_counts() -> None:
    found = agreement([settled("ztf", "r", 0.6)], gaia_period=0.6)

    assert found is not None
    assert found.agreed_sources == ("ztf_r", "gaia_published")


def test_one_survey_in_two_bands_is_one_source() -> None:
    assert agreement([settled("asassn", "V", 0.6), settled("asassn", "g", 0.6)]) is None
    assert agreement([settled("ztf", "i", 0.6), settled("ztf", "g", 0.6)]) is None


def test_the_period_most_sources_found_wins() -> None:
    found = agreement(
        [
            settled("ztf", "g", 0.6),
            settled("ztf", "r", 0.6),
            settled("asassn", "V", 0.6),
            settled("tess", "T", 4.2),
        ],
        gaia_period=4.2,
    )

    assert found is not None
    assert found.agreed_sources == ("ztf_g", "ztf_r", "asassn")
    assert found.relation == "1:1"


def test_two_equally_supported_periods_are_no_agreement() -> None:
    found = agreement(
        [settled("ztf", "g", 0.6), settled("ztf", "r", 0.6), settled("tess", "T", 4.2)],
        gaia_period=4.2,
    )

    assert found is None


def test_bands_without_a_period_are_ignored() -> None:
    empty = AliasTest(survey="tess", band="T", ranking=())

    assert agreement([empty, settled("ztf", "g", 0.6)]) is None


def test_a_source_at_both_scales_does_not_conflict_with_itself() -> None:
    found = agreement(
        [settled("ztf", "g", 0.6), settled("asassn", "V", 0.6), settled("asassn", "g", 1.2)]
    )

    assert found is not None
    assert found.relation == "1:1"
    assert found.agreed_sources == ("ztf_g", "asassn")
    assert abs(found.agreed_period - 0.6) < 1e-12


def test_agreement_does_not_chain_past_a_tenth_of_a_percent() -> None:
    found = agreement(
        [settled("ztf", "g", 0.6), settled("ztf", "r", 0.6005), settled("tess", "T", 0.601)]
    )

    # Either neighbouring pair agrees; all three never do, the outer two being 0.17 % apart.
    assert found is not None
    assert len(found.agreed_sources) == 2


def test_a_contaminated_survey_counts_as_half_a_source() -> None:
    pair = [settled("ztf", "g", 0.6), settled("tess", "T", 0.6)]

    assert agreement(pair, contaminated={"tess"}) is None
    found = agreement([*pair, settled("ztf", "r", 0.6)], contaminated={"tess"})
    assert found is not None
    assert found.agreed_sources == ("ztf_g", "ztf_r", "tess")


def test_a_contaminated_gaia_period_counts_as_half_a_source() -> None:
    assert agreement([settled("ztf", "r", 0.6)], gaia_period=0.6, contaminated={"gaia"}) is None


def test_contaminated_sources_weigh_less_against_clean_ones_at_another_period() -> None:
    # Three sources at 0.6 d against two at 1.7 d; contaminating two of the three makes it
    # 2.0 against 2.0, a tie.
    votes = [
        settled("tess", "T", 0.6),
        settled("asassn", "V", 0.6),
        settled("ztf", "g", 0.6),
        settled("ztf", "r", 1.7),
    ]

    found = agreement(votes, gaia_period=1.7)
    assert found is not None
    assert found.agreed_sources == ("ztf_g", "asassn", "tess")
    assert agreement(votes, gaia_period=1.7, contaminated={"tess", "asassn"}) is None


def test_long_periods_agree_within_the_baselines_resolution() -> None:
    """At 42 d a 2750 d baseline resolves the period to 1.5 %: ZTF g and r 0.104 % apart (the
    SR star in the fixtures) are the same peak."""
    votes = [
        settled("ztf", "g", 42.2805, baseline=ZTF_BASELINE),
        settled("ztf", "r", 42.2364, baseline=ZTF_BASELINE),
    ]

    found = agreement(votes)
    assert found is not None
    assert found.agreed_sources == ("ztf_g", "ztf_r")
    assert found.tolerance == pytest.approx(found.agreed_period / ZTF_BASELINE)
    # Without a baseline the fixed 0.1 % applies and they disagree.
    assert agreement([settled("ztf", "g", 42.2805), settled("ztf", "r", 42.2364)]) is None


def test_short_periods_keep_the_fixed_tolerance() -> None:
    votes = [
        settled("ztf", "g", 0.6, baseline=ZTF_BASELINE),
        settled("ztf", "r", 0.6012, baseline=ZTF_BASELINE),
    ]

    assert agreement(votes) is None


def test_the_shortest_agreeing_baseline_sets_the_tolerance() -> None:
    found = agreement([settled("ztf", "g", 40.0, baseline=ZTF_BASELINE)], gaia_period=40.3)

    assert found is not None
    assert found.agreed_sources == ("ztf_g", "gaia_published")
    assert found.tolerance == pytest.approx(found.agreed_period / GAIA_BASELINE)


def test_a_band_whose_fold_explains_little_does_not_vote() -> None:
    """A constant star's ZTF bands both settle on the sidereal half-day, folding badly."""
    noise = [
        settled("ztf", "g", 0.498637, scatter=0.91),
        settled("ztf", "r", 0.498757, scatter=0.93),
    ]

    assert agreement(noise) is None
    assert MAX_SCATTER < 0.91
    folding = [settled("ztf", "g", 0.498637), settled("ztf", "r", 0.498757)]
    assert agreement(folding) is not None


@pytest.mark.parametrize(("gaia", "ztf"), [(40.8, 40.0), (40.0, 40.8)])
def test_agreement_is_symmetric_in_which_vote_is_lower(gaia: float, ztf: float) -> None:
    """2 % apart at 40 d: within Gaia's 3.9 % resolution, outside ZTF's 1.5 %. The pair is
    judged at the coarser of the two, whichever is the lower period."""
    found = agreement([settled("ztf", "g", ztf, baseline=ZTF_BASELINE)], gaia_period=gaia)

    assert found is not None
    assert found.agreed_sources == ("ztf_g", "gaia_published")
