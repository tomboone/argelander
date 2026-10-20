"""One Target's Analysis from its Light Curves and its Gaia record: spec §6 steps 3–8 in order.

Clean each band, search it with Lomb-Scargle and BLS, rank every candidate and its aliases,
judge the blend checks, let the sources vote on a period with contaminated surveys at half
weight, and place the Target on the colour-magnitude diagram. Gaia's published period is
the first hypothesis in every band and a source in Agreement.

`analysis/worker.py` (M4) wraps this with the Cache reads and the `analyses` row; the
prototype notebook and the gate test (NEU-1711) call it on the fixtures directly.
"""

from collections.abc import Iterable

from argelander import PIPELINE_VERSION
from argelander.analysis.agreement import agreement
from argelander.analysis.aliases import alias_test, candidates
from argelander.analysis.blend import blend
from argelander.analysis.clean import clean
from argelander.analysis.cmd import cmd_position
from argelander.analysis.model import Analysis
from argelander.analysis.periodogram import box_least_squares, lomb_scargle
from argelander.catalog.gaia import GaiaRecord
from argelander.lightcurve import LightCurve


def analyze(
    curves: Iterable[LightCurve],
    record: GaiaRecord | None,
    *,
    target_id: int,
    pipeline_version: str = PIPELINE_VERSION,
) -> tuple[Analysis, list[LightCurve]]:
    """The Target's Analysis, and its cleaned Light Curves, which the plots draw.

    Without a Gaia `record` there is no Gaia period, blend check or CMD position.
    """
    gaia_period = record.vari_period if record is not None else None
    cleaned = [clean(lc) for lc in curves]
    analysis = Analysis(target_id=target_id, pipeline_version=pipeline_version)
    for lc in cleaned:
        results = (lomb_scargle(lc, gaia_period), box_least_squares(lc, gaia_period))
        analysis.periods.extend(results)
        analysis.alias_tests.append(alias_test(lc, candidates(*results)))
    if record is not None:
        analysis.blend = blend(record, cleaned)
        analysis.cmd = cmd_position(record)
    contaminated = analysis.blend.surveys if analysis.blend is not None else frozenset()
    analysis.agreement = agreement(analysis.alias_tests, gaia_period, contaminated)
    return analysis, cleaned
