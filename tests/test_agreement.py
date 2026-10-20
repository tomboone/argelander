"""The Agreement rule (spec §6 step 6): which sources' alias-tested periods settle a period."""

from argelander.analysis.agreement import agreement
from argelander.analysis.model import AliasTest, Hypothesis


def settled(survey: str, band: str, period: float) -> AliasTest:
    """A band whose alias test settled on `period`."""
    return AliasTest(
        survey=survey,
        band=band,
        ranking=(
            Hypothesis(
                period=period, candidate=period, relation="P", bins=20, scatter=0.1, score=0.0
            ),
        ),
    )


def test_two_sources_within_a_tenth_of_a_percent_agree() -> None:
    found = agreement([settled("ztf", "g", 0.6), settled("asassn", "V", 0.6004)])

    assert found is not None
    assert found.relation == "1:1"
    assert found.agreed_sources == ("ztf_g", "asassn")
    assert abs(found.agreed_period - 0.6002) < 1e-12


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
