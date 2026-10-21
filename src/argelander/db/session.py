"""Engines and session factories: sync for the workers, async for the review app.

Both run on psycopg 3 from the one `DATABASE_URL`: SQLAlchemy's `postgresql+psycopg`
dialect serves sync and asyncio alike. A process builds its factory once at startup (a
worker's main loop, FastAPI's lifespan) and keeps it; nothing here is a module global.
"""

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine as _create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from argelander.config import get_settings


def create_sync_engine(url: str | None = None) -> Engine:
    """An engine on `url`, or on Settings' DATABASE_URL when none is given."""
    # pre_ping: the workers hold connections across long idle polls and Postgres restarts.
    return create_engine(url or get_settings().database_url, pool_pre_ping=True)


def create_async_engine(url: str | None = None) -> AsyncEngine:
    """An asyncio engine on `url`, or on Settings' DATABASE_URL when none is given."""
    return _create_async_engine(url or get_settings().database_url, pool_pre_ping=True)


def sync_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    return sessionmaker(engine or create_sync_engine(), expire_on_commit=False)


def async_session_factory(engine: AsyncEngine | None = None) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: an expired attribute would lazy-load, which asyncio forbids.
    return async_sessionmaker(engine or create_async_engine(), expire_on_commit=False)
