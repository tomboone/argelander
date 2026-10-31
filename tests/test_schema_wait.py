"""The workers' wait for the schema head (NEU-1947), against a real Postgres.

Under Watchtower only the api migrates on start, so fetch-worker and analyze-worker wait until
the database is at the head revision their image carries. The `engine` fixture (conftest.py)
migrates a throwaway database to head; `one_behind` steps it back a revision and restores it.
"""

import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from alembic import command
from sqlalchemy import Engine, text
from typer.testing import CliRunner

from argelander.analysis import worker as analyze_worker
from argelander.cli import app
from argelander.config import get_settings
from argelander.db import migrations
from argelander.db.migrations import (
    SchemaNotAtHead,
    alembic_config,
    upgrade,
    wait_for_schema_head,
)
from argelander.db.session import sync_session_factory
from argelander.fetch import worker as fetch_worker

runner = CliRunner()


@pytest.fixture
def one_behind(database_url: str, engine: Engine) -> Iterator[None]:
    """The database one revision behind head, as when the api has not migrated yet."""
    config = alembic_config(database_url)
    command.downgrade(config, "-1")
    try:
        yield
    finally:
        command.upgrade(config, "head")


def test_at_head_returns_without_waiting(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(migrations.time, "sleep", sleeps.append)

    wait_for_schema_head(sync_session_factory(engine), timeout=0, interval=5)

    assert sleeps == []


@pytest.mark.usefixtures("one_behind")
def test_one_revision_behind_waits_then_gives_up(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []
    real_sleep = time.sleep

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        real_sleep(seconds)

    monkeypatch.setattr(migrations.time, "sleep", sleep)

    with pytest.raises(SchemaNotAtHead, match="argelander db-upgrade"):
        wait_for_schema_head(sync_session_factory(engine), timeout=0.2, interval=0.05)

    assert sleeps
    assert set(sleeps) == {0.05}


def test_a_database_ahead_of_the_image_is_not_told_to_run_db_upgrade(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An older image on a database a newer one migrated: a revision this image does not carry.
    with engine.begin() as connection:
        (head,) = connection.execute(text("SELECT version_num FROM alembic_version")).scalars()
        connection.execute(text("UPDATE alembic_version SET version_num = 'feedfacecafe'"))
    try:
        with pytest.raises(SchemaNotAtHead, match="feedfacecafe is newer than this image's"):
            wait_for_schema_head(sync_session_factory(engine), timeout=0, interval=5)
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE alembic_version SET version_num = :head"), {"head": head}
            )


@pytest.mark.usefixtures("one_behind")
def test_returns_once_the_database_reaches_head_during_the_wait(
    database_url: str, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []

    def migrate(seconds: float) -> None:
        # The api migrating while the worker waits.
        sleeps.append(seconds)
        upgrade("head", database_url)

    monkeypatch.setattr(migrations.time, "sleep", migrate)

    wait_for_schema_head(sync_session_factory(engine), timeout=60, interval=5)

    assert sleeps == [5]


@pytest.mark.usefixtures("one_behind")
@pytest.mark.parametrize(
    ("command_name", "worker"),
    [("fetch-worker", fetch_worker), ("analyze-worker", analyze_worker)],
)
def test_a_worker_behind_the_schema_exits_non_zero_without_running(
    command_name: str,
    worker: ModuleType,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runs: list[object] = []
    monkeypatch.setattr(worker, "run", lambda *args: runs.append(args))
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("ALLOW_UNMOUNTED_CACHE", "1")
    monkeypatch.setenv("SCHEMA_WAIT_TIMEOUT", "0.2")
    monkeypatch.setenv("SCHEMA_WAIT_INTERVAL", "0.05")
    get_settings.cache_clear()
    try:
        result = runner.invoke(app, [command_name])
    finally:
        get_settings.cache_clear()

    assert result.exit_code == 1
    assert "not at the image's head" in result.output
    assert runs == []
