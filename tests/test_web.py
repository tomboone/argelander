"""The review app's skeleton (spec §9): the layout, the static files, the plots and /healthz."""

import asyncio
import logging
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from typer.testing import CliRunner

from argelander.cli import app as cli
from argelander.config import Settings
from argelander.logs import dict_config
from argelander.web import app as web_app
from argelander.web.app import Session, create_app

# Nothing listens on port 1, so every connection is refused at once: Postgres is down.
UNREACHABLE = "postgresql+psycopg://argelander:argelander@127.0.0.1:1/argelander"


def _client(database_url: str, plots_dir: Path) -> TestClient:
    settings = Settings(_env_file=None, database_url=database_url, plots_dir=plots_dir)
    return TestClient(create_app(settings))


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """An app whose database is down: enough for every route but a passing /healthz."""
    with _client(UNREACHABLE, tmp_path) as client:
        yield client


def test_healthz_is_200_with_the_database_up(database_url: str, tmp_path: Path) -> None:
    with _client(database_url, tmp_path) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_is_503_with_the_database_down(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_healthz_answers_503_at_the_timeout_though_the_ping_is_slow_to_cancel(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def hung(sessions: object) -> None:
        try:
            await asyncio.sleep(60)  # Postgres accepted the query and never answered
        except asyncio.CancelledError:
            await asyncio.sleep(0.5)  # psycopg's cancel and cleanup, which outlast the timeout
            raise

    monkeypatch.setattr(web_app, "_ping", hung)
    monkeypatch.setattr(web_app, "HEALTHZ_TIMEOUT", 0.1)

    start = time.perf_counter()
    response = client.get("/healthz")

    assert response.status_code == 503
    assert time.perf_counter() - start < 0.4


def test_the_session_dependency_gives_a_route_a_working_session(
    database_url: str, tmp_path: Path
) -> None:
    settings = Settings(_env_file=None, database_url=database_url, plots_dir=tmp_path)
    app = create_app(settings)

    @app.get("/probe")
    async def probe(session: Session) -> int:
        return (await session.execute(text("SELECT 41 + 1"))).scalar_one()

    with TestClient(app) as client:
        assert client.get("/probe").json() == 42


def test_the_root_serves_the_base_layout_with_htmx_and_the_stylesheet(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<title>Argelander</title>" in response.text
    assert "/static/htmx.min.js" in response.text
    assert "/static/style.css" in response.text


def test_the_vendored_static_files_are_served(client: TestClient) -> None:
    htmx = client.get("/static/htmx.min.js")
    css = client.get("/static/style.css")

    assert htmx.status_code == 200
    assert htmx.text.startswith("var htmx=")
    assert css.status_code == 200


def test_a_plot_under_plots_dir_is_served_at_plots(client: TestClient, tmp_path: Path) -> None:
    plot = tmp_path / "V1 Dra" / "phase.png"
    plot.parent.mkdir()
    plot.write_bytes(b"\x89PNG\r\n\x1a\n")

    response = client.get("/plots/V1 Dra/phase.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == b"\x89PNG\r\n\x1a\n"


def test_the_plots_are_read_only(client: TestClient, tmp_path: Path) -> None:
    (tmp_path / "phase.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    assert client.put("/plots/phase.png", content=b"x").status_code == 405
    assert client.delete("/plots/phase.png").status_code == 405


def test_a_missing_plots_dir_is_created_so_a_missing_plot_is_404(tmp_path: Path) -> None:
    with _client(UNREACHABLE, tmp_path / "absent") as client:
        assert client.get("/plots/phase.png").status_code == 404
    assert (tmp_path / "absent").is_dir()


def test_each_request_is_logged_with_its_method_path_status_and_duration(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="argelander.web"):
        client.get("/healthz")

    (record,) = [r for r in caplog.records if hasattr(r, "method")]
    assert vars(record)["method"] == "GET"
    assert vars(record)["path"] == "/healthz"
    assert vars(record)["status"] == 503
    assert vars(record)["duration_ms"] >= 0


def test_a_request_whose_route_raises_is_logged_as_a_500(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = Settings(_env_file=None, database_url=UNREACHABLE, plots_dir=tmp_path)
    app = create_app(settings)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("boom")

    with (
        TestClient(app, raise_server_exceptions=False) as client,
        caplog.at_level(logging.INFO, logger="argelander.web"),
    ):
        assert client.get("/boom").status_code == 500

    (record,) = [r for r in caplog.records if hasattr(r, "method")]
    assert vars(record)["status"] == 500


def test_the_web_command_runs_uvicorn_on_loopback_port_8000(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr("uvicorn.run", lambda app, **options: calls.append(options | {"app": app}))

    result = CliRunner().invoke(cli, ["web"])

    assert result.exit_code == 0, result.output
    (options,) = calls
    assert options["app"] == "argelander.web.app:create_app"
    assert options["factory"] is True
    assert options["host"] == "127.0.0.1"
    assert options["port"] == 8000
    assert options["access_log"] is False
    assert options["log_config"] == dict_config()
