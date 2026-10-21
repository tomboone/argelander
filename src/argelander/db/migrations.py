"""Alembic, driven from code: `argelander db-upgrade` and the migration tests.

The `alembic` command line reads the same script location from `[tool.alembic]` in
pyproject.toml; this module does not depend on that file, so the prod image can migrate
from any working directory.
"""

from alembic import command
from alembic.config import Config

SCRIPT_LOCATION = "argelander:alembic"


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
