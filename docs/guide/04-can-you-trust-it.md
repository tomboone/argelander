# 4. Can you trust it?

Short answer: on the 27 stars it has been tested on, the pipeline gets the period right
for every star whose period is independently known, refuses the two aliases and the two
blends, and stays silent on the three constant stars. Its one failure was understood and
has been fixed. That is a strong result for a prototype and a weak basis for confidence in
the next thousand stars. This chapter says exactly what has been shown and what has not.

## The gate

Before any of this was trusted, the build order set a gate: run the pipeline on known
stars and see whether it finds what is published. The fixture set
(`tests/fixtures/manifest.yaml`, NEU-1710) has 27 stars, chosen on 2026-10-20:

- **20 positives**: VSX entries in Draco (and one Cepheid in neighbouring Cepheus) at
  13–16 mag whose VSX period agrees within 0.1 % with the period that Chen et al. (2020)
  found independently in ZTF. Entries whose VSX period was *identical* to the ZTF one were
  skipped, because VSX had imported it from that catalogue and the two would not be
  independent. They span the types the first classifier rules cover: `EW`, `EA`, `EB`,
  `RRAB`, `RRC`, `DSCT`, `HADS`, `DCEP`, `SR` and `ROT`.
- **2 alias traps**: entries whose VSX period differs from both the ZTF and the Gaia
  period by one cycle per day, while those two agree with each other. The VSX value is
  the trap.
- **2 blends**: entries whose position falls on a Gaia source not flagged variable, with a
  Gaia eclipsing binary within 1.5″ whose period matches the VSX one.
- **3 constants**: Gaia sources near the same sky position with no VSX entry, not flagged
  variable, and among the steadiest at their magnitude.

The pass rule (`argelander.prototype.judge`, enforced by `tests/test_prototype.py`): a
positive must reach [Agreement](../../CONTEXT.md#analysis) at its published period,
within the Agreement's own tolerance, or at exactly twice or half it; an alias trap must not reach Agreement at the
published alias, and if it reaches one at all it must be at the true period; a blend or a
constant must reach no Agreement or be flagged `blend_suspect`. A star the pipeline is
known to fail is listed in the test with its reason, and the test fails if it ever starts
passing, so the list has to be kept honest.

## The results, 2026-11-05

Run on the committed fixtures with all five sources: ZTF g, ZTF r, ASAS-SN (V and g, one
source), TESS and Gaia's published period. ASAS-SN joined the fixtures on 2026-11-02; this
run also carries the TESS binning of NEU-2187. *Relation* is the Agreement's own: 2:1 when
some sources found half the period and others the whole. Flags are the blend check's:
*blend* is `blend_suspect` (ZTF and Gaia), *asassn* and *tess* the other two surveys'
contamination flags.

| Star | Role | VSX type | VSX period (d) | Agreed period (d) | Relation | Sources | Flags | CMD region | Gate |
| --- | --- | --- | ---: | ---: | --- | --- | --- | --- | --- |
| NSVS 2887379 | positive | EW | 0.27930 | 0.27929 | 2:1 | ZTF g, ZTF r, ASAS-SN, TESS, Gaia | tess | main sequence | passes |
| ASASSN-V J164726.16+724659.3 | positive | EW | 0.30085 | 0.30085 | 2:1 | ZTF g, ZTF r, ASAS-SN, Gaia | tess | main sequence | passes |
| LINEAR 20427289 | positive | EW | 0.31915 | 0.31915 | 2:1 | ZTF g, ZTF r, ASAS-SN, Gaia | tess | main sequence | passes |
| ASASSN-V J163706.32+633921.6 | positive | EA | 1.30324 | 1.30329 | 1:1 | ZTF g, ZTF r, ASAS-SN, TESS, Gaia | tess | main sequence | passes |
| DW Dra | positive | EA | 1.22625 | 1.22633 | 1:1 | ZTF g, ZTF r, ASAS-SN, TESS, Gaia | tess | main sequence | passes |
| T-Dra0-09947 | positive | EA | 0.40982 | 0.40982 | 1:1 | ZTF g, ZTF r, ASAS-SN, TESS, Gaia | tess | main sequence | passes |
| 1SWASP J191601.56+560055.3 | positive | EB | 0.32202 | 0.32202 | 1:1 | ZTF g, ZTF r, ASAS-SN, TESS, Gaia | asassn, tess | main sequence | passes |
| CSS_J173907.6+550053 | positive | RRAB | 0.48585 | 0.48586 | 1:1 | ZTF g, ZTF r, ASAS-SN, Gaia | tess | instability strip | passes |
| V0364 Dra | positive | RRAB | 0.47704 | 0.47704 | 1:1 | ZTF g, ZTF r, ASAS-SN, TESS, Gaia | tess | instability strip | passes |
| V0502 Dra | positive | RRAB | 0.48013 | 0.48012 | 1:1 | ZTF g, ZTF r, ASAS-SN, TESS, Gaia | tess | instability strip | passes |
| ASASSN-V J163919.05+740125.0 | positive | RRAB | 0.48897 | 0.48896 | 1:1 | ZTF g, ZTF r, ASAS-SN, TESS, Gaia | tess | instability strip | passes |
| V0379 Dra | positive | RRC | 0.35762 | 0.35757 | 1:1 | ZTF g, ZTF r, ASAS-SN, TESS, Gaia | – | instability strip | passes |
| ASASSN-V J164540.98+652508.2 | positive | RRC | 0.42448 | 0.42449 | 1:1 | ZTF g, ZTF r, ASAS-SN, Gaia | tess | unknown | passes |
| ASASSN-V J170333.89+604850.1 | positive | DSCT | 0.05032 | 0.05032 | 1:1 | ZTF g, ZTF r, ASAS-SN, TESS, Gaia | tess | instability strip | passes |
| V0553 Dra | positive | HADS | 0.17198 | 0.17198 | 1:1 | ZTF g, ZTF r, ASAS-SN | – | instability strip | passes |
| KO Cep | positive | DCEP | 4.55809 | 4.55814 | 1:1 | ZTF g, ZTF r, ASAS-SN, TESS, Gaia | tess | giant branch | passes |
| ASASSN-V J194707.50+750321.7 | positive | SR | 55.50 | 55.27 | 1:1 | ZTF g, ZTF r, ASAS-SN | tess | giant branch | passes |
| ASASSN-V J192915.72+664402.8 | positive | SR | 42.25 | 42.25 | 1:1 | ZTF g, ZTF r, ASAS-SN | tess | giant branch | passes |
| ASASSN-V J164537.58+592828.7 | positive | ROT | 12.09 | 12.111 | 1:1 | ZTF g, ZTF r, ASAS-SN, TESS | tess | main sequence | passes |
| ASASSN-V J183551.48+614330.1 | positive | ROT | 2.53156 | 2.53305 | 1:1 | ZTF g, ZTF r | tess | main sequence | passes |
| PS1-3PI J190107.42+492456.3 | alias trap | RRC | 0.46287 | 0.31616 | 1:1 | ZTF g, ZTF r, ASAS-SN, Gaia | tess | instability strip | passes |
| CSS_J181701.4+514940 | alias trap | EW | 0.44657 | 0.30858 | 2:1 | ZTF g, ZTF r, ASAS-SN, TESS, Gaia | tess | main sequence | passes |
| CSS_J160212.7+530724 | blend | EW | 0.31802 | – | – | – | asassn, blend, tess | unknown | passes |
| CSS_J172350.6+504650 | blend | EW | 0.27008 | – | – | – | asassn, blend, tess | unknown | passes |
| Gaia DR3 1435854639894380800 | constant | – | – | – | – | – | tess | giant branch | passes |
| Gaia DR3 1434111643382583936 | constant | – | – | – | – | – | tess | main sequence | passes |
| Gaia DR3 1435683567052954368 | constant | – | – | – | – | – | tess | main sequence | passes |

To reproduce it, run `task test -- tests/test_prototype.py`, or
[`notebooks/01_prototype.ipynb`](../../notebooks/01_prototype.ipynb), which prints the
same table with the votes.

## What passes, and what each kind of negative tests

**All 20 positives agree at the published period.** The agreed and published values
differ in the fifth decimal for short periods, which is the precision seven years of ZTF
buys, and by a fraction of a percent for the two semiregulars at 42 and 55 days, where a
peak cannot be narrower. Fifteen of the twenty had a Gaia published period to vote with,
and Gaia agreed with the surveys every time. ASAS-SN voted in nineteen of the twenty
Agreements; the twentieth, ASASSN-V J183551.48+614330.1, is a faint rotator whose
ASAS-SN folds explain too little to vote, and it agrees from ZTF g and r alone. Three
contact binaries passed through a 2:1 Agreement. In each, ASAS-SN settled at half the
period (for NSVS 2887379, one of its two bands, and ZTF r with it), where an `EW` star's
two nearly equal eclipses fold onto each other (chapter 2's *Half periods*); the other
sources found the whole, and the rule took the whole.

**Both alias traps resolved to the true period.** PS1-3PI J190107.42+492456.3 is an RR
Lyrae star VSX lists at 0.46287 days; the pipeline found 0.31617, the period VSX's value
is a 1-day alias of, from ZTF g, ZTF r and Gaia. CSS_J181701.4+514940 is the case in
chapters 2 and 3. These test the one thing the bulk imports could not do: choose the
right peak when the tallest one is wrong.

**Both blends reached no Agreement and were flagged.** Every survey of each was flagged:
ZTF, Gaia and ASAS-SN by the neighbour within 1.5″, TESS by its crowded field. All four
sources, ZTF g, ZTF r, ASAS-SN and TESS, voted for the neighbour's period or half of it. Four half-weight votes add up to 2.0, which is
all the weight an Agreement needs, and when ASAS-SN first arrived, before the clean-source
rule, both blends got through that way. What stops them now is that not one of the four is
clean (chapter 3, section 3.5). These test whether the Gaia neighbour list reaches the pipeline
and is used.

**The three constant stars reached no Agreement.** Every ASAS-SN and TESS band of the
three folded at 0.97 to 0.99, and five of their six ZTF bands at 0.82 to 0.99, all above
the 0.75 cut-off, so none of those offered a period. The sixth, ZTF g of Gaia DR3
1435683567052954368, folds at 0.60 at 0.113 days, a 1-day alias of a box-fit peak in its
own Light Curve, and so it votes. Nothing votes with it, so nothing is agreed: the cut-off
let a band through, and the two-source rule caught it, which is the reason there are two
rules. These test whether the pipeline can say *nothing here*, which the bulk imports also
could not.

## The one failure, and how it was fixed

ASASSN-V J164537.58+592828.7 is a spotted rotating star (`ROT`) with a 12.09-day period.
The pipeline saw it from the first run: ZTF g and r both settle at 12.11 days, and TESS at
12.15. But every one of those sources was weighed at half: TESS because its field holds 22
other stars adding up to more light than the Target, and ZTF because the zero-point rule
fired. Two halves make 1, three make 1.5, either way under the 2 required, and the gate
recorded *no Agreement*. The star was listed as a known failure in
`tests/test_prototype.py`, so it failed visibly on every run until it was fixed.

The ZTF zero-point rule was the real culprit, and it deserves a plain explanation because
it was on 11 of the 20 positives. ZTF measures a star in several overlapping fields, each
with its own identifier, and the fetcher compared the median magnitude of each against the
others; a disagreement of more than 0.05 mag was meant to catch a neighbour leaking into
one field and not another. But for a *variable* star, two fields that happened to observe
at different phases of the cycle have different medians with nothing wrong at all, and the
larger the amplitude the larger the difference. The rule mostly flagged the stars it should
have trusted most.

NEU-1798 moved the judgement from the fetcher to the blend check, after the period is
settled. Each field is folded at the band's period and compared, phase bin by phase bin,
with the curve the band's other fields trace; what is left is the field's real zero-point
difference. On V0364 Dra the median rule said ZTF g's two fields differed by 0.12 mag; at
equal phase they differ by 0.01. No positive is flagged now, both blends still are, by
their neighbours, and the ROT star reaches Agreement at 12.122 days from ZTF g, ZTF r and
TESS: two full votes and a half. With ASAS-SN it has a third full vote, and on the
current run it agrees at 12.111 days.

One caveat belongs with that result. Five of the fields that carried the old flags, with
median offsets of 0.10 to 0.15 mag and the ROT star's 0.096, have only 2 to 9 points in
the fixtures, which are trimmed to 500 points a band. A median of that few points of a
variable star is mostly luck, but at equal phase so few points are not enough to judge
either, so those fields are recorded as *unmeasured* and do not flag. The ROT star's ZTF g
field is one of them (8 points), so its ZTF g vote counts in full because that field could
not be judged, not because it was judged clean. On the full Light Curves the pipeline will
see in production, the same fields hold several times as many points and will be
measured.

Since NEU-1802 the comparison is also put to use. Each measured field is moved onto the
reference field's zero point at equal phase before the plots are drawn and the range is
read. Until then, a field the median rule had shifted by a tenth of a magnitude too much
stayed shifted. On the fixtures this changes no gate result, and no positive's ZTF range
moves by more than 0.01 mag, because every field it moves by more than 0.03 mag holds only
10 to 40 of its band's 500 points.

## What ASAS-SN changed

When ASAS-SN joined the fixtures on 2026-11-02, the gate failed in two places on its first
run. Both were rules that had held only because a source was missing, and each was fixed
with a new rule, not a new number.

**An Agreement needs at least one clean source.** Weighing a contaminated source at half
was meant to stop a neighbour's period from passing as the Target's. Without ASAS-SN, a
blend's three contaminated sources weighed 1.5 and fell short of the 2 required. With it,
four weighed 2.0, and both blends reached Agreement at the neighbour's period; the
classifier went on to score them `DSCT` and `EW` at Confidence 1.0. The arithmetic was
right and the reasoning was wrong: when every survey is looking through the same
neighbour, they agree because they see the same thing, and adding more of them adds
nothing. So weight is no longer enough on its own. At least one source at the period must
be clean. On the fixtures this changes only the two blends: every positive and both alias
traps have a clean ZTF band among their agreeing sources.

**The constant-star check leaves ASAS-SN out.** When no period is agreed, the classifier
still has to say whether the Target varies at all. A constant star (`CST`) and a slow
irregular variable (`L`) both lack a period, and what tells them apart is whether the
magnitudes spread further than their errors explain. That comparison is only as good as
the errors, and ASAS-SN reports errors smaller than its real scatter: on the three
constant stars its scatter is 1.8 to 3.2 times its reported error, where ZTF's is 0.95 to
1.3 times. Measured against ASAS-SN's errors, the constants at G 13.7 and 14.0 seemed to
spread 2.8 to 3.6 times their noise, well over the 2.0 a constant must stay under and, for
one of them, past the 3.0 that marks a variable; both scored as irregular variables. So the
check never measures ASAS-SN. It measures clean ZTF bands, which cover the same years to
the same depth, and TESS when its field is clean; a Target with neither goes unmeasured
rather than measured on the wrong errors. A correction factor on ASAS-SN's
errors was considered and rejected: the understatement falls from 3.2 to 1.8 times
between G 14 and G 15, and a factor fitted to three stars would describe only those three.

Beyond the two rules, ASAS-SN added a fifth source behind most Agreements and the V-band
range VSX prefers (chapter 3, section 3.8). No positive's Agreement moved off its
published period.

## The thresholds were tuned on these 27 stars

Several numbers in the pipeline are not derived from first principles. They were set
by looking at these stars, and the margins are thin:

| Threshold | Value | What set it | Margin on the 27 |
| --- | ---: | --- | --- |
| Fold scatter above which a band does not vote | 0.75 | When it was set, the constants' ZTF folds scored 0.82 to 0.99 and every voting band of a positive 0.71 or less | Breached: one constant's ZTF g band now folds at 0.60 and votes, alone. The highest voting band of a positive is still 0.71 (a δ Scuti star in ZTF r) |
| Period tolerance | 0.1 %, or P / baseline | ZTF g and r came out 0.10 to 0.17 % apart on the same 40-day peak under a fixed 0.1 % rule | Set by two semiregulars |
| Neighbour radius and brightness for `blend_suspect` | 3″, brighter than G + 2 | VSX's contamination radii, widened for ZTF's seeing and Gaia's BP/RP windows | Both blends have neighbours at 1.2 and 1.5″; nothing tests a neighbour at 2.9″ |
| ZTF zero-point offset, at equal phase | 0.05 mag | Spec, by assertion; measured at equal phase since NEU-1798 | Fires on none; the largest measured offset on a positive is 0.037 mag (the ROT star's ZTF r) |
| TESS field brightness | G + 1, or half the Target's light combined | Spec, by assertion | Fires on 25 of 27 |
| Points needed to search a band | 30 | Spec, by assertion | V0364 Dra's Gaia bands have 32 to 41 |
| Clean sources among an Agreement's | at least 1 | ASAS-SN's arrival: both blends reached weight 2.0 from four contaminated sources at the neighbour's period | Changes only the two blends; every other Agreement on the 27 has a clean ZTF band among its sources |
| Surveys the constant-star check measures | clean ZTF and TESS, never ASAS-SN | ASAS-SN's arrival: on the constants ASAS-SN's scatter is 1.8 to 3.2 times its reported errors, ZTF's 0.95 to 1.3 | In ZTF the constants spread at most 1.4 times their errors, under the 2.0 a constant must stay below; measured on ASAS-SN, two of them spread 2.8 to 3.6 times and scored as irregular variables |

The gate is a regression test: it says the pipeline has not got worse on these stars. It
is not a measurement of how often the pipeline is right on a star it has never seen.
ASAS-SN moved none of the numbers above; it moved the rules instead. On the current run
one constant's ZTF g band sits under the fold-scatter line. Every number will have to be
re-examined on the first real [Slice](../../CONTEXT.md#selection), where there will be
stars of types and periods the fixtures do not contain.

## What the pipeline cannot know

Some things no amount of agreement between surveys can settle. These are the checks Tom
makes in his [Review](../../CONTEXT.md#proposals-and-review), with the
[Proposal](../../CONTEXT.md#proposals-and-review) open, before anything becomes a
[Submission](../../CONTEXT.md#submission).

- **Whether the entry is the right star.** The pipeline starts from a VSX entry matched
  to a Gaia source by position, the [Target](../../CONTEXT.md#selection). If VSX's position is off, or two Gaia sources sit under
  it, the evidence is for the wrong star. Check the neighbour table and the separation
  VSX records.
- **The type.** Until milestone 4 the only type hint is Gaia's own classification and
  the shape of the fold. The alias test tells `EW` from a pulsator by whether 2P folds
  with two different minima, but the classifier rules that turn that into a confident type
  do not exist yet. Read the fold yourself against the shapes in
  [`docs/reference/vsx-type-ranges.md`](../reference/vsx-type-ranges.md).
- **The magnitude range when a survey is contaminated.** The range is reported
  uncorrected, with a Remarks sentence saying so. If every survey is flagged, the range is
  not the star's.
- **Periods the data cannot hold.** The search stops at 100 days, and a period longer
  than a survey's span, or than TESS's 27-day sector, is invisible to that survey. A
  semiregular star's period changes from cycle to cycle, and Agreement at 2 % does not
  mean the number is stable.
- **More than one period.** The pipeline settles one. A star pulsating in two modes, or a
  binary with a spotted star in it, has more than one, and the second shows only as extra
  scatter in the fold.
- **What is already known.** A VSX entry has references and remarks, and sometimes a
  published study with better data than any survey. Read the entry before proposing a
  change to it; VSX's rules expect the revision comment to say why the new value is better
  than the old.
- **Whether ASAS-SN can see a faint star.** ASAS-SN reaches about 16.5 mag, and the
  Slice runs to 16. It voted for 19 of the 20 positives, but the one it missed,
  ASASSN-V J183551.48+614330.1, is at G 15.2, and fainter Targets will lose it more often.
  When ASAS-SN drops out, an Agreement rests on one source fewer, and when its V band is
  short of points or contaminated, the range comes from another band, named on the
  Proposal. A range in V and a range in g are not interchangeable; check which one you
  are reading.

## Before the first submission

A short checklist, built from the chapters above, for a Proposal that looks right:

1. The combined light curve shows the variation in every survey, not only one.
2. The periodogram has the same peak in every searched band, and the dashed alias lines
   sit on smaller peaks, not comparable ones.
3. Every phase plot folds to one clean curve, and the eclipsing-binary ones show two
   minima when folded at the proposed period.
4. The votes come from at least two surveys at full weight, or you can explain each half.
5. Each flag has either a neighbour in the table to justify it or an explanation for why
   it is a false alarm.
6. The colour-magnitude region is one the type can live in.
7. The VSX entry's existing references do not already settle the question.
8. You can say, in your own words, why the published value is wrong and the proposed
   value is right. That sentence is the revision comment.

## Self-check

Two questions to answer from this guide alone, without opening the notebook. If either is
hard, the chapter named is the one to reread.

1. Why is VSX's 0.4466 days for CSS_J181701.4+514940 an alias, and of what? (Chapter 2,
   *The 1-day alias* and *Half periods*; chapter 3, section 3.4.)
2. Why will the pipeline not vouch for CSS_J160212.7+530724's period, when its light
   curve folds so well? (Chapter 2, *Neighbouring stars*; chapter 3, sections 3.5 and
   3.6.)
