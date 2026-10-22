"""The Target history page (spec §9 `GET /targets/{id}`): the Target's data, its Light Curve
files and everything done to it, in order."""

import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from argelander.config import Settings
from argelander.db.models import (
    Analysis,
    LightCurveFile,
    LightCurveFileStatus,
    Proposal,
    ProposalStatus,
    Review,
    ReviewDecision,
    Slice,
    Submission,
    SubmissionStatus,
    Survey,
    Target,
)
from argelander.db.session import sync_session_factory
from argelander.web.app import create_app

DAY_0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
EVIDENCE = [
    {
        "rule": "period_range",
        "type_code": "RRAB",
        "verdict": "supports",
        "weight": 1.0,
        "explanation": "0.512 d is inside RRAB's range.",
    }
]


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    """Session factory on a database emptied of Targets and everything under them."""
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE targets, slices RESTART IDENTITY CASCADE"))
    return sync_session_factory(engine)


@pytest.fixture
def client(database_url: str, tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(_env_file=None, database_url=database_url, plots_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        yield client


def _at(days: float) -> datetime:
    return DAY_0 + timedelta(days=days)


def _target(session: Session) -> Target:
    slice_ = Slice(
        name="Dra-13-16", constellation="Dra", mag_min=13.0, mag_max=16.0, pipeline_version="0.1.0"
    )
    session.add(slice_)
    session.flush()
    target = Target(
        slice_id=slice_.id,
        vsx_oid=4321,
        vsx_name="V0001 Dra",
        gaia_source_id=1234567890,
        ra=270.0,
        dec=60.0,
        vsx_type="VAR",
        vsx_period=0.3456789,
        gaia_g=14.3,
        gaia_vari_class="RR",
    )
    session.add(target)
    session.flush()
    return target


def _analysis(session: Session, target: Target, version: str, at: datetime | None) -> Analysis:
    analysis = Analysis(
        target_id=target.id,
        pipeline_version=version,
        inputs={
            "ztf": {"n_points": 812, "t_min_bjd": 2458200.5, "t_max_bjd": 2459720.9, "bands": ["g"]}
        },
        periods={},
        agreement=True,
        agreed_period=0.5123456,
        agreed_sources=["ztf", "gaia"],
        agreement_relation="1:1",
        finished_at=at,
    )
    session.add(analysis)
    session.flush()
    return analysis


def _proposal(session: Session, analysis: Analysis, at: datetime | None) -> Proposal:
    proposal = Proposal(
        analysis_id=analysis.id,
        target_id=analysis.target_id,
        pipeline_version=analysis.pipeline_version,
        proposed_type="RRAB",
        period=0.5123456,
        confidence=Decimal("0.800"),
        evidence=EVIDENCE,
        **({"created_at": at} if at else {}),
    )
    session.add(proposal)
    session.flush()
    return proposal


def _review(session: Session, proposal: Proposal, at: datetime | None) -> Review:
    proposal.status = ProposalStatus.APPROVED
    review = Review(
        proposal_id=proposal.id,
        decision=ReviewDecision.APPROVE,
        notes="Clean RRab fold in ZTF g and r.",
        **({"reviewed_at": at} if at else {}),
    )
    session.add(review)
    session.flush()
    return review


def _submission(session: Session, review: Review, at: datetime | None) -> Submission:
    submission = Submission(
        proposal_id=review.proposal_id,
        review_id=review.id,
        plot_path="1/0.2.0/submission.png",
        **({"created_at": at} if at else {}),
    )
    session.add(submission)
    session.flush()
    return submission


def _timeline(html: str) -> list[str]:
    """The timeline's entries, by the kind and id each opens with."""
    (block,) = re.findall(r'<ol class="timeline">.*?</ol>', html, re.DOTALL)
    return [
        re.sub(r"<[^>]+>", "", heading)
        for heading in re.findall(r"<strong>(.*?)</strong>", block, re.DOTALL)
    ]


def test_a_target_s_analyses_proposals_review_and_submission_render_in_chronological_order(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    # The ids run against the clock: the second Analysis row is the older one, so the order
    # can only come from the timestamps.
    with sessions.begin() as session:
        target = _target(session)
        later = _analysis(session, target, "0.2.0", _at(10))
        earlier = _analysis(session, target, "0.1.0", _at(0))
        first = _proposal(session, earlier, _at(0.1))
        second = _proposal(session, later, _at(10.1))
        first.status = ProposalStatus.SUPERSEDED
        first.superseded_by = second.id
        review = _review(session, second, _at(11))
        submission = _submission(session, review, _at(11))
        submission.status = SubmissionStatus.ACCEPTED
        submission.submitted_at = _at(12)
        submission.verdict_at = _at(20)
        submission.verdict_reason = "Accepted as RRAB."
        ids = (target.id, later.id, earlier.id, first.id, second.id, review.id, submission.id)
    target_id, later_id, earlier_id, first_id, second_id, review_id, submission_id = ids

    response = client.get(f"/targets/{target_id}")

    assert response.status_code == 200
    assert _timeline(response.text) == [
        f"Analysis {earlier_id}",
        f"Proposal {first_id}",
        f"Analysis {later_id}",
        f"Proposal {second_id}",
        f"Review {review_id}",
        f"Submission {submission_id}",  # prepared
        f"Submission {submission_id}",  # submitted
        f"Submission {submission_id}",  # Verdict
    ]
    page = response.text
    assert "Pipeline Version 0.1.0" in page
    assert "Pipeline Version 0.2.0" in page
    assert f'Superseded by <a href="/proposals/{second_id}">' in page
    assert f"Proposal {second_id}</a>: Approved." in page
    assert "Clean RRab fold in ZTF g and r." in page
    assert "Verdict: accepted." in page
    assert "Accepted as RRAB." in page


def test_rows_written_at_one_instant_follow_the_pipeline_s_order(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    # One transaction gives every created_at the same now(), as one worker's write does.
    with sessions.begin() as session:
        target = _target(session)
        analysis = _analysis(session, target, "0.1.0", None)
        proposal = _proposal(session, analysis, None)
        review = _review(session, proposal, None)
        submission = _submission(session, review, None)
        ids = (target.id, analysis.id, proposal.id, review.id, submission.id)
    target_id, analysis_id, proposal_id, review_id, submission_id = ids

    response = client.get(f"/targets/{target_id}")

    assert _timeline(response.text) == [
        f"Analysis {analysis_id}",
        f"Proposal {proposal_id}",
        f"Review {review_id}",
        f"Submission {submission_id}",
    ]


def test_a_same_version_rerun_places_its_queued_proposal_after_the_rewritten_analysis(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    # Spec §4 (NEU-1721): the rerun rewrites the Analysis row, finished_at included, and its
    # queued Proposal in place, which keeps the created_at of the first run.
    with sessions.begin() as session:
        target = _target(session)
        analysis = _analysis(session, target, "0.1.0", _at(5))
        proposal = _proposal(session, analysis, _at(0.1))
        ids = (target.id, analysis.id, proposal.id)
    target_id, analysis_id, proposal_id = ids

    response = client.get(f"/targets/{target_id}")

    assert _timeline(response.text) == [f"Analysis {analysis_id}", f"Proposal {proposal_id}"]


def test_a_submission_needing_changes_has_no_verdict_entry(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    with sessions.begin() as session:
        target = _target(session)
        proposal = _proposal(session, _analysis(session, target, "0.1.0", _at(0)), _at(0))
        submission = _submission(session, _review(session, proposal, _at(1)), _at(1))
        submission.status = SubmissionStatus.NEEDS_CHANGES
        submission.submitted_at = _at(2)
        submission.verdict_at = _at(3)
        target_id = target.id

    page = client.get(f"/targets/{target_id}").text

    assert "Verdict" not in page
    assert "now needs_changes." in page


def test_the_light_curve_files_are_shown_per_survey_and_a_missing_survey_as_no_data(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    with sessions.begin() as session:
        target = _target(session)
        session.add_all(
            [
                LightCurveFile(
                    target_id=target.id,
                    survey=Survey.ZTF,
                    source_key="1+2",
                    path="ztf/1234567890",
                    n_points=812,
                    t_min_bjd=2458200.5,
                    t_max_bjd=2459720.9,
                    bands=["g", "r"],
                    fetched_at=_at(0),
                    fetcher_version="1",
                    status=LightCurveFileStatus.OK,
                ),
                LightCurveFile(
                    target_id=target.id,
                    survey=Survey.TESS,
                    fetched_at=_at(0),
                    fetcher_version="1",
                    status=LightCurveFileStatus.EMPTY,
                ),
            ]
        )
        target_id = target.id

    page = client.get(f"/targets/{target_id}").text

    rows = dict(re.findall(r"<tr[^>]*>\s*<th>(\w+)</th>(.*?)</tr>", page, re.DOTALL))
    assert "812" in rows["ztf"]
    assert "2458200.5 to 2459720.9" in rows["ztf"]
    assert "g, r" in rows["ztf"]
    assert "empty" in rows["tess"]
    assert "no data" in rows["gaia"]
    assert "no data" in rows["asassn"]


def test_the_page_shows_the_target_s_vsx_and_gaia_data(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    with sessions.begin() as session:
        target_id = _target(session).id

    page = client.get(f"/targets/{target_id}").text

    assert "<h1>V0001 Dra</h1>" in page
    assert "4321" in page  # the VSX OID
    assert "0.3456789" in page  # VSX's period
    assert "1234567890" in page  # the Gaia DR3 source
    assert "14.30" in page  # G
    assert "Nothing yet" in page


def test_an_unknown_target_is_a_404(sessions: sessionmaker[Session], client: TestClient) -> None:
    assert client.get("/targets/999").status_code == 404


def test_the_queue_and_the_evidence_page_link_to_the_target_s_history(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    with sessions.begin() as session:
        target = _target(session)
        proposal = _proposal(session, _analysis(session, target, "0.1.0", None), None)
        target_id, proposal_id = target.id, proposal.id

    assert f'href="/targets/{target_id}"' in client.get("/").text
    assert f'href="/targets/{target_id}"' in client.get(f"/proposals/{proposal_id}").text
