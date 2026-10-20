"""The shared fetch machinery: per-archive semaphores and retry with backoff (spec §5.1).

Every test drives a fake fetcher through `asyncio.run`; nothing touches an archive.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from argelander.config import Settings
from argelander.fetch.base import (
    ArchiveLimits,
    Fetcher,
    FetchError,
    TargetRef,
    with_retries,
)
from argelander.lightcurve import LightCurve

TARGET = TargetRef(gaia_source_id=1, ra=270.0, dec=60.0, vsx_name="V0001 Dra")


class FakeHTTPError(Exception):
    """Shaped like `requests.HTTPError`: the status lives on `.response.status_code`."""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.response = SimpleNamespace(status_code=status)


class FakeDALServiceError(Exception):
    """Shaped like pyvo's `DALServiceError` and urllib's `HTTPError`: the status is `.code`."""

    def __init__(self, code: int) -> None:
        super().__init__(f"HTTP {code}")
        self.code = code


class RequestException(OSError):
    """Stands in for requests' base error, which subclasses OSError, not ConnectionError."""


class RequestsConnectionError(RequestException):
    pass


class RequestsTimeout(RequestException):
    pass


class RequestsReadTimeout(RequestsTimeout):
    pass


RequestException.__module__ = "requests.exceptions"
RequestsConnectionError.__module__ = "requests.exceptions"
RequestsConnectionError.__qualname__ = "ConnectionError"
RequestsTimeout.__module__ = "requests.exceptions"
RequestsTimeout.__qualname__ = "Timeout"
RequestsReadTimeout.__module__ = "requests.exceptions"
RequestsReadTimeout.__qualname__ = "ReadTimeout"


@dataclass
class SleepyFetcher:
    """Holds an IRSA slot for `seconds` in a worker thread, recording when it held it."""

    limits: ArchiveLimits
    seconds: float
    survey: str = "ztf"
    spans: list[tuple[float, float]] = field(default_factory=list)

    def _blocking_query(self) -> None:
        start = time.monotonic()
        time.sleep(self.seconds)
        self.spans.append((start, time.monotonic()))

    async def fetch(self, target: TargetRef) -> list[LightCurve]:
        await self.limits.run("irsa", self._blocking_query)
        return []


@dataclass
class FlakyFetcher:
    """Fails with `error` for the first `failures` calls, then returns no Light Curves."""

    failures: int
    error: Exception
    survey: str = "ztf"
    calls: int = 0

    async def fetch(self, target: TargetRef) -> list[LightCurve]:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return []


def _settings(**overrides: object) -> Settings:
    return Settings.model_validate({"database_url": "postgresql+psycopg://unused", **overrides})


def test_fakes_satisfy_the_fetcher_protocol() -> None:
    fetchers: list[Fetcher] = [
        SleepyFetcher(ArchiveLimits({"irsa": 1}), 0.0),
        FlakyFetcher(0, ConnectionError()),
    ]

    assert [f.survey for f in fetchers] == ["ztf", "ztf"]


def test_limit_of_one_runs_concurrent_fetches_sequentially() -> None:
    fetcher = SleepyFetcher(ArchiveLimits({"irsa": 1}), seconds=0.1)

    async def both() -> None:
        await asyncio.gather(fetcher.fetch(TARGET), fetcher.fetch(TARGET))

    asyncio.run(both())

    (_, first_end), (second_start, _) = sorted(fetcher.spans)
    assert second_start >= first_end


def test_limit_of_two_runs_concurrent_fetches_together() -> None:
    fetcher = SleepyFetcher(ArchiveLimits({"irsa": 2}), seconds=0.1)

    async def both() -> None:
        await asyncio.gather(fetcher.fetch(TARGET), fetcher.fetch(TARGET))

    asyncio.run(both())

    (_, first_end), (second_start, _) = sorted(fetcher.spans)
    assert second_start < first_end


def test_limits_come_from_settings() -> None:
    limits = ArchiveLimits.from_settings(_settings(gaia_concurrency=3))

    assert limits.limit("irsa") == 2
    assert limits.limit("mast") == 2
    assert limits.limit("gaia") == 3
    assert limits.limit("asassn") == 2


def test_unknown_archive_is_rejected() -> None:
    limits = ArchiveLimits({"irsa": 1})

    with pytest.raises(ValueError, match="no concurrency limit for archive 'vizier'"):
        limits.limit("vizier")


def test_two_failures_then_success_returns_the_result_after_two_retries(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fetcher = FlakyFetcher(failures=2, error=FakeHTTPError(503))

    with caplog.at_level(logging.WARNING, logger="argelander.fetch.base"):
        result = asyncio.run(
            with_retries(
                lambda: fetcher.fetch(TARGET), survey="ztf", max_attempts=3, base_delay=0.0
            )
        )

    assert result == []
    assert fetcher.calls == 3
    assert [vars(r)["attempt"] for r in caplog.records] == [1, 2]


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (FakeHTTPError(429), 429),
        (FakeHTTPError(502), 502),
        (FakeDALServiceError(503), 503),
        (ConnectionError("reset"), None),
        (RequestsConnectionError("refused"), None),
        (RequestsReadTimeout("read timed out"), None),
    ],
)
def test_exhausting_max_attempts_raises_fetch_error(error: Exception, status: int | None) -> None:
    fetcher = FlakyFetcher(failures=3, error=error)

    with pytest.raises(FetchError) as raised:
        asyncio.run(
            with_retries(
                lambda: fetcher.fetch(TARGET), survey="ztf", max_attempts=3, base_delay=0.0
            )
        )

    assert fetcher.calls == 3
    assert raised.value.survey == "ztf"
    assert raised.value.attempts == 3
    assert raised.value.status == status
    assert raised.value.__cause__ is error


@pytest.mark.parametrize(
    "error",
    [
        FakeHTTPError(404),
        FakeDALServiceError(400),
        RequestException("invalid URL"),
        FileNotFoundError("cache"),
        ValueError("bad column"),
    ],
)
def test_non_transient_errors_are_not_retried(error: Exception) -> None:
    fetcher = FlakyFetcher(failures=1, error=error)

    with pytest.raises(type(error)):
        asyncio.run(
            with_retries(
                lambda: fetcher.fetch(TARGET), survey="ztf", max_attempts=3, base_delay=0.0
            )
        )

    assert fetcher.calls == 1


def test_backoff_grows_exponentially_with_jitter_under_a_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delays: list[float] = []

    async def record_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr("argelander.fetch.base.random.uniform", lambda low, high: high)
    monkeypatch.setattr("argelander.fetch.base.asyncio.sleep", record_sleep)
    fetcher = FlakyFetcher(failures=4, error=TimeoutError())

    asyncio.run(
        with_retries(
            lambda: fetcher.fetch(TARGET),
            survey="ztf",
            max_attempts=5,
            base_delay=1.0,
            max_delay=5.0,
        )
    )

    assert delays == [1.0, 2.0, 4.0, 5.0]
