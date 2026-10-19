"""Command-line entry point. Every Compose service runs `python -m argelander <command>`."""

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
