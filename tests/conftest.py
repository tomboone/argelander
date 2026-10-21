import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from astropy.table import Table
from sqlalchemy import Engine, create_engine, make_url, text

from argelander.catalog.gaia import GaiaRecord, records_from_tables
from argelander.db.migrations import upgrade
from argelander.db.session import create_sync_engine

GAIA_FIXTURES = Path(__file__).parent / "fixtures" / "gaia"


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Fail any `live` test that runs in CI, where `-m "not live"` should have dropped it.

    GitHub Actions sets `CI=true`. If the marker filter ever stops excluding `live` tests, the
    run fails loudly instead of letting real archive calls slip onto GitHub's runners.
    """
    if item.get_closest_marker("live") and os.environ.get("CI") == "true":
        pytest.fail("a `live` test ran in CI; check the -m filter", pytrace=False)


@pytest.fixture(scope="session")
def recorded_gaia() -> dict[int, GaiaRecord]:
    """The `GaiaRecord`s of the six Draco sources recorded in `fixtures/gaia` (see
    `test_catalog_gaia.py` for who they are)."""
    names = ("catalog", "catalog_cross_ids", "catalog_neighbours", "catalog_tess_field")
    return records_from_tables(
        *(Table.read(GAIA_FIXTURES / f"{name}.xml", format="votable") for name in names)
    )


# The database tests migrate a throwaway database beside the configured one (`<name>_test`),
# so the dev database is never touched. They need DATABASE_URL: the dev stack's app container
# has it, and CI runs a Postgres service for them. Outside CI they skip without it. Each test
# module gets its own fresh database.


def _configured_url() -> str:
    try:
        return os.environ["DATABASE_URL"]
    except KeyError:
        if os.environ.get("CI") == "true":
            pytest.fail("DATABASE_URL is unset in CI; the job's Postgres service sets it")
        pytest.skip("needs DATABASE_URL (run inside the dev stack)")


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    """A freshly created, empty database on the configured server; dropped afterwards."""
    configured = make_url(_configured_url())
    name = f"{configured.database}_test"
    admin = create_engine(configured, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield configured.set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture(scope="module")
def engine(database_url: str) -> Iterator[Engine]:
    upgrade("head", database_url)
    engine = create_sync_engine(database_url)
    yield engine
    engine.dispose()
