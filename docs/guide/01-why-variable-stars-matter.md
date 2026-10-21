# 1. Why variable stars matter

Most stars shine at a steady brightness for longer than humans have been watching. A
**variable star** is one whose brightness changes, by anything from a thousandth of a
magnitude to a factor of thousands, over anything from minutes to years. Friedrich
Argelander, whose name this project borrows, set up the first systematic programme for
watching them in the 1840s and invented the naming scheme (R Draconis, S Draconis, …)
still in use.

## Magnitudes, briefly

Astronomers measure brightness in **magnitudes**, a scale inherited from the Greeks in
which *smaller numbers are brighter*. The brightest stars are magnitude 0 or 1; the
faintest the eye can see are about 6; each step of 5 magnitudes is a factor of 100 in
brightness. Argelander's first [Slice](../../CONTEXT.md#selection) is Draco at magnitude
13 to 16: stars a hundred to a thousand times too faint for the eye, but comfortably
within reach of the sky surveys in chapter 3.

Two consequences of the scale run through every plot in this guide. Every magnitude axis
is drawn upside down, so that *up* means *brighter*. And a change of 0.1 magnitude is
about 10 % in brightness, so a 0.5-magnitude eclipse is a star losing 40 % of its light.

## What makes a star vary

Three mechanisms account for nearly every star Argelander will meet.

**Pulsation.** Some stars are unstable in a particular way: their outer layers swell,
cool and dim, then fall back, heat and brighten, over and over, like a bell that keeps
ringing. The rhythm is set by the star's size and density, so stars of a kind share a
period. The kinds matter in chapter 3 and 4:

- **Cepheids** (VSX type `DCEP`): bright yellow giants with periods of 1 to 100 days.
- **RR Lyrae stars** (`RRAB`, `RRC`): old, fainter pulsators with periods of 0.2 to 1 day.
  `RRAB` curves rise steeply and decline slowly; `RRC` curves are nearly sinusoidal.
- **δ Scuti stars** (`DSCT`, `HADS`): small-amplitude pulsators with periods under 0.3 days.
- **Semiregular and Mira variables** (`SR`, `M`): huge, cool red giants that pulse over
  tens to hundreds of days, not always regularly.

**Eclipses.** About half of all stars are in binaries, and when the orbit is edge-on to us
each star periodically passes in front of the other. The light dips twice per orbit: a
deeper *primary* eclipse and a shallower *secondary*. The shape tells how close the stars
are:

- `EA` (Algol type): the stars are well separated, so the light is flat between two sharp
  dips.
- `EB` (β Lyrae type): the stars are close enough to be stretched, so the light curves
  continuously, with unequal dips.
- `EW` (W Ursae Majoris type): the stars touch and share an envelope, so the light curves
  continuously with two nearly equal dips. These are the commonest variables at 13–16
  mag, and they are at the centre of the alias problem in chapter 2.

**Rotation.** A star with a large dark spot, or a bright one, brightens and dims as the
spot rotates in and out of view (`ROT`). A close binary whose stars are stretched into
ellipsoids shows a double wave as the long and short sides turn towards us (`ELL`), even
without eclipses.

A fourth group, eruptive and cataclysmic stars that flare or outburst, is not periodic in
the same way and is outside what this project proposes.

## What a period tells astronomers

The **period** is the time for one full cycle, in days, and it is the single most useful
number about a variable star.

For pulsators the period is tied to the star's true brightness. Henrietta Leavitt found
in 1912 that Cepheids with longer periods are intrinsically brighter, and the relation is
tight enough that measuring a Cepheid's period gives its true luminosity; comparing that
with how bright it looks gives its distance. Cepheids are how the distance to nearby
galaxies was first measured and still anchor the scale on which the expansion of the
universe is measured. RR Lyrae stars all have nearly the same true brightness, so they do
the same job for old star clusters and the structure of our own Galaxy. A wrong period
means a wrong distance.

For eclipsing binaries the period is the orbital period. Together with the eclipse
shapes and a spectrum, it gives the masses and radii of both stars directly, which is the
only way those are measured for most kinds of star, and is what the models of how stars
age are tested against.

For rotating stars the period is the rotation period, which slows as a star ages, so it
is a clock.

In each case the period is only meaningful alongside the **type**. An `EW` binary with a
0.31-day orbit and an `RRC` pulsator with a 0.31-day cycle are different objects whose
light curves can look alike, and the physics applied to each is different. The published
type is what tells a researcher which physics to apply, and the published period is what
they apply it to.

## Who uses VSX

The **AAVSO International Variable Star Index**, VSX, is the catalogue of record for
variable stars: one entry per star, with its names, position, type, period, epoch,
magnitude range and the references the values came from. It holds more than two million
entries and is maintained by the American Association of Variable Star Observers, with a
small group of volunteer moderators who check every entry and every change.

Its users are observers planning what to watch, researchers cross-matching their own
discoveries against what is known, and survey teams deciding whether a star they found is
new. For all of them VSX is the place where a star's identity and its headline numbers are
settled. An entry that gives the wrong period or type is wrong for everyone downstream.

## What a revision is

Anyone registered with VSX can propose a change to an entry through its revision form:
new values for any field, the evidence for them, and a comment explaining the change. A
moderator reads it and either accepts it, so the entry changes, or rejects it with a
reason. VSX's rules for what a revision must contain are captured in
[`docs/reference/vsx-submission-rules.md`](../reference/vsx-submission-rules.md); the main
ones are that the epoch is given in HJD, the magnitude range names its passband, the
plots attached are the submitter's own rendering of the data and never a survey website's,
and that a new user has one submission open at a time until their first is approved.

In Argelander's vocabulary the pipeline produces a
[Proposal](../../CONTEXT.md#proposals-and-review): the classifier's proposed revision, with
a type, a period, an epoch, a magnitude range and a
[Confidence](../../CONTEXT.md#proposals-and-review),
resting on an [Analysis](../../CONTEXT.md#analysis). Tom's
[Review](../../CONTEXT.md#proposals-and-review) accepts, edits or rejects it. An accepted
Proposal becomes a [Submission](../../CONTEXT.md#submission): the form's field values,
the reference text and the plot, entered by hand. The moderator's answer is the
[Verdict](../../CONTEXT.md#submission). No code ever touches the VSX form
([ADR 0001](../adr/0001-manual-vsx-submission.md)).

The next chapter is about why entries end up wrong in the first place.
