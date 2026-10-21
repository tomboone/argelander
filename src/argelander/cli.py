"""Command-line entry point. Every Compose service runs `python -m argelander <command>`."""

from typing import Annotated

import typer

from argelander import PIPELINE_VERSION

app = typer.Typer(
    name="argelander",
    help="Argelander: proposes evidence-backed corrections to the AAVSO VSX catalog.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Argelander: proposes evidence-backed corrections to the AAVSO VSX catalog.

    The callback keeps Typer in group mode while there is only one command, so
    `python -m argelander version` works now and later commands slot in beside it.
    """


@app.command()
def version() -> None:
    """Print the pipeline version."""
    typer.echo(PIPELINE_VERSION)


@app.command("select-slice")
def select_slice(
    constellation: Annotated[str, typer.Argument(help="IAU abbreviation, e.g. Dra.")],
    mag_min: Annotated[float, typer.Argument(help="Brightest magnitude at maximum.")],
    mag_max: Annotated[float, typer.Argument(help="Faintest magnitude at maximum.")],
    name: Annotated[
        str | None, typer.Option(help="The Slice's name; default <constellation>-<min>-<max>.")
    ] = None,
) -> None:
    """Select a Slice of Targets from VSX and Gaia DR3 and queue a fetch Job for each."""
    # Imported here so `version` and --help never load astropy, astroquery or SQLAlchemy.
    import asyncio

    from argelander import selector
    from argelander.catalog.constellation import bounding_box
    from argelander.config import get_settings
    from argelander.db.session import sync_session_factory

    if mag_min > mag_max:
        raise typer.BadParameter(f"mag_min {mag_min} is fainter than mag_max {mag_max}")
    try:
        bounding_box(constellation)
    except ValueError as error:
        raise typer.BadParameter(str(error), param_hint="CONSTELLATION") from error

    entries, records = asyncio.run(
        selector.gather(
            constellation, mag_min, mag_max, max_attempts=get_settings().fetch_max_attempts
        )
    )
    with sync_session_factory().begin() as session:
        summary = selector.store(
            session,
            name=name or selector.default_name(constellation, mag_min, mag_max),
            constellation=constellation,
            mag_min=mag_min,
            mag_max=mag_max,
            entries=entries,
            records=records,
        )
    typer.echo("\n".join(summary.lines()))


@app.command("db-upgrade")
def db_upgrade() -> None:
    """Apply database migrations up to the newest revision."""
    # Imported here so `version` and --help never load SQLAlchemy and Alembic.
    from argelander.db.migrations import upgrade

    upgrade()
