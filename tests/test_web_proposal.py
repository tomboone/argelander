"""The evidence page (spec §9 `GET /proposals/{id}`): one Proposal, its evidence and its diff."""

import re
from collections.abc import Iterator
from decimal import Decimal
from itertools import count
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from argelander.config import Settings
from argelander.db.models import Analysis, Proposal, ProposalStatus, Slice, Target
from argelander.db.session import sync_session_factory
from argelander.web.app import create_app
from argelander.web.proposal import plot_order

_oids = count(1)

PLOTS = {
    "periodogram": "1/0.1.0/periodogram.png",
    "phase-ztf-r": "1/0.1.0/phase-ztf-r.png",
    "cmd": "1/0.1.0/cmd.png",
    "phase-ztf-g": "1/0.1.0/phase-ztf-g.png",
    "combined": "1/0.1.0/combined.png",
    "submission": "1/0.1.0/submission.png",
}
PEAK = {"period": 0.5123456, "power": 0.61, "false_alarm_probability": 1e-12}
ALIAS_PEAK = {"period": 1.0246912, "power": 0.2, "false_alarm_probability": None}
PERIODS = {
    "ztf": {
        "g": {
            "lomb_scargle": {
                "n_points": 812,
                "insufficient": False,
                "peaks": [PEAK, ALIAS_PEAK],
                "gaia": None,
            },
            "bls": {"n_points": 812, "insufficient": True, "peaks": [], "gaia": None},
        }
    }
}
ALIAS_TESTS = {
    "ztf": {
        "g": {
            "baseline": 1520.4,
            "ranking": [
                {
                    "period": 0.5123456,
                    "candidate": 0.5123456,
                    "relation": "P",
                    "bins": 25,
                    "scatter": 0.081,
                    "score": -1234.5,
                },
                {
                    "period": 1.0246912,
                    "candidate": 0.5123456,
                    "relation": "2P",
                    "bins": 50,
                    "scatter": 0.11,
                    "score": -1100.0,
                },
            ],
        }
    }
}
BLEND = {
    "contaminations": [
        {
            "survey": "ztf",
            "flag": "blend_suspect",
            "neighbours": [{"source_id": 99, "separation": 1.8, "g": 15.2}],
            "flux_fraction": 0.25,
            "remark": 'ZTF magnitudes contaminated by Gaia DR3 99 (G= 15.2; sep. 1.8"). '
            "Range not corrected.",
        }
    ],
    "zero_points": [],
}
CMD = {"bp_rp": 0.42, "abs_g": 0.61, "distance_pc": 4210.0, "region": "instability_strip"}


def _evidence(rule: str, type_code: str, verdict: str, weight: float, why: str) -> dict[str, Any]:
    return {
        "rule": rule,
        "type_code": type_code,
        "verdict": verdict,
        "weight": weight,
        "explanation": why,
    }


AGREED_EVIDENCE = [
    _evidence("period_range", "RRAB", "supports", 1.0, "0.512 d is inside RRAB's range."),
    _evidence("period_range", "EW", "against", 1.0, "0.512 d is outside EW's range."),
    _evidence("amplitude", "RRAB", "supports", 0.5, "An amplitude of 0.8 mag suits RRAB."),
]
# Without Agreement (spec §7) every periodic type is opposed. EW and RRAB tie at 0, EW seen
# first; Gaia's RR class breaks the tie for RRAB, as `confidence.best_type` does.
UNAGREED_EVIDENCE = [
    _evidence(
        "agreement",
        "EW",
        "against",
        1.0,
        "No two sources agree on a period, so EW, a periodic type, cannot be proposed.",
    ),
    _evidence("gaia_class", "RRAB", "supports", 1.0, "Gaia DR3 classifies it RR."),
    _evidence(
        "agreement",
        "RRAB",
        "against",
        1.0,
        "No two sources agree on a period, so RRAB, a periodic type, cannot be proposed.",
    ),
]


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    """Session factory on a database emptied of Proposals and everything under them."""
    global _oids
    _oids = count(1)
    with engine.begin() as connection:
        connection.execute(
            text("TRUNCATE proposals, analyses, targets, slices RESTART IDENTITY CASCADE")
        )
    return sync_session_factory(engine)


@pytest.fixture
def client(database_url: str, tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(_env_file=None, database_url=database_url, plots_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        yield client


def _target(session: Session) -> Target:
    slice_ = Slice(
        name="Dra-13-16", constellation="Dra", mag_min=13.0, mag_max=16.0, pipeline_version="0.1.0"
    )
    session.add(slice_)
    session.flush()
    oid = next(_oids)
    target = Target(
        slice_id=slice_.id,
        vsx_oid=oid,
        vsx_name=f"V{oid:04d} Dra",
        gaia_source_id=oid,
        ra=270.0,
        dec=60.0,
        vsx_type="VAR",
        vsx_period=0.3456789,
        vsx_mag_max=14.1,
        vsx_mag_min=14.9,
        vsx_band="V",
        gaia_vari_class="RR",
        gaia_vari_class_score=0.93,
        gaia_vari_period=0.5123,
    )
    session.add(target)
    session.flush()
    return target


def _analysis(
    session: Session, target: Target, pipeline_version: str, agreed: bool = True
) -> Analysis:
    analysis = Analysis(
        target_id=target.id,
        pipeline_version=pipeline_version,
        inputs={
            "ztf": {
                "light_curve_file_id": 1,
                "n_points": 812,
                "t_min_bjd": 2458200.5,
                "t_max_bjd": 2459720.9,
                "bands": ["g", "r"],
            }
        },
        periods=PERIODS,
        alias_tests=ALIAS_TESTS,
        agreement=agreed,
        agreed_period=0.5123456 if agreed else None,
        agreed_sources=["ztf", "gaia"] if agreed else None,
        agreement_relation="1:1" if agreed else None,
        agreement_tolerance=0.001 if agreed else None,
        blend=BLEND,
        cmd=CMD,
        plots=PLOTS,
    )
    session.add(analysis)
    session.flush()
    return analysis


def _proposal(
    session: Session,
    analysis: Analysis,
    period: float | None = 0.5123456,
    epoch_bjd: float | None = 2459000.12345,
    confidence: str = "0.800",
    status: ProposalStatus = ProposalStatus.QUEUED,
    proposed_type: str = "RRAB",
    evidence: list[dict[str, Any]] | None = None,
) -> Proposal:
    proposal = Proposal(
        analysis_id=analysis.id,
        target_id=analysis.target_id,
        pipeline_version=analysis.pipeline_version,
        proposed_type=proposed_type,
        period=period,
        epoch_bjd=epoch_bjd,
        mag_max=14.05,
        mag_min=14.85,
        band="g",
        confidence=Decimal(confidence),
        evidence=evidence if evidence is not None else AGREED_EVIDENCE,
        status=status,
    )
    session.add(proposal)
    session.flush()
    return proposal


def _diff_rows(html: str) -> dict[str, tuple[str, str, bool]]:
    """The diff block's rows: field → (earlier, current, marked as changed)."""
    (block,) = re.findall(r'<section class="diff">.*?</section>', html, re.DOTALL)
    rows = re.findall(
        r"<tr( class=\"changed\")?>\s*<th>([^<]+)</th><td>([^<]*)</td><td>([^<]*)</td>", block
    )
    return {label: (earlier, current, bool(changed)) for changed, label, earlier, current in rows}


def test_two_proposals_for_a_target_render_both_and_the_diff_marks_the_changed_period(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    with sessions.begin() as session:
        target = _target(session)
        earlier = _proposal(
            session, _analysis(session, target, "0.1.0"), period=0.5123456, confidence="0.700"
        )
        current = _proposal(session, _analysis(session, target, "0.2.0"), period=1.0246912)
        earlier.status = ProposalStatus.SUPERSEDED
        earlier.superseded_by = current.id
        earlier_id, current_id = earlier.id, current.id

    response = client.get(f"/proposals/{current_id}")

    assert response.status_code == 200
    assert f'<a href="/proposals/{earlier_id}">Proposal {earlier_id}</a> (superseded)' in (
        response.text
    )
    rows = _diff_rows(response.text)
    assert rows["Period (d)"] == ("0.5123456", "1.024691", True)
    assert rows["Confidence"] == ("0.700", "0.800", True)
    assert rows["Pipeline Version"] == ("0.1.0", "0.2.0", True)
    assert rows["Type"] == ("RRAB", "RRAB", False)
    assert rows["Band"] == ("g", "g", False)
    # The earlier one links forward to the Proposal that superseded it, and has no diff.
    older = client.get(f"/proposals/{earlier_id}").text
    assert f'Superseded by <a href="/proposals/{current_id}">' in older
    assert '<section class="diff">' not in older


def test_an_approved_proposal_diffs_into_and_links_to_the_newer_one_beside_it(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    # Spec §4: an approved or rejected Proposal is never superseded; the newer one is queued
    # beside it, and the app shows them side by side.
    with sessions.begin() as session:
        target = _target(session)
        approved = _proposal(
            session,
            _analysis(session, target, "0.1.0"),
            proposed_type="EW",
            status=ProposalStatus.APPROVED,
        )
        newer = _proposal(session, _analysis(session, target, "0.2.0"))
        approved_id, newer_id = approved.id, newer.id

    newest = client.get(f"/proposals/{newer_id}").text
    older = client.get(f"/proposals/{approved_id}").text

    assert f"Proposal {approved_id}</a> (approved)" in newest
    assert _diff_rows(newest)["Type"] == ("EW", "RRAB", True)
    assert _diff_rows(newest)["Period (d)"] == ("0.5123456", "0.5123456", False)
    assert f'The Target\'s newest is <a href="/proposals/{newer_id}">' in older
    assert "newest is" not in newest


def test_every_plot_in_the_analysis_is_an_img_under_plots(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    with sessions.begin() as session:
        proposal_id = _proposal(session, _analysis(session, _target(session), "0.1.0")).id

    html = client.get(f"/proposals/{proposal_id}").text

    sources = re.findall(r'<img src="([^"]+)"', html)
    assert sources == [f"/plots/{PLOTS[kind]}" for kind, _ in plot_order(PLOTS)]
    assert sorted(sources) == sorted(f"/plots/{path}" for path in PLOTS.values())


def test_the_plots_are_laid_out_phase_plots_first_then_the_spec_order() -> None:
    kinds = [kind for kind, _ in plot_order({**PLOTS, "extra": "x.png"})]

    assert kinds == [
        "phase-ztf-g",
        "phase-ztf-r",
        "periodogram",
        "cmd",
        "combined",
        "submission",
        "extra",
    ]
    assert plot_order(None) == ()


def test_a_proposal_without_agreement_leads_with_the_agreement_rules_explanation(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    with sessions.begin() as session:
        analysis = _analysis(session, _target(session), "0.1.0", agreed=False)
        proposal_id = _proposal(
            session,
            analysis,
            period=None,
            confidence="0.000",
            proposed_type="VAR",
            evidence=UNAGREED_EVIDENCE,
        ).id

    html = client.get(f"/proposals/{proposal_id}").text

    (alert,) = re.findall(r'<div class="alert" role="alert">(.*?)</div>', html, re.DOTALL)
    # RRAB scored best (Gaia's class breaks the tie with EW), so its explanation is the one,
    # and its Evidence group comes first.
    assert "No Agreement." in alert
    assert "so RRAB, a periodic type, cannot be proposed." in alert
    assert re.findall(r"<h3>(\w+) <span class=\"muted\">Confidence", html) == ["RRAB", "EW"]
    # The alert comes before the fields, at the top of the page.
    assert html.index('class="alert"') < html.index("<h2>Proposal and VSX</h2>")


def test_the_alert_follows_the_proposals_evidence_not_a_rerun_of_its_analysis(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    # A same-version rerun found Agreement and overwrote the Analysis row beneath a reviewed
    # Proposal proposed without it (spec §4, NEU-1721): the Proposal's own Evidence decides.
    with sessions.begin() as session:
        analysis = _analysis(session, _target(session), "0.1.0", agreed=True)
        proposal_id = _proposal(
            session,
            analysis,
            period=None,
            confidence="0.000",
            status=ProposalStatus.REJECTED,
            proposed_type="VAR",
            evidence=UNAGREED_EVIDENCE,
        ).id

    html = client.get(f"/proposals/{proposal_id}").text

    assert "so RRAB, a periodic type, cannot be proposed." in html
    assert 'class="alert"' in html


def test_a_proposal_with_agreement_has_no_alert(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    with sessions.begin() as session:
        proposal_id = _proposal(session, _analysis(session, _target(session), "0.1.0")).id

    html = client.get(f"/proposals/{proposal_id}").text

    assert 'class="alert"' not in html
    assert "ztf, gaia agree on 0.5123456 d (1:1, tolerance 0.001)" in html


def test_the_page_shows_the_proposal_beside_vsx_and_all_the_evidence(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    with sessions.begin() as session:
        proposal_id = _proposal(session, _analysis(session, _target(session), "0.1.0")).id

    html = client.get(f"/proposals/{proposal_id}").text

    assert (
        '<tr><th>Period (d)</th><td class="number">0.5123456</td>'
        '<td class="number">0.3456789</td></tr>'
    ) in html
    assert "<tr><th>Type</th><td>RRAB</td><td>VAR</td></tr>" in html
    # Evidence grouped by type, the best-scoring type first.
    assert re.findall(r"<h3>(\w+) <span class=\"muted\">Confidence ([\d.]+)</span>", html) == [
        ("RRAB", "1.000"),
        ("EW", "0.000"),
    ]
    assert "0.512 d is outside EW&#39;s range." in html
    # Per-survey peaks, the alias ranking, the blend, the CMD and Gaia's own class.
    assert "<h4>ztf g: top peaks</h4>" in html
    assert "insufficient (812 points)" in html
    assert "baseline 1520.4 d" in html
    assert "<td>2P</td>" in html
    assert "<h3>ztf: blend_suspect</h3>" in html
    assert "Light from other sources: 25%." in html
    assert "region instability_strip" in html
    assert "Class RR (score 0.93)" in html
    assert "period 0.5123 d" in html


def _field_row(html: str, label: str) -> tuple[str, str]:
    """The Proposal-and-VSX table's row for `label`: (proposed, VSX now)."""
    (row,) = re.findall(
        rf"<tr><th>{re.escape(label)}</th><td[^>]*>([^<]*)</td><td[^>]*>([^<]*)</td></tr>", html
    )
    return row


def test_the_epoch_is_shown_in_hjd_under_the_bjd_for_the_revision_form(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    with sessions.begin() as session:
        proposal_id = _proposal(session, _analysis(session, _target(session), "0.1.0")).id

    html = client.get(f"/proposals/{proposal_id}").text

    assert html.index("<th>Epoch (BJD)</th>") < html.index("<th>Epoch (HJD)</th>")
    hjd, vsx = _field_row(html, "Epoch (HJD)")
    # VSX takes HJD to four decimals; it trails BJD_TDB by about TDB − UTC, 69 s.
    assert re.fullmatch(r"\d{7}\.\d{4}", hjd)
    assert float(hjd) == pytest.approx(2459000.12345 - 0.0008, abs=0.0001)
    assert vsx == "—"


def test_a_proposal_without_an_epoch_shows_a_dash_for_its_hjd(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    with sessions.begin() as session:
        analysis = _analysis(session, _target(session), "0.1.0")
        proposal_id = _proposal(session, analysis, proposed_type="SR", epoch_bjd=None).id

    response = client.get(f"/proposals/{proposal_id}")

    assert response.status_code == 200
    assert _field_row(response.text, "Epoch (HJD)") == ("—", "—")


def test_previous_and_next_step_through_the_queue_in_its_order(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    with sessions.begin() as session:
        ids = [
            _proposal(session, _analysis(session, _target(session), "0.1.0"), confidence=c).id
            for c in ("0.900", "0.500", "0.500", "0.100")
        ]
        # A reviewed one is not in the queue, so the steps pass over it.
        _proposal(
            session,
            _analysis(session, _target(session), "0.1.0"),
            confidence="0.300",
            status=ProposalStatus.APPROVED,
        )

    def steps(proposal_id: int) -> tuple[list[str], list[str]]:
        html = client.get(f"/proposals/{proposal_id}").text
        return (
            re.findall(r'<a href="/proposals/(\d+)" rel="prev">', html),
            re.findall(r'<a href="/proposals/(\d+)" rel="next">', html),
        )

    top, tie_first, tie_second, bottom = (str(i) for i in ids)
    assert steps(ids[0]) == ([], [tie_first])
    assert steps(ids[1]) == ([top], [tie_second])
    assert steps(ids[2]) == ([tie_first], [bottom])
    assert steps(ids[3]) == ([tie_second], [])


def test_an_unknown_proposal_is_a_404(sessions: sessionmaker[Session], client: TestClient) -> None:
    assert client.get("/proposals/12345").status_code == 404
    assert client.get("/proposals/abc").status_code == 422
