"""The review form (spec §9 `POST /proposals/{id}/review`): approve, edit or reject, with notes."""

from collections.abc import Iterator
from decimal import Decimal
from itertools import count
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from argelander.config import Settings
from argelander.db.models import (
    Analysis,
    Proposal,
    ProposalStatus,
    Review,
    ReviewDecision,
    Slice,
    Submission,
    SubmissionStatus,
    Target,
    TargetStatus,
)
from argelander.db.session import sync_session_factory
from argelander.web.app import create_app

_oids = count(1)

PLOTS = {
    "phase-ztf-g": "1/0.1.0/phase-ztf-g.png",
    "periodogram": "1/0.1.0/periodogram.png",
    "submission": "1/0.1.0/submission.png",
}
HTMX = {"HX-Request": "true"}


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    """Session factory on a database emptied of Proposals and everything under them."""
    global _oids
    _oids = count(1)
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE submissions, reviews, proposals, analyses, targets, slices "
                "RESTART IDENTITY CASCADE"
            )
        )
    return sync_session_factory(engine)


@pytest.fixture
def client(database_url: str, tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(_env_file=None, database_url=database_url, plots_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        yield client


def _proposal(
    session: Session,
    confidence: str = "0.800",
    plots: dict[str, str] | None = PLOTS,
    status: ProposalStatus = ProposalStatus.QUEUED,
) -> Proposal:
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
        status=TargetStatus.PROPOSED,
    )
    session.add(target)
    session.flush()
    analysis = Analysis(
        target_id=target.id,
        pipeline_version="0.1.0",
        inputs={},
        periods={},
        agreement=False,
        plots=plots,
    )
    session.add(analysis)
    session.flush()
    proposal = Proposal(
        analysis_id=analysis.id,
        target_id=target.id,
        pipeline_version="0.1.0",
        proposed_type="RRAB",
        period=0.5123456,
        epoch_bjd=2459000.12345,
        mag_max=14.05,
        mag_min=14.85,
        band="g",
        confidence=Decimal(confidence),
        evidence=[],
        status=status,
    )
    session.add(proposal)
    session.flush()
    return proposal


def _counts(session: Session) -> tuple[int, int]:
    return (
        session.scalar(select(func.count()).select_from(Review)) or 0,
        session.scalar(select(func.count()).select_from(Submission)) or 0,
    )


# The edit fields as the form arrives pre-filled from `_proposal`'s values.
FILLED = {
    "proposed_type": "RRAB",
    "period": "0.5123456",
    "epoch_bjd": "2459000.12345",
    "mag_max": "14.05",
    "mag_min": "14.85",
    "band": "g",
    "plot": "submission",
}


def _post(client: TestClient, proposal_id: int, **data: Any) -> Any:
    return client.post(f"/proposals/{proposal_id}/review", data=data, headers=HTMX)


@pytest.mark.parametrize("notes", [None, "", "   \n "])
def test_a_review_without_notes_is_a_422_and_writes_nothing(
    sessions: sessionmaker[Session], client: TestClient, notes: str | None
) -> None:
    with sessions.begin() as session:
        proposal_id = _proposal(session).id

    data = {"decision": "approve"} | ({} if notes is None else {"notes": notes})
    response = _post(client, proposal_id, **data)

    assert response.status_code == 422
    assert "Notes are required" in response.text
    with sessions() as session:
        assert _counts(session) == (0, 0)
        assert session.get_one(Proposal, proposal_id).status == ProposalStatus.QUEUED


def test_approve_writes_a_review_approves_and_prepares_one_submission(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    with sessions.begin() as session:
        proposal = _proposal(session)
        proposal_id, target_id = proposal.id, proposal.target_id

    response = _post(client, proposal_id, decision="approve", notes="  Clean RRAB fold. ")

    assert response.status_code == 200
    assert "Approved" in response.text
    with sessions() as session:
        review = session.scalars(select(Review)).one()
        submission = session.scalars(select(Submission)).one()
        assert review.proposal_id == proposal_id
        assert review.decision == ReviewDecision.APPROVE
        assert review.notes == "Clean RRAB fold."
        assert review.edited is None
        assert session.get_one(Proposal, proposal_id).status == ProposalStatus.APPROVED
        assert submission.status == SubmissionStatus.PREPARED
        assert (submission.proposal_id, submission.review_id) == (proposal_id, review.id)
        assert submission.plot_path == PLOTS["submission"]
        assert submission.form_fields is None
        assert session.get_one(Target, target_id).status == TargetStatus.REVIEWED


def test_edit_stores_the_changed_fields_and_the_submission_reflects_them(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    with sessions.begin() as session:
        proposal_id = _proposal(session).id

    # The form arrives pre-filled with the Proposal's values: only the changed ones are kept.
    edits = {"proposed_type": "RRAB:", "period": "0.5123999", "band": "r", "plot": "phase-ztf-g"}
    response = _post(
        client,
        proposal_id,
        decision="edit",
        notes="Gaia's period folds better; ZTF r is the cleaner band.",
        **(FILLED | edits),
    )

    assert response.status_code == 200
    with sessions() as session:
        review = session.scalars(select(Review)).one()
        submission = session.scalars(select(Submission)).one()
        assert review.decision == ReviewDecision.EDIT
        assert review.edited == {
            "uncertain": True,
            "period": 0.5123999,
            "band": "r",
            "plot": "phase-ztf-g",
        }
        assert session.get_one(Proposal, proposal_id).status == ProposalStatus.APPROVED
        # The form renderer (M7) reads the edits through the Submission's Review.
        assert submission.review_id == review.id
        assert submission.status == SubmissionStatus.PREPARED
        assert submission.plot_path == PLOTS["phase-ztf-g"]
        # The Proposal itself keeps the classifier's values.
        assert session.get_one(Proposal, proposal_id).period == 0.5123456


def test_reject_rejects_and_prepares_no_submission(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    with sessions.begin() as session:
        proposal = _proposal(session)
        proposal_id, target_id = proposal.id, proposal.target_id

    # The edit fields are ignored on a rejection, however they were left.
    response = _post(client, proposal_id, decision="reject", notes="Blend.", period="nonsense")

    assert response.status_code == 200
    assert "Rejected" in response.text
    with sessions() as session:
        review = session.scalars(select(Review)).one()
        assert (review.decision, review.edited) == (ReviewDecision.REJECT, None)
        assert session.get_one(Proposal, proposal_id).status == ProposalStatus.REJECTED
        assert _counts(session) == (1, 0)
        assert session.get_one(Target, target_id).status == TargetStatus.REVIEWED


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("proposed_type", "RRX", "not a VSX type code"),
        ("proposed_type", "", "Type is required"),
        ("period", "abc", "not a number"),
        ("period", "-1", "must be positive"),
        ("period", "nan", "not a number"),
        ("epoch_bjd", "59000.1", "a full BJD"),
        ("mag_max", "15.5", "brighter, smaller number"),
        ("band", "Q", "not a VSX passband"),
        ("plot", "nowhere", "not a plot of this Analysis"),
    ],
)
def test_an_edit_whose_fields_do_not_parse_is_a_422_and_writes_nothing(
    sessions: sessionmaker[Session], client: TestClient, field: str, value: str, message: str
) -> None:
    with sessions.begin() as session:
        proposal_id = _proposal(session).id

    data = FILLED | {field: value}
    response = _post(client, proposal_id, decision="edit", notes="Edited.", **data)

    assert response.status_code == 422
    assert message in response.text
    # The form comes back as it was filled in (the plot is a select, so it has no value).
    if field != "plot":
        assert f'value="{value}"' in response.text
    with sessions() as session:
        assert _counts(session) == (0, 0)


def test_an_edit_that_changes_nothing_is_a_422(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    with sessions.begin() as session:
        proposal_id = _proposal(session).id

    response = _post(client, proposal_id, decision="edit", notes="Edited.", **FILLED)

    assert response.status_code == 422
    assert "Nothing was changed" in response.text


def test_a_missing_or_unknown_decision_is_a_422(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    with sessions.begin() as session:
        proposal_id = _proposal(session).id

    assert _post(client, proposal_id, notes="Notes.").status_code == 422
    assert _post(client, proposal_id, decision="maybe", notes="Notes.").status_code == 422


def test_approving_without_a_plot_to_attach_is_a_422(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    with sessions.begin() as session:
        proposal_id = _proposal(session, plots=None).id

    response = _post(client, proposal_id, decision="approve", notes="Looks right.")

    assert response.status_code == 422
    assert "no plot to attach" in response.text


def test_without_a_submission_plot_approve_is_a_422_but_an_edit_can_pick_one(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    plots = {kind: path for kind, path in PLOTS.items() if kind != "submission"}
    with sessions.begin() as session:
        proposal_id = _proposal(session, plots=plots).id

    approve = _post(client, proposal_id, decision="approve", notes="Looks right.")
    edit = _post(
        client,
        proposal_id,
        decision="edit",
        notes="The g fold is the clearest.",
        **(FILLED | {"plot": "phase-ztf-g"}),
    )

    assert approve.status_code == 422
    assert "choose one to attach with an edit" in approve.text
    assert edit.status_code == 200
    with sessions() as session:
        assert session.scalars(select(Review)).one().edited == {"plot": "phase-ztf-g"}
        assert session.scalars(select(Submission)).one().plot_path == PLOTS["phase-ztf-g"]


def test_a_reviewed_proposal_cannot_be_reviewed_again(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    with sessions.begin() as session:
        proposal_id = _proposal(session).id
    assert _post(client, proposal_id, decision="reject", notes="Blend.").status_code == 200

    again = _post(client, proposal_id, decision="approve", notes="Second thoughts.")

    assert again.status_code == 409
    # The panel shows the Review already recorded.
    assert "Rejected" in again.text and "Blend." in again.text
    with sessions() as session:
        assert _counts(session) == (1, 0)


def test_an_unknown_proposal_is_a_404(sessions: sessionmaker[Session], client: TestClient) -> None:
    assert _post(client, 999, decision="approve", notes="Notes.").status_code == 404


def test_the_reviewed_partial_advances_to_the_next_queued_proposal(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    with sessions.begin() as session:
        top = _proposal(session, confidence="0.900").id
        middle = _proposal(session, confidence="0.800").id
        last = _proposal(session, confidence="0.700").id

    after_middle = _post(client, middle, decision="reject", notes="No.")
    after_last = _post(client, last, decision="reject", notes="No.")
    after_top = _post(client, top, decision="reject", notes="No.")

    assert after_middle.headers["HX-Location"] == f"/proposals/{last}"
    # The bottom of the queue wraps to its top; an empty queue stays put and offers the way
    # back to it.
    assert after_last.headers["HX-Location"] == f"/proposals/{top}"
    assert "HX-Location" not in after_top.headers
    assert "The queue is empty" in after_top.text


def test_a_plain_form_post_is_redirected_to_the_next_queued_proposal(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    with sessions.begin() as session:
        first = _proposal(session, confidence="0.900").id
        second = _proposal(session, confidence="0.800").id

    response = client.post(
        f"/proposals/{first}/review",
        data={"decision": "approve", "notes": "Fine."},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/proposals/{second}"


def test_a_plain_form_post_with_errors_gets_the_whole_page_back(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    with sessions.begin() as session:
        proposal_id = _proposal(session).id

    response = client.post(f"/proposals/{proposal_id}/review", data={"decision": "approve"})

    assert response.status_code == 422
    assert "<h2>Proposal and VSX</h2>" in response.text
    assert "Notes are required" in response.text


def test_the_evidence_page_embeds_the_form_pre_filled_from_the_proposal(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    with sessions.begin() as session:
        proposal_id = _proposal(session).id

    html = client.get(f"/proposals/{proposal_id}").text

    assert f'hx-post="/proposals/{proposal_id}/review"' in html
    assert '<textarea name="notes" id="review-notes" rows="4" required>' in html
    assert 'name="period" value="0.5123456"' in html
    assert 'name="epoch_bjd" value="2459000.12345"' in html
    # The submission plot is chosen by default.
    assert '<option value="submission" selected>' in html


def test_a_superseded_proposal_has_no_form(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    with sessions.begin() as session:
        proposal_id = _proposal(session, status=ProposalStatus.SUPERSEDED).id

    html = client.get(f"/proposals/{proposal_id}").text
    response = _post(client, proposal_id, decision="approve", notes="Notes.")

    assert "hx-post=" not in html
    assert "not open to review" in html
    assert response.status_code == 409
