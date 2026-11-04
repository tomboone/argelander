"""Profile one Target's Analysis step by step, read-only (NEU-2190).

It reads the Target and its `ok` Light Curve files with the query `analysis/worker.py`
`run_once` uses, but in a READ ONLY transaction, and reads the curves from the Cache. Then:

  pass 1: `pipeline.analyze` with a timer around every step, and around every BLS `power`
          call (coarse, refine, Gaia period, with each one's grid size); then the worker's
          hand-off without its database writes (`propose.elements`, then `render_all`);
  pass 2: the same under cProfile (skip it with --no-cprofile).

It writes `report.txt`, `report.json`, `profile.prof`, `profile.txt` and the plots under
--out (/tmp/neu2190 by default) and prints report.txt. Nothing is written to the database,
the Cache or PLOTS_DIR. ("Step" here is one call inside the analyze Stage, not a Stage.)
Without --target it takes the Target with the most TESS points.

On the mini, in a one-off analyzer container beside the running analyzer (about 12 minutes
for a viewing-zone Target, both passes), from a laptop checkout:

    ssh kumadrome 'mkdir -p /tmp/neu2190 && chmod 777 /tmp/neu2190'
    ssh kumadrome 'cd ~/argelander && docker compose run --rm --no-deps -T \
        -v /tmp/neu2190:/tmp/neu2190 analyzer python - --out /tmp/neu2190' \
        < scripts/profile_target.py
    scp -r kumadrome:/tmp/neu2190 ~/Downloads/neu2190-prod

On the dev stack:

    docker compose exec -T app python - --min-tess-points 1 < scripts/profile_target.py
"""

import argparse
import cProfile
import io
import json
import pstats
import time
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

import argelander.analysis.periodogram as periodogram
import argelander.analysis.pipeline as pipeline
from argelander import PIPELINE_VERSION
from argelander.analysis.worker import load_curves
from argelander.classify.propose import elements, range_curve
from argelander.config import get_settings
from argelander.db.models import Analysis as AnalysisRow
from argelander.db.models import (
    Job,
    JobKind,
    JobStatus,
    LightCurveFile,
    LightCurveFileStatus,
    Survey,
    Target,
)
from argelander.db.session import sync_session_factory
from argelander.plots.render import render_all
from argelander.selector import gaia_record

# --- step timers ----------------------------------------------------------------------------

timings: dict[tuple[str, str], float] = defaultdict(float)
bls_calls: list[dict[str, Any]] = []
current_bls: dict[str, Any] = {}
fine_grids: dict[str, int] = {}


def label_of(args: tuple) -> str:
    lc = args[0] if args else None
    if hasattr(lc, "survey") and hasattr(lc, "band"):
        return f"{lc.survey}/{lc.band}"
    return "-"


def timed(step: str, fn: Callable) -> Callable:
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            timings[(step, label_of(args))] += time.perf_counter() - start

    return wrapper


def timed_bls(fn: Callable) -> Callable:
    def wrapper(lc, *args, **kwargs):
        current_bls.clear()
        current_bls.update(
            label=f"{lc.survey}/{lc.band}",
            fine=int(periodogram._bls_grid(lc.time).size),
            coarse=int(periodogram._bls_grid(lc.time, coarsen=periodogram.BLS_COARSEN).size),
            calls=0,
        )
        fine_grids[current_bls["label"]] = current_bls["fine"]
        start = time.perf_counter()
        try:
            return fn(lc, *args, **kwargs)
        finally:
            timings[("bls", current_bls["label"])] += time.perf_counter() - start
            current_bls.clear()

    return wrapper


class TimedBLS(periodogram.BoxLeastSquares):
    """Records every `power` call BLS makes: its grid size, and which pass it is."""

    def power(self, period, duration, *args, **kwargs):
        start = time.perf_counter()
        try:
            return super().power(period, duration, *args, **kwargs)
        finally:
            seconds = time.perf_counter() - start
            n = int(np.size(period))
            if current_bls:
                current_bls["calls"] += 1
                if n == 1:
                    bls_pass = "gaia"
                elif current_bls["coarse"] < 3:
                    bls_pass = "fine (single pass)"
                else:
                    bls_pass = "coarse" if current_bls["calls"] == 1 else "refine"
                label = current_bls["label"]
            else:  # eclipse_duration, from the hand-off
                bls_pass, label = "eclipse_duration", "-"
            bls_calls.append({"label": label, "pass": bls_pass, "n_periods": n, "seconds": seconds})


STEPS = {
    "clean": "clean",
    "bin_tess": "bin_tess",
    "lomb_scargle": "lomb_scargle",
    "alias_test": "alias_test",
    "candidates": "alias_test",
    "blend": "blend",
    "realign": "realign",
    "refold": "realign",
    "agreement": "agreement",
    "cmd_position": "cmd",
    "shape": "shape",
    "variability": "variability",
}


def instrument() -> None:
    for name, step in STEPS.items():
        setattr(pipeline, name, timed(step, getattr(pipeline, name)))
    # setattr, not assignment: the stand-ins are not the patched names' declared types.
    for module, name, value in (
        (pipeline, "box_least_squares", timed_bls(pipeline.box_least_squares)),
        (periodogram, "BoxLeastSquares", TimedBLS),
    ):
        setattr(module, name, value)


# --- the run --------------------------------------------------------------------------------


def load(target_id: int | None, min_tess_points: int) -> tuple[Target, list, dict[str, Any]]:
    sessions = sync_session_factory()
    with sessions() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        if target_id is None:
            target_id = session.scalars(
                select(LightCurveFile.target_id)
                .where(
                    LightCurveFile.survey == Survey.TESS,
                    LightCurveFile.status == LightCurveFileStatus.OK,
                    LightCurveFile.n_points >= min_tess_points,
                )
                .order_by(LightCurveFile.n_points.desc())
                .limit(1)
            ).first()
            if target_id is None:
                raise SystemExit(f"no Target has a TESS curve of {min_tess_points}+ points")
        target = session.get_one(Target, target_id)
        files = session.scalars(
            select(LightCurveFile)
            .where(
                LightCurveFile.target_id == target.id,
                LightCurveFile.status == LightCurveFileStatus.OK,
            )
            .order_by(LightCurveFile.survey)
        ).all()
        row = session.scalars(
            select(AnalysisRow).where(
                AnalysisRow.target_id == target.id,
                AnalysisRow.pipeline_version == PIPELINE_VERSION,
            )
        ).first()
        claimed = claimed_analyze_jobs(session)
        info = {
            "target_id": target.id,
            "vsx_name": target.vsx_name,
            "gaia_dr3": target.gaia_source_id,
            "pipeline_version": PIPELINE_VERSION,
            "files": {
                str(f.survey): {
                    "n_points": f.n_points,
                    "baseline_days": (
                        round(f.t_max_bjd - f.t_min_bjd, 1)
                        if f.t_max_bjd is not None and f.t_min_bjd is not None
                        else None
                    ),
                    "bands": f.bands,
                }
                for f in files
            },
            "stored_analysis_seconds": (
                round((row.finished_at - row.started_at).total_seconds(), 1)
                if row is not None and row.finished_at and row.started_at
                else None
            ),
            "analyze_jobs_claimed_at_start": claimed,
        }
    # Leaving the block closes the session, which rolls the read-only transaction back without
    # expiring the loaded rows (an explicit rollback would, and they are read detached).
    return target, list(files), info


def claimed_analyze_jobs(session: Session) -> int:
    """analyze Jobs claimed now: the analyzer's own while it runs, and any stale claim."""
    return (
        session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.kind == JobKind.ANALYZE, Job.status == JobStatus.CLAIMED)
        )
        or 0
    )


def claimed_now() -> int:
    with sync_session_factory()() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        return claimed_analyze_jobs(session)


def run(target: Target, files: list, out: Path) -> dict[str, Any]:
    settings = get_settings()
    phases: dict[str, Any] = {}
    t0 = time.perf_counter()
    curves = load_curves(settings.cache_dir, target, files)
    record = gaia_record(target)
    t1 = time.perf_counter()
    analysis, cleaned = pipeline.analyze(curves, record, target_id=target.id)
    t2 = time.perf_counter()
    found = elements(analysis, cleaned, target)
    t3 = time.perf_counter()
    contaminated = analysis.blend.surveys if analysis.blend is not None else frozenset()
    range_band = range_curve(cleaned, contaminated)
    render_all(
        analysis,
        cleaned,
        vsx_type=found.proposed_type,
        period=found.period,
        epoch=found.epoch_bjd,
        out_dir=out / "plots",
        chosen=(range_band.survey, range_band.band) if range_band is not None else None,
    )
    t4 = time.perf_counter()
    phases.update(
        load_curves=t1 - t0,
        analyze=t2 - t1,
        elements=t3 - t2,
        render_all=t4 - t3,
        total=t4 - t0,
    )
    phases["_points"] = {f"{lc.survey}/{lc.band}": int(lc.time.size) for lc in cleaned}
    return phases


def report(info: dict[str, Any], phases: dict[str, Any]) -> str:
    lines = [
        f"Target {info['vsx_name']} (id {info['target_id']}), Gaia DR3 {info['gaia_dr3']}, "
        f"pipeline {info['pipeline_version']}",
        f"analyze Jobs claimed at start / after pass 1 (the analyzer running alongside): "
        f"{info['analyze_jobs_claimed_at_start']} / {info.get('analyze_jobs_claimed_after')}",
        f"stored 0.2.0 Analysis took: {info['stored_analysis_seconds']} s",
        "files: " + json.dumps(info["files"]),
        "points after clean+bin: " + json.dumps(phases["_points"]),
        "",
        "phase                  seconds",
    ]
    for key in ("load_curves", "analyze", "elements", "render_all", "total"):
        lines.append(f"  {key:<20} {phases[key]:8.2f}")
    lines += ["", "step / band            seconds"]
    by_step: dict[str, float] = defaultdict(float)
    for (step, label), seconds in sorted(timings.items()):
        lines.append(f"  {step:<12} {label:<14} {seconds:8.2f}")
        by_step[step] += seconds
    lines += ["", "step totals"]
    for step, seconds in sorted(by_step.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {step:<20} {seconds:8.2f}")
    step_sum = sum(by_step.values())
    accounted = step_sum + phases["load_curves"] + phases["elements"] + phases["render_all"]
    lines += [
        f"  {'(sum of steps)':<20} {step_sum:8.2f}  of analyze {phases['analyze']:.2f}"
        f" ({100 * step_sum / phases['analyze']:.1f} %)",
        f"  {'(+ load, hand-off)':<20} {accounted:8.2f}  of total {phases['total']:.2f}"
        f" ({100 * accounted / phases['total']:.1f} %)",
    ]
    lines += ["", "BLS power calls: band, pass, grid size, seconds, share of the band's BLS"]
    band_bls = {label: seconds for (step, label), seconds in timings.items() if step == "bls"}
    for call in bls_calls:
        total = band_bls.get(call["label"])
        share = f"{100 * call['seconds'] / total:5.1f} %" if total else ""
        lines.append(
            f"  {call['label']:<14} {call['pass']:<18} {call['n_periods']:>10} "
            f"{call['seconds']:8.2f}  {share}"
        )
    lines += ["", "BLS fine grid (the single-pass search the coarse pass replaces), by band"]
    lines += [f"  {label:<14} {fine:>10}" for label, fine in fine_grids.items()]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=int, help="targets.id; default: the most TESS points")
    parser.add_argument("--min-tess-points", type=int, default=250_000)
    parser.add_argument("--out", type=Path, default=Path("/tmp/neu2190"))
    parser.add_argument("--no-cprofile", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    target, files, info = load(args.target, args.min_tess_points)
    instrument()
    phases = run(target, files, args.out)
    info["analyze_jobs_claimed_after"] = claimed_now()
    text_report = report(info, phases)
    (args.out / "report.txt").write_text(text_report + "\n")
    (args.out / "report.json").write_text(
        json.dumps(
            {
                "info": info,
                "phases": phases,
                "steps": [
                    {"step": s, "label": label, "seconds": v} for (s, label), v in timings.items()
                ],
                "bls_calls": bls_calls,
                "bls_fine_grids": fine_grids,
            },
            indent=2,
            default=str,
        )
    )
    print(text_report, flush=True)

    if not args.no_cprofile:
        timings.clear()
        bls_calls.clear()
        profiler = cProfile.Profile()
        profiler.enable()
        profiled = run(target, files, args.out)
        profiler.disable()
        profiler.dump_stats(args.out / "profile.prof")
        buffer = io.StringIO()
        stats = pstats.Stats(profiler, stream=buffer).sort_stats("cumulative")
        stats.print_stats(60)
        stats.sort_stats("tottime").print_stats(40)
        (args.out / "profile.txt").write_text(
            f"cProfile pass total: {profiled['total']:.2f} s\n\n" + buffer.getvalue()
        )
        print(f"\ncProfile pass total: {profiled['total']:.2f} s (see profile.txt)", flush=True)


if __name__ == "__main__":
    main()
