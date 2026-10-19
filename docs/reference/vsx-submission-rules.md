# VSX submission rules that bind this project

Source: the one-time notice shown to a new VSX user before their first submission
("Submitting Variables to VSX"), read and accepted by Tom on 2026-10-19 at
vsx.aavso.org. The full VSX Manual (PDF) is linked from that page. This file keeps
the rules that constrain Argelander's design; it is not the whole notice. Re-read the
live page before changing anything here.

## Rate limits and lifecycle

- 5 submissions per week per user.
- **1 submission only for a new user until the first one is approved.**
- Fewer than 5 draft submissions before another object can be revised.
- A submission that still needs modifications after 60 days is deleted by the system,
  with a notice one week before. Drafts older than 60 days are deleted the same way.
- Moderators may edit any submission for clarity or convention and will usually tell
  the submitter.

## What a data-mined revision must contain

- Position: J2000 from Gaia EDR3/DR3 as given by VizieR (sub-arcsecond; anything over
  1″ error is discouraged).
- Survey identifier of the object (e.g. the ZTF or ASAS-SN id) alongside catalogue
  cross-ids in their canonical spelling (`GSC 01234-06789`, `2MASS J11431012-5804040`,
  `UCAC4 810-003941`).
- Variability type, maximum and minimum magnitude, and for periodic variables the
  period and epoch.
- **Epoch is HJD.** Time of minimum for eclipsers, time of maximum for pulsators.
  Survey times in MJD must be converted (and heliocentrically corrected).
- Magnitudes **preferably in V**. If a calibrated max and min in one standard band is
  not possible, give a catalogue mean magnitude (e.g. V from APASS or a transformed
  catalogue value) plus the observed amplitude with its band stated.
- Maximum magnitude means brightest (smallest number); minimum means faintest.
- References: a bibcode when one exists (no link in that case); otherwise a URL.
- Anything that does not fit a standard field goes in Remarks (secondary period,
  secondary minimum depth, etc.).
- Data-mined submissions are held to a higher standard than imported catalogues: the
  submitter is expected to have done the period analysis, not to repeat a survey's
  own values.

## Evidence attachments

- Do not attach light curves taken from survey websites, and do not attach raw data;
  VSX does not archive data.
- Provide phase plots for periodic variables, or a combined light curve from several
  surveys in one plot with each survey distinguishable.
- The New Star form accepts two uploads; save as a draft to add more.
- Use the Spot check link on the position and the Check name link on the primary name
  to avoid duplicates. Use the "Draft only" checkbox until ready.

## Survey handling rules

- ZTF: use only `catflag 0`. Useful range g/r 13–21 mag. Aperture contamination ~2″.
- TESS: prefer PDCSAP, but watch for over-correction that deletes real features or
  mean-level changes; use SAP in those cases. Useful range ~2–15 mag. Aperture
  contamination ~120″ (variable); VSX calls TESS "useless in most fields due to
  blending".
- ASAS-SN: useful range V 10–16.5 mag. Aperture contamination ~17″.
- Gaia: useful range G 5–21 mag. Contamination ~0.7″.
- Other radii from the notice: APASS ~12″, Pan-STARRS1 ~3″, ATLAS ~7″, CSS ~9″,
  SuperWASP ~60″, NSVS ~55″, ASAS-3 ~23″.
- Stars closer than ~60″ may be blended in some surveys; pick the most probable
  identification by colour and magnitude.
- Submissions based only on Tycho photometry are not acceptable.

## Scope reminders

- VSX is a catalogue of Galactic variable stars; supernovae and quasars are normally
  excluded. (Relevant to the later cross-catalog audit work.)
- More than ~10 objects from one source: email vsx at aavso.org with a tabular list
  rather than submitting individually.
- Systematic searches producing many discoveries should go to a journal (JAAVSO, PZP)
  rather than one-by-one submissions.

## Implications recorded in the spec on 2026-10-19

- M3 submits one revision, waits for approval, then the remaining one or two.
- `submission/form.py` converts the epoch from BJD_TDB to HJD.
- Magnitude range is reported in V where ASAS-SN V data exist; otherwise a Gaia G or
  APASS mean plus amplitude in the band observed, band always stated.
- Blend checks use per-survey contamination radii (ZTF 2″, ASAS-SN 17″, TESS 120″),
  so the Gaia neighbour query is widened and TESS gets its own contamination flag.
- The tracker warns when a `submitted` item awaiting modification approaches 60 days.
- Plots include a combined multi-survey light curve with surveys distinguishable.
