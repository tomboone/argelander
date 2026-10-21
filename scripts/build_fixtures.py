"""Build the offline Light Curve fixtures from the archives (spec §11). Live: it calls IRSA,
MAST, the Gaia archive and Sky Patrol, so it is run by hand, never in CI.

For every star in `tests/fixtures/manifest.yaml` it fetches through the survey fetchers,
trims each Light Curve to at most `MAX_POINTS` points, writes it to
`tests/fixtures/lightcurves/{survey}/{gaia_source_id}/{band}.parquet` (the Cache layout), and
records in the star's `coverage` which bands each survey returned. A survey that has no
data for a star records `[]`, the coverage gap. A survey whose fetch fails keeps the
coverage it had, so ASAS-SN stays `pending` while Sky Patrol is down (NEU-1764).

`--catalog` records the Gaia catalog join for every manifest star instead: the four query
results as VOTables in `tests/fixtures/catalog/`, from which the prototype gate (NEU-1711)
rebuilds each star's `GaiaRecord` offline (Gaia's period, neighbours, colour, parallax).

`--measure` fetches without trimming into a temporary Cache, leaves the fixtures and the
manifest alone, and prints the Parquet bytes per star per survey and the TESS source each
star used, as a Markdown table: the measurement in spec §14.

Run in the app container, from the repo root:

    python scripts/build_fixtures.py                                  # all four surveys
    python scripts/build_fixtures.py --survey ztf --survey tess --survey gaia
    python scripts/build_fixtures.py --survey asassn --star "V0364 Dra"
    python scripts/build_fixtures.py --catalog
    python scripts/build_fixtures.py --measure --survey ztf --survey tess --survey gaia

Trimming draws a random subset rather than every n-th point: TESS and Gaia sample on a
regular cadence, and a regular stride would put a new Nyquist limit on the fixture that the
real data does not have. The generator is seeded with the Gaia source ID, so a rebuild from
the same archive response writes the same points.
"""

import argparse
import asyncio
import dataclasses
import shutil
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from argelander.catalog.gaia import (
    CATALOG_QUERY,
    CROSS_ID_QUERY,
    NEIGHBOUR_QUERY,
    TESS_FIELD_QUERY,
    fetch_catalog,
    run_upload_query,
)
from argelander.config import get_settings
from argelander.fetch.asassn import AsassnFetcher
from argelander.fetch.base import ArchiveLimits, Fetcher, TargetRef
from argelander.fetch.gaia_epoch import GaiaEpochFetcher
from argelander.fetch.tess import TessFetcher
from argelander.fetch.ztf import ZtfFetcher
from argelander.lightcurve import LightCurve, cache_path

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
MANIFEST = FIXTURES / "manifest.yaml"
LIGHT_CURVES = FIXTURES / "lightcurves"
CATALOG = FIXTURES / "catalog"
# The file each catalog query's result is recorded in, in `records_from_tables` order.
CATALOG_TABLES = {
    "catalog": CATALOG_QUERY,
    "cross_ids": CROSS_ID_QUERY,
    "neighbours": NEIGHBOUR_QUERY,
    "tess_field": TESS_FIELD_QUERY,
}

SURVEYS = ("ztf", "tess", "gaia", "asassn")
MAX_POINTS = 500
# Coverage of a survey not fetched yet, as opposed to `[]`, fetched and holding nothing.
PENDING = "pending"
# `meta` entries that hold one value per point and so are trimmed with the arrays.
_ALIGNED_META = ("sap",)


def trim(lc: LightCurve, max_points: int, seed: int) -> LightCurve:
    """`lc` with at most `max_points` of its points, drawn at random and kept in time order.

    The original count is recorded in `meta["fixture_trim"]`.
    """
    n = len(lc.time)
    if n <= max_points:
        return lc
    keep = np.sort(np.random.default_rng(seed).choice(n, size=max_points, replace=False))
    meta = dict(lc.meta)
    for key in _ALIGNED_META:
        if key in meta:
            meta[key] = [meta[key][i] for i in keep]
    meta["fixture_trim"] = {"points": n, "kept": max_points, "seed": seed}
    return dataclasses.replace(
        lc,
        time=lc.time[keep],
        value=lc.value[keep],
        error=lc.error[keep],
        quality=lc.quality[keep],
        meta=meta,
        oid=lc.oid[keep] if lc.oid is not None else None,
    )


def read_manifest(path: Path = MANIFEST) -> tuple[str, list[dict[str, Any]]]:
    """The manifest's leading comment block and its list of stars."""
    text = path.read_text()
    header = "".join(line for line in text.splitlines(keepends=True)[: _header_length(text)])
    return header, yaml.safe_load(text)


def write_manifest(header: str, stars: list[dict[str, Any]], path: Path = MANIFEST) -> None:
    """Write `stars` under `header`. Comments outside the header do not survive, so the
    header is where the manifest's fields are explained."""
    body = yaml.safe_dump(stars, sort_keys=False, allow_unicode=True, width=100)
    path.write_text(header + "\n" + body)


def _header_length(text: str) -> int:
    """How many lines the leading comment block runs to."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("#"):
            return i
    return len(lines)


def write_fixtures(survey: str, gaia_source_id: int, curves: Sequence[LightCurve]) -> list[str]:
    """Replace the star's fixtures for `survey` with trimmed `curves`; the bands written."""
    directory = LIGHT_CURVES / survey / str(gaia_source_id)
    shutil.rmtree(directory, ignore_errors=True)
    for lc in curves:
        trim(lc, MAX_POINTS, gaia_source_id).to_parquet(
            cache_path(LIGHT_CURVES, survey, gaia_source_id, lc.band)
        )
    return sorted(lc.band for lc in curves)


def record_catalog(stars: Sequence[dict[str, Any]]) -> None:
    """Run the catalog join's four queries for `stars` and write each result to `CATALOG`."""
    ids = [star["gaia_source_id"] for star in stars]
    CATALOG.mkdir(exist_ok=True)
    for name, adql in CATALOG_TABLES.items():
        table = run_upload_query(adql, ids)
        table.write(CATALOG / f"{name}.xml", format="votable", overwrite=True)
        print(f"{name}: {len(table)} rows", flush=True)


def measure(stars: Sequence[dict[str, Any]], surveys: Sequence[str]) -> str:
    """Fetch `stars` untrimmed into a temporary Cache; a Markdown table of the Parquet bytes
    each star takes per survey, and the TESS source it used."""
    sizes: dict[int, dict[str, int]] = {star["gaia_source_id"]: {} for star in stars}
    tess_source: dict[int, str] = {}
    with tempfile.TemporaryDirectory() as cache:

        def write(survey: str, gaia_source_id: int, curves: Sequence[LightCurve]) -> list[str]:
            for lc in curves:
                path = cache_path(Path(cache), survey, gaia_source_id, lc.band)
                lc.to_parquet(path)
                sizes[gaia_source_id][survey] = (
                    sizes[gaia_source_id].get(survey, 0) + path.stat().st_size
                )
                if survey == "tess":
                    tess_source[gaia_source_id] = lc.meta.get("source", "?")
            return sorted(lc.band for lc in curves)

        failures = asyncio.run(build([dict(s) for s in stars], surveys, write=write))
    for failure in failures:
        print(f"FAILED {failure}", file=sys.stderr)

    def kb(n: int | None) -> str:
        return "–" if n is None else f"{n / 1000:,.0f}"

    lines = [
        "| Star | G | " + " | ".join(f"{s} kB" for s in surveys) + " | Total kB | TESS source |",
        "| --- | ---: | " + " | ".join("---:" for _ in surveys) + " | ---: | --- |",
    ]
    for star in stars:
        row = sizes[star["gaia_source_id"]]
        lines.append(
            f"| {star['star']} | {star['gaia_g']:.2f} | "
            + " | ".join(kb(row.get(s)) for s in surveys)
            + f" | {kb(sum(row.values()))} | {tess_source.get(star['gaia_source_id'], '–')} |"
        )
    totals = [sum(row.values()) for row in sizes.values()]
    lines.append("")
    lines.append(
        f"Per star: median {kb(int(np.median(totals)))} kB, max {kb(max(totals))} kB, "
        f"mean {kb(int(np.mean(totals)))} kB over {len(totals)} stars."
    )
    return "\n".join(lines)


async def build(
    stars: list[dict[str, Any]],
    surveys: Sequence[str],
    *,
    on_progress: Callable[[], None] = lambda: None,
    write: Callable[[str, int, Sequence[LightCurve]], list[str]] = write_fixtures,
) -> list[str]:
    """Fetch and write every star's fixtures for `surveys`, updating each star's `coverage`
    in place; the failures, one line each. `write` stores one star's curves for one survey
    and returns the bands written; by default it writes trimmed fixtures.

    The surveys run at once, each under its own archive's semaphore, and each star is
    written as soon as its fetch returns, followed by a call to `on_progress`: a fetch of
    the whole set takes tens of minutes, and an interrupted run keeps what it finished.
    """
    settings = get_settings()
    limits = ArchiveLimits.from_settings(settings)
    attempts = settings.fetch_max_attempts
    targets = [
        TargetRef(
            gaia_source_id=star["gaia_source_id"],
            ra=star["ra"],
            dec=star["dec"],
            vsx_name=star["star"],
        )
        for star in stars
    ]
    if "tess" in surveys:
        # The TESS fetcher records the Gaia sources inside its aperture, as the fetch worker
        # will once select-slice has run the catalog join.
        records = await fetch_catalog([t.gaia_source_id for t in targets], max_attempts=attempts)
        targets = [
            dataclasses.replace(t, tess_field=records[t.gaia_source_id].tess_field)
            if t.gaia_source_id in records
            else t
            for t in targets
        ]
    fetchers: dict[str, Fetcher] = {
        "ztf": ZtfFetcher(limits, max_attempts=attempts),
        "tess": TessFetcher(limits, max_attempts=attempts),
        "asassn": AsassnFetcher(limits, max_attempts=attempts),
    }
    gaia = GaiaEpochFetcher(limits, max_attempts=attempts)
    failures: list[str] = []

    # sys.__stdout__, not sys.stdout: lightkurve's `suppress_stdout` swaps sys.stdout for a
    # devnull file it then closes, and two TESS fetches overlapping in threads can restore
    # each other's closed devnull, leaving sys.stdout closed for the rest of the run.
    def record(star: dict[str, Any], survey: str, result: list[LightCurve] | Exception) -> None:
        if isinstance(result, Exception):
            failures.append(f"{star['star']} {survey}: {result!r}")
            print(f"FAILED {failures[-1]}", file=sys.__stderr__, flush=True)
            return
        star["coverage"][survey] = write(survey, star["gaia_source_id"], result)
        coverage = star["coverage"][survey] or "no data"
        print(f"{star['star']} {survey}: {coverage}", file=sys.__stdout__, flush=True)
        on_progress()

    async def fetch_one(star: dict[str, Any], target: TargetRef, survey: str) -> None:
        try:
            result = await fetchers[survey].fetch(target)
        except Exception as error:
            result = error
        record(star, survey, result)

    async def fetch_gaia() -> None:
        # Gaia is fetched in batches, never per star (spec §5.1).
        try:
            curves = await gaia.fetch_many(targets)
        except Exception as error:
            for star in stars:
                record(star, "gaia", error)
            return
        for star in stars:
            record(star, "gaia", curves[star["gaia_source_id"]])

    jobs = [
        fetch_one(star, target, survey)
        for survey in surveys
        if survey != "gaia"
        for star, target in zip(stars, targets, strict=True)
    ]
    if "gaia" in surveys:
        jobs.append(fetch_gaia())
    await asyncio.gather(*jobs)
    return failures


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the offline Light Curve fixtures.")
    parser.add_argument(
        "--survey",
        action="append",
        choices=SURVEYS,
        help="a survey to fetch; repeat for several (default: all four)",
    )
    parser.add_argument(
        "--star", action="append", help="a manifest star to fetch; repeat for several"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--catalog", action="store_true", help="record the Gaia catalog join for every star"
    )
    mode.add_argument(
        "--measure",
        action="store_true",
        help="print untrimmed Parquet sizes per star; writes no fixtures",
    )
    args = parser.parse_args(argv)
    surveys = args.survey or list(SURVEYS)

    header, stars = read_manifest()
    selected = [s for s in stars if args.star is None or s["star"] in args.star]
    unknown = set(args.star or ()) - {s["star"] for s in stars}
    if unknown:
        parser.error(f"not in the manifest: {', '.join(sorted(unknown))}")
    if args.catalog:
        if args.star:
            parser.error("--catalog records the whole manifest; drop --star")
        record_catalog(stars)
        return 0
    if args.measure:
        print(measure(selected, surveys))
        return 0

    failures = asyncio.run(
        build(selected, surveys, on_progress=lambda: write_manifest(header, stars))
    )
    write_manifest(header, stars)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
