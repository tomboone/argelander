"""Placeholder `live` test: proves CI's `-m "not live"` deselects archive-hitting tests.

GitHub Actions sets `CI=true`. If the marker filter ever stops excluding `live` tests, this
fails the CI run loudly instead of letting real archive calls slip onto GitHub's runners.
Delete it once the first real `live` test (a fetcher against ZTF, MAST, Gaia or ASAS-SN)
takes over that job.
"""

import os

import pytest


@pytest.mark.live
def test_live_tests_never_run_in_ci() -> None:
    assert os.environ.get("CI") != "true", "a `live` test ran in CI; check the -m filter"
