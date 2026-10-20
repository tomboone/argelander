"""VSX revision-form field values (spec §9.1).

What goes in a form field is also what the evidence plots print, so `submission.png` shows
the band and period exactly as they are pasted into the form. The rest of the form's fields
arrive with the Submission tracker.
"""

import math

# The revision form's passband for each survey band (docs/reference/vsx-revision-form.md).
# TESS is not in the dropdown and goes in the custom passband box.
PASSBANDS = {
    ("ztf", "g"): "g",
    ("ztf", "r"): "r",
    ("ztf", "i"): "i",
    ("gaia", "G"): "G",
    ("asassn", "V"): "V",
    ("asassn", "g"): "g",
    ("tess", "T"): "TESS",
}

# The period is rounded so that the rounding shifts phase by at most this many cycles across
# the baseline: significant digits only, matched to the baseline (VSX Manual, Precision).
MAX_ROUNDING_DRIFT = 0.01


def format_period(period: float, baseline: float) -> str:
    """The period in days, to as many decimals as a `baseline`-day light curve determines.

    Rounding by δP drifts the fold by `baseline` × δP / P² cycles over the baseline, so the
    last decimal kept is the coarsest one whose half-unit stays under `MAX_ROUNDING_DRIFT`.
    """
    if period <= 0 or baseline <= 0:
        raise ValueError(f"period and baseline must be positive, got {period}, {baseline}")
    decimals = math.ceil(math.log10(baseline / (2 * MAX_ROUNDING_DRIFT * period**2)))
    return f"{period:.{max(decimals, 0)}f}"
