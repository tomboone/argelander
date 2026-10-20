"""What every fetcher shares: the Fetcher protocol, per-archive semaphores, retry with backoff.

Spec §5.1. One fetcher process serves every survey, so politeness is local: each archive gets
its own `asyncio.Semaphore` (a shared limiter would throttle the fast archives to the slowest),
and each archive call is retried on HTTP 429/5xx and connection errors with exponential backoff
and full jitter. The archive clients (astroquery, lightkurve) block, so `ArchiveLimits.run`
hands them to a worker thread while holding the archive's slot.

A concrete fetcher composes the two, retrying outside the semaphore so a backoff sleep never
holds a slot another Target could use:

    rows = await with_retries(
        lambda: limits.run("irsa", query_irsa, target.ra, target.dec),
        survey="ztf",
        max_attempts=settings.fetch_max_attempts,
    )
"""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, Self

from argelander.config import Settings
from argelander.lightcurve import LightCurve

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TessField:
    """The Gaia sources inside TESS's ~120″ photometric aperture around a Target, other than
    the Target itself: the inputs to the `tess_contaminated` check (spec §5.1, §6 step 7)."""

    n_sources: int
    brightest_g: float | None  # None when n_sources is 0


@dataclass(frozen=True)
class TargetRef:
    """What a fetcher needs to find one Target in an archive."""

    gaia_source_id: int
    ra: float  # ICRS degrees
    dec: float  # ICRS degrees
    vsx_name: str
    # From the Target's GaiaRecord once the catalog join has run; None until then.
    tess_field: TessField | None = None


class Fetcher(Protocol):
    survey: str  # "ztf" | "tess" | "gaia" | "asassn", as in `LightCurve.survey`

    async def fetch(self, target: TargetRef) -> list[LightCurve]:
        """Every Light Curve the survey holds for `target`, one per band, converted at the
        boundary to BJD_TDB; an empty list when the survey has no data for it."""
        ...


class FetchError(Exception):
    """An archive call still failed with a transient error after `attempts` tries."""

    def __init__(self, survey: str, attempts: int, status: int | None) -> None:
        reason = f"HTTP {status}" if status is not None else "connection error"
        super().__init__(f"{survey}: gave up after {attempts} attempts; last failure: {reason}")
        self.survey = survey
        self.attempts = attempts
        self.status = status


class ArchiveLimits:
    """One semaphore per archive, bounding how many calls to it run at once.

    Build it inside the event loop that uses it, once per process (the fetch worker does).
    """

    def __init__(self, limits: Mapping[str, int]) -> None:
        self._limits = dict(limits)
        self._semaphores = {archive: asyncio.Semaphore(n) for archive, n in limits.items()}

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(
            {
                "irsa": settings.irsa_concurrency,
                "mast": settings.mast_concurrency,
                "gaia": settings.gaia_concurrency,
                "asassn": settings.asassn_concurrency,
            }
        )

    def limit(self, archive: str) -> int:
        """The configured concurrency for `archive`."""
        self._check(archive)
        return self._limits[archive]

    async def run[**P, T](
        self, archive: str, func: Callable[P, T], *args: P.args, **kwargs: P.kwargs
    ) -> T:
        """Run the blocking `func(*args, **kwargs)` in a worker thread under `archive`'s limit.

        Cancelling the caller frees the slot but cannot stop the thread, whose request runs on.
        Do not wrap `run` in a timeout; set the archive client's own timeout instead.
        """
        self._check(archive)
        async with self._semaphores[archive]:
            return await asyncio.to_thread(func, *args, **kwargs)

    def _check(self, archive: str) -> None:
        if archive not in self._limits:
            raise ValueError(f"no concurrency limit for archive {archive!r}")


# requests' connection failures subclass OSError, not the builtin ConnectionError or
# TimeoutError. Matched by qualified name so this module does not import requests, which
# arrives with the archive clients (astroquery, lightkurve, pyvo all raise it).
_TRANSIENT_CLIENT_ERRORS = frozenset(
    {"requests.exceptions.ConnectionError", "requests.exceptions.Timeout"}
)


def http_status(error: BaseException) -> int | None:
    """The HTTP status an archive client attached to `error`, if any.

    requests hangs it on `error.response.status_code`; urllib's `HTTPError` and pyvo's
    `DALServiceError` on `error.code`. Anything else has none.
    """
    status = getattr(getattr(error, "response", None), "status_code", None)
    if isinstance(status, int):
        return status
    code = getattr(error, "code", None)
    return code if isinstance(code, int) and 100 <= code <= 599 else None


def is_transient(error: BaseException) -> bool:
    """Whether retrying could help: HTTP 429 or 5xx, or a connection error or timeout."""
    status = http_status(error)
    if status is not None:
        return status == 429 or 500 <= status <= 599
    if isinstance(error, (ConnectionError, TimeoutError)):
        return True
    return any(
        f"{cls.__module__}.{cls.__qualname__}" in _TRANSIENT_CLIENT_ERRORS
        for cls in type(error).__mro__
    )


async def with_retries[T](
    call: Callable[[], Awaitable[T]],
    *,
    survey: str,
    max_attempts: int,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
) -> T:
    """Await `call()`, retrying transient failures up to `max_attempts` calls in total.

    This budget is per archive call, inside one fetch Job. The Job's own `max_attempts` (the
    `jobs` table) is the outer budget: how often the whole Target is claimed again.

    Before retry n (counting from 1) it sleeps a uniform random time up to
    `min(max_delay, base_delay * 2 ** (n - 1))` seconds. Non-transient errors propagate
    unchanged on the first failure; a transient one on the last attempt becomes `FetchError`,
    chained to it.
    """
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be at least 1, got {max_attempts}")
    for attempt in range(1, max_attempts + 1):
        try:
            return await call()
        except Exception as error:
            if not is_transient(error):
                raise
            status = http_status(error)
            if attempt == max_attempts:
                raise FetchError(survey, attempt, status) from error
            delay = random.uniform(0.0, min(max_delay, base_delay * 2 ** (attempt - 1)))
            logger.warning(
                "%s: attempt %d of %d failed (%r); retrying in %.1f s",
                survey,
                attempt,
                max_attempts,
                error,
                delay,
                extra={"survey": survey, "attempt": attempt, "status": status},
            )
            await asyncio.sleep(delay)
    raise AssertionError("unreachable: the loop returns or raises")
