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

    # Attempts per archive call within one fetch Job, retrying HTTP 429/5xx and connection
    # errors with exponential backoff (spec §5.1). Re-claiming a failed Job is the `jobs`
    # table's own max_attempts, not this.
    fetch_max_attempts: int = Field(default=3, ge=1)

    # The fetch worker (spec §3): how many fetch Jobs it claims at once, so Gaia's epoch
    # photometry goes out as one batched request for all of them, and how long it sleeps,
    # in seconds, when the queue is empty. A batch is recorded only once all of it has been
    # fetched, so it has to finish well inside the 30-minute stale-claim window: TESS, at two
    # MAST downloads at a time, sets the pace.
    fetch_batch_size: int = Field(default=10, ge=1)
    fetch_poll_interval: float = Field(default=60.0, gt=0)
    # The analyze worker's sleep, in seconds, when no analyze Job waits.
    analyze_poll_interval: float = Field(default=60.0, gt=0)
    # Lets the fetch and analyze workers run on a CACHE_DIR that is not a mount point. Dev
    # only: in prod an unmounted USB SSD would leave the Cache filling the boot disk, or the
    # analyzer failing every Target on an empty one (spec §10).
    allow_unmounted_cache: bool = False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide Settings, read once on first use."""
    return Settings()
