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


@app.command("fetch-worker")
def fetch_worker() -> None:
    """Claim fetch Jobs, fetch every survey, index the Cache and queue analysis, until stopped.

    Refuses to start when CACHE_DIR is not a mount point, unless ALLOW_UNMOUNTED_CACHE=1.
    """
    # Imported here so `version` and --help never load the fetchers and SQLAlchemy.
    from argelander.config import get_settings
    from argelander.db.session import sync_session_factory
    from argelander.fetch import worker
    from argelander.logs import configure_logging

    configure_logging()
    try:
        worker.main(get_settings(), sync_session_factory())
    except worker.CacheNotMounted as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error


@app.command("analyze-worker")
def analyze_worker() -> None:
    """Claim analyze Jobs, analyse each Target's Light Curves and write its Analysis, until
    stopped.

    Refuses to start when CACHE_DIR is not a mount point, unless ALLOW_UNMOUNTED_CACHE=1.
    """
    # Imported here so `version` and --help never load the analysis chain and SQLAlchemy.
    from argelander.analysis import worker
    from argelander.config import get_settings
    from argelander.db.session import sync_session_factory
    from argelander.fetch.worker import CacheNotMounted
    from argelander.logs import configure_logging

    configure_logging()
    try:
        worker.main(get_settings(), sync_session_factory())
    except CacheNotMounted as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error


@app.command()
def classify(
    replot: Annotated[
        bool,
        typer.Option(
            "--replot",
            help="Redraw the plots of every Analysis at this Pipeline Version from its "
            "Proposal, without analysing or classifying again.",
        ),
    ] = False,
    target: Annotated[str | None, typer.Option(help="Only this Target, by VSX name.")] = None,
) -> None:
    """Redraw the evidence plots of stored Analyses (with --replot).

    analyze-worker classifies each Target as it analyses it, so --replot is the one mode.
    """
    if not replot:
        raise typer.BadParameter(
            "analyze-worker proposes as it analyses; classify only redraws, with --replot"
        )
    # Imported here so `version` and --help never load the analysis chain and SQLAlchemy.
    from argelander.classify.replot import UnknownTarget, replot_all
    from argelander.config import get_settings
    from argelander.db.session import sync_session_factory

    settings = get_settings()
    try:
        count = replot_all(
            sync_session_factory(), settings.cache_dir, settings.plots_dir, vsx_name=target
        )
    except UnknownTarget as error:
        raise typer.BadParameter(str(error), param_hint="--target") from error
    typer.echo(f"redrew the plots of {count} analyses")


@app.command()
def enqueue(
    kind: Annotated[str, typer.Argument(help="The Job kind: fetch or analyze.")],
    target: Annotated[str | None, typer.Option(help="Only this Target, by VSX name.")] = None,
    slice_name: Annotated[
        str | None,
        typer.Option(
            "--slice", help="Every Target this Slice selected first, by the Slice's name."
        ),
    ] = None,
    failed: Annotated[
        bool,
        typer.Option(
            "--failed",
            help="Only Targets whose newest Job of this kind failed for good; alone, across "
            "every Slice.",
        ),
    ] = False,
) -> None:
    """Queue a fetch or analyze Job for each Target named, and set its status to match.

    A Target that already has a Job of either kind pending or claimed is skipped.
    """
    # Imported here so `version` and --help never load SQLAlchemy.
    from argelander import status
    from argelander.db.models import JobKind
    from argelander.db.session import sync_session_factory

    try:
        job_kind = JobKind(kind)
    except ValueError as error:
        choices = ", ".join(JobKind)
        raise typer.BadParameter(f"{kind!r} is not one of {choices}", param_hint="KIND") from error
    if target is not None and slice_name is not None:
        raise typer.BadParameter("give --target or --slice, not both")
    if target is None and slice_name is None and not failed:
        raise typer.BadParameter("give --target, --slice or --failed")

    with sync_session_factory().begin() as session:
        try:
            ids = status.target_ids(
                session, job_kind, vsx_name=target, slice_name=slice_name, failed=failed
            )
        except status.UnknownTarget as error:
            raise typer.BadParameter(str(error), param_hint="--target") from error
        except status.UnknownSlice as error:
            raise typer.BadParameter(str(error), param_hint="--slice") from error
        queued = status.requeue(session, job_kind, ids)
    typer.echo(
        f"queued {queued} {job_kind} Jobs for {len(ids)} Targets "
        f"({len(ids) - queued} already had a Job pending or claimed)"
    )


@app.command("status")
def status_(
    slice_name: Annotated[
        str | None,
        typer.Option(
            "--slice", help="Only the Targets this Slice selected first, by the Slice's name."
        ),
    ] = None,
) -> None:
    """Count the Targets at each status and the Jobs of each kind at each status."""
    # Imported here so `version` and --help never load SQLAlchemy.
    from argelander import status
    from argelander.db.session import sync_session_factory

    with sync_session_factory()() as session:
        try:
            counts = status.counts(session, slice_name)
        except status.UnknownSlice as error:
            raise typer.BadParameter(str(error), param_hint="--slice") from error
    typer.echo("\n".join(counts.lines()))


@app.command("recompute-status")
def recompute_status() -> None:
    """Rederive every Target's status from its Jobs and Proposals; print each change.

    A Proposal's Review and Submission count too (spec §4).
    """
    # Imported here so `version` and --help never load SQLAlchemy.
    from argelander import status
    from argelander.db.session import sync_session_factory

    with sync_session_factory().begin() as session:
        changes = status.recompute(session)
    for change in changes:
        typer.echo(f"{change.vsx_name}: {change.was} -> {change.now}")
    typer.echo(f"changed {len(changes)} Targets")


@app.command("db-upgrade")
def db_upgrade() -> None:
    """Apply database migrations up to the newest revision."""
    # Imported here so `version` and --help never load SQLAlchemy and Alembic.
    from argelander.db.migrations import upgrade

    upgrade()
