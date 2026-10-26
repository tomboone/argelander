"""Alembic, driven from code: `argelander db-upgrade`, the workers' schema wait and the
migration tests.

The `alembic` command line reads the same script location from `[tool.alembic]` in
pyproject.toml; this module does not depend on that file, so the prod image can migrate
from any working directory.
"""

import logging
import time

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

SCRIPT_LOCATION = "argelander:alembic"

logger = logging.getLogger(__name__)


class SchemaNotAtHead(Exception):
    """The database did not reach the image's head revision within the schema wait."""


def alembic_config(database_url: str | None = None) -> Config:
    """Alembic's Config for this package; `database_url` overrides Settings' DATABASE_URL.

    The URL travels in `Config.attributes`, not as `sqlalchemy.url`, because Alembic
    interpolates `%` in main options and a password may contain one.
    """
    config = Config()
    config.set_main_option("script_location", SCRIPT_LOCATION)
    if database_url is not None:
        config.attributes["database_url"] = database_url
    return config


def upgrade(revision: str = "head", database_url: str | None = None) -> None:
    command.upgrade(alembic_config(database_url), revision)


def current_revisions(sessions: sessionmaker[Session]) -> set[str]:
    """The revision(s) the database is at; none before its first migration."""
    with sessions() as session:
        return set(MigrationContext.configure(session.connection()).get_current_heads())


def wait_for_schema_head(
    sessions: sessionmaker[Session], *, timeout: float, interval: float
) -> None:
    """Return once the database is at this image's head revision; `SchemaNotAtHead` if it is
    not there within `timeout` seconds, checking every `interval`.

    Under Watchtower only the api migrates on start. Watchtower orders restarts by `depends_on`
    but does not wait for health. The workers depend only on postgres, which Watchtower does not
    watch, so a worker on a new image can come up before the api has migrated. The worker waits
    rather than write against the old schema; exiting when the wait runs out lets
    `restart: unless-stopped` try again.
    """
    script = ScriptDirectory.from_config(alembic_config())
    head = _describe(set(script.get_heads()))
    known = {revision.revision for revision in script.walk_revisions()}
    deadline = time.monotonic() + timeout
    while True:
        unknown: set[str] = set()
        try:
            current = current_revisions(sessions)
        except OperationalError as error:
            # Postgres restarting under the worker, say: not at head, as far as it can tell.
            reason = str(error.orig).partition("\n")[0]
            seen = f"unreachable ({reason})"
        else:
            seen = _describe(current)
            if seen == head:
                return
            # A revision this image has no migration for: an older image on a newer database,
            # which db-upgrade from this image cannot fix.
            unknown = current - known
        if time.monotonic() >= deadline:
            advice = (
                f"{_describe(unknown)} is newer than this image's migrations, so deploy the "
                "image that carries it"
                if unknown
                else "run `argelander db-upgrade`"
            )
            raise SchemaNotAtHead(
                f"the database is at {seen}, not at the image's head {head}, after "
                f"{timeout:g} s; {advice}"
            )
        logger.info(
            "waiting for the database to reach the schema head",
            extra={"database_revision": seen, "head_revision": head},
        )
        time.sleep(interval)


def _describe(revisions: set[str]) -> str:
    return ", ".join(sorted(revisions)) or "no revision"
