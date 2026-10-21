# Argelander

The pipeline that proposes evidence-backed corrections to VSX variable-star
entries. One context: everything from picking a star to hearing back from a
VSX moderator.

## Language

### Selection

**Target**:
One VSX entry under study, identified by its VSX OID and Gaia DR3 source ID.
_Avoid_: star, object, candidate, source

**Slice**:
The batch of Targets selected together: one sky region and one magnitude range.
_Avoid_: batch, run, region

### Evidence

**Light Curve**:
The time series for one Target from one survey, in BJD_TDB, after survey quality
cleaning.
_Avoid_: photometry, data, time series

**Cache**:
The on-disk Parquet store of raw Light Curves, keyed by survey and source ID.
Re-downloadable; never the system of record.

**Zero-point offset**:
The constant magnitude difference between two ZTF fields' measurements of the same
Target, compared at equal phase of its cycle. A large one means a neighbour's light
sits in one field's photometry and not the other's; a difference between raw medians
is not one, since a variable star's fields sample its cycle unevenly.
_Avoid_: calibration error, field offset

### Analysis

**Analysis**:
The derived quantities for one Target from one pipeline version: periodograms,
candidate periods per survey, alias tests, blend check, colour-magnitude position.
_Avoid_: result, run output

**Pipeline Version**:
The stamp on every Analysis and Proposal that says which code and rules produced
it. Two rows with different Pipeline Versions are comparable, not duplicates.
_Avoid_: version, revision

**Agreement**:
The condition for accepting a period: at least two independent sources among
ZTF g, ZTF r, ASAS-SN, TESS and Gaia's published period agree after alias tests. A
source from a survey the blend check finds contaminated counts as half a source.
_Avoid_: consensus, match

### Proposals and review

**Proposal**:
The classifier's candidate VSX revision for one Target: type, period, epoch,
magnitude range with band, and confidence, with the Analysis it rests on.
_Avoid_: classification, prediction, result, candidate

**Confidence**:
The classifier's 0–1 score for a Proposal, built from explainable rule evidence.
Sorts the review queue; never auto-approves anything.
_Avoid_: probability, score

**Superseded**:
The state of an unreviewed Proposal that a newer Pipeline Version has replaced.
Reviewed or submitted Proposals are never superseded.

**Review**:
Tom's decision on a Proposal (approve, edit then approve, or reject) with notes.
The evidence trail and future training data.
_Avoid_: approval, decision, vetting

### Submission

**Submission**:
An approved Proposal prepared as VSX revision-form field values, reference text
and a phase plot, and entered manually by Tom.
_Avoid_: revision, report, ticket

**Verdict**:
A VSX moderator's response to a Submission: accepted, or rejected with a reason.
_Avoid_: outcome, status, feedback

### Pipeline

**Stage**:
One of the six pipeline steps: target selector, fetcher, analyzer, classifier
and plots, review, submission tracker.

**Job**:
One unit of Stage work on one Target, claimed from the Postgres queue.
_Avoid_: task
