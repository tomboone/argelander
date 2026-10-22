"""The review app (spec §9): FastAPI, server-rendered Jinja2 templates, htmx, no build step.

`create_app` builds the app; `argelander web` serves it with uvicorn. It reads Postgres and
the plot directory only (spec §12). The static files are vendored: htmx 2.0.11 from
unpkg.com/htmx.org@2.0.11/dist/htmx.min.js, and one stylesheet.
"""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from argelander.config import Settings, get_settings
from argelander.db.session import async_session_factory, create_async_engine
from argelander.web.queue import QueueFilters, proposed_types, queue_page, status_counts

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=WEB_DIR / "templates")

# Inside the Docker healthcheck's 10 s and Uptime Kuma's own timeout, so a hung Postgres
# answers 503 instead of leaving the check to time out.
HEALTHZ_TIMEOUT = 5.0


async def _ping(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions() as session:
        await session.execute(text("SELECT 1"))


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One AsyncSession per request, from the factory the lifespan built."""
    async with request.app.state.sessions() as session:
        yield session


Session = Annotated[AsyncSession, Depends(get_session)]


def _forget(pings: set[asyncio.Future[None]]) -> Callable[[asyncio.Future[None]], None]:
    """A done-callback that drops a finished ping and retrieves its outcome, so an abandoned
    ping that later fails is not reported as an exception nobody retrieved."""

    def done(ping: asyncio.Future[None]) -> None:
        pings.discard(ping)
        if not ping.cancelled():
            ping.exception()

    return done


def create_app(settings: Settings | None = None) -> FastAPI:
    """The review app on `settings`, or on the process-wide Settings when none are given."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        engine = create_async_engine(settings.database_url)
        app.state.sessions = async_session_factory(engine)
        app.state.pings = set()
        yield
        await engine.dispose()

    # No OpenAPI schema or docs pages: the app serves HTML to one reader, not an API.
    app = FastAPI(title="Argelander", lifespan=lifespan, openapi_url=None)
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    # StaticFiles fails every request when its directory is missing, which in dev it is until
    # the analyzer draws a plot. Prod binds the SSD's plots read-only, and Docker will not
    # start the api without them, so there the directory is always there already.
    # StaticFiles answers GET and HEAD only: the plots are read-only to the app.
    settings.plots_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/plots", StaticFiles(directory=settings.plots_dir), name="plots")

    @app.middleware("http")
    async def log_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # uvicorn's own access log is off (cli.web), so this is the one line per request. A
        # route that raises is logged as the 500 the client gets; uvicorn logs the traceback.
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            logger.info(
                "%s %s %d",
                request.method,
                request.url.path,
                status,
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": status,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                },
            )

    @app.get("/", response_class=HTMLResponse)
    async def index(
        request: Request, session: Session, filters: Annotated[QueueFilters, Query()]
    ) -> HTMLResponse:
        """The review queue. An htmx request from the filters or the pager gets the queue
        region alone; a history restore, which htmx asks for when its cache misses, gets the
        whole page."""
        context: dict[str, object] = {
            "queue": await queue_page(session, filters),
            "filters": filters,
        }
        partial = (
            "HX-Request" in request.headers and "HX-History-Restore-Request" not in request.headers
        )
        if not partial:
            context |= {
                "counts": await status_counts(session),
                "types": await proposed_types(session),
            }
        name = "_queue.html" if partial else "index.html"
        # One URL answers with a fragment or a page, so a cache must key on the header.
        return templates.TemplateResponse(request, name, context, headers={"Vary": "HX-Request"})

    @app.get("/healthz")
    async def healthz(request: Request) -> JSONResponse:
        """200 when Postgres answers `SELECT 1`, 503 when it does not; Uptime Kuma watches it."""
        # The ping runs as a task the request stops waiting for at the timeout. Cancelling it
        # in place is not enough: on a pooled connection to a hung Postgres, psycopg's cancel
        # and cleanup outlast the timeout (15 s against a frozen proxy), and so would the
        # session's close. The abandoned task finishes or fails on its own; `pings` holds it
        # until then, so it is not garbage-collected mid-flight.
        ping = asyncio.ensure_future(_ping(request.app.state.sessions))
        request.app.state.pings.add(ping)
        ping.add_done_callback(_forget(request.app.state.pings))
        try:
            await asyncio.wait_for(asyncio.shield(ping), HEALTHZ_TIMEOUT)
        except (SQLAlchemyError, OSError, TimeoutError):
            ping.cancel()
            logger.warning("healthz: the database did not answer", exc_info=True)
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    return app
