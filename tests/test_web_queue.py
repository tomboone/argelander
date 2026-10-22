"""The review queue (spec §9 `GET /`): queued Proposals by Confidence, filtered and paged."""

import re
import time
from collections.abc import Iterator
from decimal import Decimal
from itertools import count
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, insert, text
from sqlalchemy.orm import Session, sessionmaker

from argelander.config import Settings
from argelander.db.models import Analysis, Proposal, ProposalStatus, Slice, Target
from argelander.db.session import sync_session_factory
from argelander.web.app import create_app
from argelander.web.queue import PAGE_SIZE

_oids = count(1)

BLENDED = {
    "contaminations": [
        {"survey": "ztf", "flag": "blend_suspect"},
        {"survey": "gaia", "flag": "blend_suspect"},
        {"survey": "tess", "flag": "tess_contaminated"},
    ],
    "zero_points": [],
}
CLEAN = {"contaminations": [], "zero_points": []}


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


def _slice(session: Session) -> int:
    slice_ = Slice(
        name="Dra-13-16", constellation="Dra", mag_min=13.0, mag_max=16.0, pipeline_version="0.1.0"
    )
    session.add(slice_)
    session.flush()
    return slice_.id


def _proposal(
    session: Session,
    slice_id: int,
    confidence: str,
    proposed_type: str = "RRAB",
    status: ProposalStatus = ProposalStatus.QUEUED,
    blend: dict[str, Any] | None = None,
    inputs: dict[str, Any] | None = None,
) -> str:
    """A Target, its Analysis and one Proposal; returns the Target's VSX name."""
    oid = next(_oids)
    target = Target(
        slice_id=slice_id,
        vsx_oid=oid,
        vsx_name=f"V{oid:04d} Dra",
        gaia_source_id=oid,
        ra=270.0,
        dec=60.0,
        vsx_type="VAR",
    )
    session.add(target)
    session.flush()
    analysis = Analysis(
        target_id=target.id,
        pipeline_version="0.1.0",
        inputs=inputs if inputs is not None else {"ztf": {}},
        periods={},
        agreement=True,
        blend=blend,
    )
    session.add(analysis)
    session.flush()
    session.add(
        Proposal(
            analysis_id=analysis.id,
            target_id=target.id,
            pipeline_version="0.1.0",
            proposed_type=proposed_type,
            period=0.5,
            confidence=Decimal(confidence),
            evidence=[],
            status=status,
        )
    )
    session.flush()
    return target.vsx_name


def _names(html: str) -> list[str]:
    """The VSX names of the queue's rows, top to bottom."""
    return re.findall(r'<a href="/proposals/\d+">([^<]+)</a>', html)


def test_the_queue_lists_queued_proposals_by_confidence_descending(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    with sessions.begin() as session:
        slice_id = _slice(session)
        middle = _proposal(session, slice_id, "0.500")
        top = _proposal(session, slice_id, "0.900")
        bottom = _proposal(session, slice_id, "0.100")

    response = client.get("/")

    assert response.status_code == 200
    assert _names(response.text) == [top, middle, bottom]


def test_superseded_approved_and_rejected_proposals_are_counted_but_never_listed(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    with sessions.begin() as session:
        slice_id = _slice(session)
        queued = _proposal(session, slice_id, "0.200")
        _proposal(session, slice_id, "0.990", status=ProposalStatus.SUPERSEDED)
        _proposal(session, slice_id, "0.980", status=ProposalStatus.SUPERSEDED)
        _proposal(session, slice_id, "0.970", status=ProposalStatus.APPROVED)
        _proposal(session, slice_id, "0.960", status=ProposalStatus.REJECTED)

    response = client.get("/")

    assert _names(response.text) == [queued]
    counts = {
        status: n
        for n, status in re.findall(
            r'<li><span class="count">(\d+)</span> (\w+)</li>', response.text
        )
    }
    assert counts == {"queued": "1", "approved": "1", "rejected": "1", "superseded": "2"}


def test_the_filters_narrow_by_type_minimum_confidence_and_blend(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    with sessions.begin() as session:
        slice_id = _slice(session)
        rrab = _proposal(session, slice_id, "0.800", "RRAB", blend=CLEAN)
        blended_rrab = _proposal(session, slice_id, "0.400", "RRAB", blend=BLENDED)
        ea = _proposal(session, slice_id, "0.700", "EA", blend=CLEAN)
        # No blend checks at all (no Gaia record): not blended.
        unchecked = _proposal(session, slice_id, "0.300", "EA", blend=None)

    assert _names(client.get("/?type=RRAB").text) == [rrab, blended_rrab]
    assert _names(client.get("/?min_confidence=0.7").text) == [rrab, ea]
    assert _names(client.get("/?blend=yes").text) == [blended_rrab]
    assert _names(client.get("/?blend=no").text) == [rrab, ea, unchecked]
    assert _names(client.get("/?type=EA&min_confidence=0.5&blend=no").text) == [ea]


def test_blank_filters_mean_no_filter_and_a_bad_one_is_refused(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    with sessions.begin() as session:
        slice_id = _slice(session)
        names = [_proposal(session, slice_id, "0.500"), _proposal(session, slice_id, "0.400")]

    response = client.get("/?type=&min_confidence=&blend=")

    assert response.status_code == 200
    assert _names(response.text) == names
    assert client.get("/?min_confidence=high").status_code == 422
    assert client.get("/?min_confidence=1.5").status_code == 422
    assert client.get("/?blend=maybe").status_code == 422


def test_the_type_filter_offers_the_types_queued_proposals_propose(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    with sessions.begin() as session:
        slice_id = _slice(session)
        _proposal(session, slice_id, "0.500", "RRAB")
        _proposal(session, slice_id, "0.500", "EA")
        _proposal(session, slice_id, "0.500", "DSCT", status=ProposalStatus.SUPERSEDED)

    response = client.get("/?type=EA")

    assert re.findall(r'<option value="(\w+)"', response.text)[:2] == ["EA", "RRAB"]
    assert '<option value="EA" selected>' in response.text
    assert "DSCT" not in response.text


def test_a_row_shows_the_blend_flags_and_the_surveys_with_data(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    with sessions.begin() as session:
        slice_id = _slice(session)
        _proposal(session, slice_id, "0.500", blend=BLENDED, inputs={"ztf": {}, "gaia": {}})

    (row,) = re.findall(r"<tr>\s*<td><a.*?</tr>", client.get("/").text, re.DOTALL)

    assert "<td>blend_suspect, tess_contaminated</td>" in row
    assert "<td>gaia, ztf</td>" in row
    assert "<td>RRAB</td>" in row
    assert "<td>VAR</td>" in row


def test_the_queue_pages_at_fifty_and_the_pager_keeps_the_filters(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    with sessions.begin() as session:
        slice_id = _slice(session)
        # Equal Confidence throughout: the id breaks the tie, so the pages do not overlap.
        names = [_proposal(session, slice_id, "0.500") for _ in range(PAGE_SIZE + 1)]

    first = client.get("/?type=RRAB")
    second = client.get("/?type=RRAB&page=2")

    assert _names(first.text) == names[:PAGE_SIZE]
    assert _names(second.text) == names[PAGE_SIZE:]
    assert "Page 1 of 2" in first.text
    assert 'href="/?type=RRAB&amp;page=2"' in first.text
    assert client.get("/?page=0").status_code == 422


def test_an_htmx_request_gets_the_queue_region_and_a_history_restore_the_page(
    sessions: sessionmaker[Session], client: TestClient
) -> None:
    with sessions.begin() as session:
        name = _proposal(session, _slice(session), "0.500")

    fragment = client.get("/", headers={"HX-Request": "true"})
    restore = client.get("/", headers={"HX-Request": "true", "HX-History-Restore-Request": "true"})

    assert fragment.text.lstrip().startswith('<section id="queue">')
    assert "<html" not in fragment.text
    assert _names(fragment.text) == [name]
    assert "<html" in restore.text
    assert fragment.headers["vary"] == "HX-Request"


def test_the_queue_renders_in_under_500_ms_with_2000_queued_proposals(
    sessions: sessionmaker[Session], engine: Engine, client: TestClient
) -> None:
    n = 2000
    with sessions.begin() as session:
        slice_id = _slice(session)
    with engine.begin() as connection:
        connection.execute(
            insert(Target),
            [
                {
                    "slice_id": slice_id,
                    "vsx_oid": i,
                    "vsx_name": f"V{i:04d} Dra",
                    "gaia_source_id": i,
                    "ra": 270.0,
                    "dec": 60.0,
                }
                for i in range(1, n + 1)
            ],
        )
        blends = (BLENDED, CLEAN, None)
        connection.execute(
            insert(Analysis),
            [
                {
                    "target_id": i,
                    "pipeline_version": "0.1.0",
                    "inputs": {"ztf": {}, "gaia": {}},
                    "periods": {},
                    "agreement": True,
                    "blend": blends[i % 3],
                }
                for i in range(1, n + 1)
            ],
        )
        connection.execute(
            insert(Proposal),
            [
                {
                    "analysis_id": i,
                    "target_id": i,
                    "pipeline_version": "0.1.0",
                    "proposed_type": ("RRAB", "EA", "VAR")[i % 3],
                    "period": 0.5,
                    "confidence": Decimal(i % 1000) / 1000,
                    "evidence": [],
                }
                for i in range(1, n + 1)
            ],
        )
    client.get("/")  # warm: the pool's first connection and the templates' compilation

    for url in ("/", "/?blend=yes&min_confidence=0.5", "/?type=EA&page=10"):
        start = time.perf_counter()
        response = client.get(url)
        elapsed = time.perf_counter() - start

        assert response.status_code == 200
        assert elapsed < 0.5, f"{url} took {elapsed:.3f} s"
    assert len(_names(client.get("/").text)) == PAGE_SIZE
