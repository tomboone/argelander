"""One Target's Analysis from its Light Curves and its Gaia record: spec §6 steps 3–8 in order.

Clean each band, search it with Lomb-Scargle and BLS, rank every candidate and its aliases,
judge the blend checks, re-align each ZTF band's oids at the zero points the blend checks
measured, let the sources vote on a period with contaminated surveys at half weight, place
the Target on the colour-magnitude diagram, and measure the curve's shape at the agreed
period and its variability for the classifier. Gaia's published period is the first
hypothesis in every band and a source in Agreement.

Re-alignment (NEU-1802) changes the curve after its period search, not before: the
periodograms stand on the fetcher's median alignment, and only each band's best fold is
scored again, so the scatter Agreement reads is that of the curve the plots draw.

`analysis/worker.py` (M4) wraps this with the Cache reads and the `analyses` row; the
prototype notebook and the gate test (NEU-1711) call it on the fixtures directly.
"""

from collections.abc import Iterable

from argelander import PIPELINE_VERSION
from argelander.analysis.agreement import agreement
from argelander.analysis.aliases import alias_test, candidates, refold
from argelander.analysis.blend import blend, realign
from argelander.analysis.clean import clean
from argelander.analysis.cmd import cmd_position
from argelander.analysis.model import Analysis
from argelander.analysis.periodogram import box_least_squares, lomb_scargle
from argelander.analysis.shape import shape, variability
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
        analysis.blend = blend(record, cleaned, analysis.alias_tests)
        analysis.cmd = cmd_position(record)
        realigned = [realign(lc, analysis.blend.zero_points) for lc in cleaned]
        analysis.alias_tests = [
            test if moved is lc else refold(moved, test)
            for lc, moved, test in zip(cleaned, realigned, analysis.alias_tests, strict=True)
        ]
        cleaned = realigned
    contaminated = analysis.blend.surveys if analysis.blend is not None else frozenset()
    analysis.agreement = agreement(analysis.alias_tests, gaia_period, contaminated)
    analysis.shape = shape(cleaned, analysis.agreement, contaminated)
    analysis.variability = variability(cleaned, contaminated)
    return analysis, cleaned
