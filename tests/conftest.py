import os

import pytest


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Fail any `live` test that runs in CI, where `-m "not live"` should have dropped it.

    GitHub Actions sets `CI=true`. If the marker filter ever stops excluding `live` tests, the
    run fails loudly instead of letting real archive calls slip onto GitHub's runners.
    """
    if item.get_closest_marker("live") and os.environ.get("CI") == "true":
        pytest.fail("a `live` test ran in CI; check the -m filter", pytrace=False)
