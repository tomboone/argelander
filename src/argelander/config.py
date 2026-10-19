"""Runtime settings, read from the environment (and `.env` in local dev).

This module owns the environment variable names. Nothing else in the package reads
`os.environ` directly; services and Compose files set the variables listed here.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # SQLAlchemy URL for the results database and job queue; psycopg 3 driver.
    database_url: str

    # The Parquet light-curve Cache, the evidence plots, and the astropy/astroquery download
    # directory (not the Cache in the glossary sense). Prod mounts all three from the USB SSD;
    # the fetcher refuses to run if CACHE_DIR is not a mount point (spec §10), so the defaults
    # are only for local dev.
    cache_dir: Path = Path("data/cache")
    plots_dir: Path = Path("data/plots")
    astro_cache_dir: Path = Path("data/astropy")

    # Per-archive concurrency limits for the fetcher's local semaphores (spec §5.1).
    irsa_concurrency: int = Field(default=2, ge=1)
    mast_concurrency: int = Field(default=2, ge=1)
    gaia_concurrency: int = Field(default=1, ge=1)
    asassn_concurrency: int = Field(default=2, ge=1)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide Settings, read once on first use."""
    return Settings()
