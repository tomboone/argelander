"""Smoke tests: the package imports, the CLI runs, and Settings reads its environment."""

import re
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from argelander import PIPELINE_VERSION
from argelander.cli import app
from argelander.config import Settings

runner = CliRunner()


def test_pipeline_version_is_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", PIPELINE_VERSION)


def test_cli_version_prints_pipeline_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == PIPELINE_VERSION


def test_python_dash_m_runs_the_cli() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "argelander", "version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert completed.stdout.strip() == PIPELINE_VERSION


def test_settings_reads_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db:5432/argelander")
    monkeypatch.setenv("CACHE_DIR", "/mnt/ssd/cache")
    monkeypatch.setenv("PLOTS_DIR", "/mnt/ssd/plots")
    monkeypatch.setenv("ASTRO_CACHE_DIR", "/mnt/ssd/astropy")
    monkeypatch.setenv("GAIA_CONCURRENCY", "3")

    settings = Settings(_env_file=None)

    assert settings.database_url == "postgresql+psycopg://u:p@db:5432/argelander"
    assert settings.cache_dir == Path("/mnt/ssd/cache")
    assert settings.plots_dir == Path("/mnt/ssd/plots")
    assert settings.astro_cache_dir == Path("/mnt/ssd/astropy")
    assert settings.gaia_concurrency == 3


def test_settings_archive_concurrency_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db:5432/argelander")
    for name in ("IRSA_CONCURRENCY", "MAST_CONCURRENCY", "GAIA_CONCURRENCY", "ASASSN_CONCURRENCY"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.irsa_concurrency == 2
    assert settings.mast_concurrency == 2
    assert settings.gaia_concurrency == 1
    assert settings.asassn_concurrency == 2


def test_settings_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValueError, match="database_url"):
        Settings(_env_file=None)
