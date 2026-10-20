import os
from pathlib import Path

import pytest
from astropy.table import Table

from argelander.catalog.gaia import GaiaRecord, records_from_tables

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
