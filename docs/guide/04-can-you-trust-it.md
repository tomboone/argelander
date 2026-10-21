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

## The results, 2026-10-20

Run on the committed fixtures with ZTF g, ZTF r, TESS and Gaia's published period. ASAS-SN
was unavailable (NEU-1796). Flags are the blend check's: *blend* is `blend_suspect` (ZTF
and Gaia), *asassn* and *tess* the other two surveys' contamination flags. They are judged
from the Gaia catalogue, so the ASAS-SN flag is set even though no ASAS-SN Light Curve
exists.

| Star | Role | VSX type | VSX period (d) | Agreed period (d) | Relation | Sources | Flags | CMD region | Gate |
| --- | --- | --- | ---: | ---: | --- | --- | --- | --- | --- |
| NSVS 2887379 | positive | EW | 0.27930 | 0.27929 | 2:1 | ZTF g, ZTF r, TESS, Gaia | tess | main sequence | passes |
| ASASSN-V J164726.16+724659.3 | positive | EW | 0.30085 | 0.30085 | 1:1 | ZTF g, ZTF r, Gaia | tess | main sequence | passes |
| LINEAR 20427289 | positive | EW | 0.31915 | 0.31915 | 1:1 | ZTF g, ZTF r, Gaia | tess | main sequence | passes |
| ASASSN-V J163706.32+633921.6 | positive | EA | 1.30324 | 1.30330 | 1:1 | ZTF g, ZTF r, TESS, Gaia | tess | main sequence | passes |
| DW Dra | positive | EA | 1.22625 | 1.22633 | 1:1 | ZTF g, ZTF r, TESS, Gaia | tess | main sequence | passes |
| T-Dra0-09947 | positive | EA | 0.40982 | 0.40982 | 1:1 | ZTF g, ZTF r, TESS, Gaia | tess | main sequence | passes |
| 1SWASP J191601.56+560055.3 | positive | EB | 0.32202 | 0.32202 | 1:1 | ZTF g, ZTF r, TESS, Gaia | asassn, tess | main sequence | passes |
| CSS_J173907.6+550053 | positive | RRAB | 0.48585 | 0.48586 | 1:1 | ZTF g, ZTF r, Gaia | tess | instability strip | passes |
| V0364 Dra | positive | RRAB | 0.47704 | 0.47705 | 1:1 | ZTF g, ZTF r, TESS, Gaia | tess | instability strip | passes |
| V0502 Dra | positive | RRAB | 0.48013 | 0.48012 | 1:1 | ZTF g, ZTF r, TESS, Gaia | tess | instability strip | passes |
| ASASSN-V J163919.05+740125.0 | positive | RRAB | 0.48897 | 0.48896 | 1:1 | ZTF g, ZTF r, TESS, Gaia | tess | instability strip | passes |
| V0379 Dra | positive | RRC | 0.35762 | 0.35757 | 1:1 | ZTF g, ZTF r, TESS, Gaia | – | instability strip | passes |
| ASASSN-V J164540.98+652508.2 | positive | RRC | 0.42448 | 0.42449 | 1:1 | ZTF g, ZTF r, Gaia | tess | unknown | passes |
| ASASSN-V J170333.89+604850.1 | positive | DSCT | 0.05032 | 0.05032 | 1:1 | ZTF g, ZTF r, TESS, Gaia | tess | instability strip | passes |
| V0553 Dra | positive | HADS | 0.17198 | 0.17198 | 1:1 | ZTF g, ZTF r | – | instability strip | passes |
| KO Cep | positive | DCEP | 4.55809 | 4.55813 | 1:1 | ZTF g, ZTF r, TESS, Gaia | tess | giant branch | passes |
| ASASSN-V J194707.50+750321.7 | positive | SR | 55.50 | 55.14 | 1:1 | ZTF g, ZTF r | tess | giant branch | passes |
| ASASSN-V J192915.72+664402.8 | positive | SR | 42.25 | 42.26 | 1:1 | ZTF g, ZTF r | tess | giant branch | passes |
| ASASSN-V J164537.58+592828.7 | positive | ROT | 12.09 | 12.122 | 1:1 | ZTF g, ZTF r, TESS | tess | main sequence | passes |
| ASASSN-V J183551.48+614330.1 | positive | ROT | 2.53156 | 2.53305 | 1:1 | ZTF g, ZTF r | tess | main sequence | passes |
| PS1-3PI J190107.42+492456.3 | alias trap | RRC | 0.46287 | 0.31617 | 1:1 | ZTF g, ZTF r, Gaia | tess | instability strip | passes |
| CSS_J181701.4+514940 | alias trap | EW | 0.44657 | 0.30858 | 2:1 | ZTF g, ZTF r, TESS, Gaia | tess | main sequence | passes |
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
and Gaia agreed with the surveys every time. One, NSVS 2887379, passed through a 2:1
Agreement: ZTF r settled at half the period while the other three found the whole.

**Both alias traps resolved to the true period.** PS1-3PI J190107.42+492456.3 is an RR
Lyrae star VSX lists at 0.46287 days; the pipeline found 0.31617, the period VSX's value
is a 1-day alias of, from ZTF g, ZTF r and Gaia. CSS_J181701.4+514940 is the case in
chapters 2 and 3. These test the one thing the bulk imports could not do: choose the
right peak when the tallest one is wrong.

**Both blends reached no Agreement and were flagged.** Every ground-based survey of each
was flagged by the neighbour within 1.5″, and three half-weight votes fall short of two.
These test whether the Gaia neighbour list reaches the pipeline and is used.

**The three constant stars cast no votes at all.** Each ZTF band's best fold scored 0.82
to 0.99, above the 0.75 cut-off, so no period was offered and nothing could agree. These
test whether the pipeline can say *nothing here*, which the bulk imports also could not.

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
TESS: two full votes and a half.

One caveat belongs with that result. Five of the fields that carried the old flags, with
median offsets of 0.10 to 0.15 mag and the ROT star's 0.096, have only 2 to 9 points in
the fixtures, which are trimmed to 500 points a band. A median of that few points of a
variable star is mostly luck, but at equal phase so few points are not enough to judge
either, so those fields are recorded as *unmeasured* and do not flag. The ROT star's ZTF g
field is one of them (8 points), so its ZTF g vote counts in full because that field could
not be judged, not because it was judged clean. On the full Light Curves the pipeline will
see in production, the same fields hold several times as many points and will be
measured.

## The thresholds were tuned on these 27 stars

Several numbers in the pipeline are not derived from first principles. They were set
by looking at these stars, and the margins are thin:

| Threshold | Value | What set it | Margin on the 27 |
| --- | ---: | --- | --- |
| Fold scatter above which a band does not vote | 0.75 | The constants' ZTF folds scored 0.82 to 0.99; every voting band of a positive scored 0.71 or less | One band at 0.71 (a δ Scuti star in ZTF r) and one at 0.82: a star either side would move the line |
| Period tolerance | 0.1 %, or P / baseline | ZTF g and r came out 0.10 to 0.17 % apart on the same 40-day peak under a fixed 0.1 % rule | Set by two semiregulars |
| Neighbour radius and brightness for `blend_suspect` | 3″, brighter than G + 2 | VSX's contamination radii, widened for ZTF's seeing and Gaia's BP/RP windows | Both blends have neighbours at 1.2 and 1.5″; nothing tests a neighbour at 2.9″ |
| ZTF zero-point offset, at equal phase | 0.05 mag | Spec, by assertion; measured at equal phase since NEU-1798 | Fires on none; the largest measured offset on a positive is 0.037 mag (the ROT star's ZTF r) |
| TESS field brightness | G + 1, or half the Target's light combined | Spec, by assertion | Fires on 25 of 27 |
| Points needed to search a band | 30 | Spec, by assertion | V0364 Dra's Gaia bands have 32 to 41 |

The gate is a regression test: it says the pipeline has not got worse on these stars. It
is not a measurement of how often the pipeline is right on a star it has never seen. Every
number above may move when ASAS-SN arrives, and will have to be re-examined on the first
real [Slice](../../CONTEXT.md#selection), where there will be stars of types and periods
the fixtures do not contain.

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
- **Anything ASAS-SN would say.** Until NEU-1796, every Agreement here rests on at most
  four sources, and the V-band range VSX prefers is not available.

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
