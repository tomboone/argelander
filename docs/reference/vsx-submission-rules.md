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

## From the VSX FAQ (captured 2026-10-19)

Source: `index.php?view=about.faq` on vsx.aavso.org, read the same day.

### Survey references, exactly as VSX wants them

"Always add the survey reference when you use their data. Also the star identifier(s)
in that survey should be added to the Other names field."

| Survey | Reference name | Bibcode |
| --- | --- | --- |
| ZTF | Masci, F. J.; et al., 2019, The Zwicky Transient Facility: Data Processing, Products, and Archive | `2019PASP..131a8003M` |
| ASAS-SN | Kochanek, C. S.; et al., 2017, The All-Sky Automated Survey for Supernovae (ASAS-SN) Light Curve Server v1.0 | `2017PASP..129j4502K` |
| Gaia DR3 photometry | Gaia collaboration; et al., 2022, Gaia Data Release 3 (Gaia DR3) Part 1 Main source | `2022yCat.1355....0G` |
| Gaia DR3 variables | Gaia collaboration; et al., 2022, Gaia Data Release 3 (Gaia DR3) Part 4 Variability | `2022yCat.1358....0G` |
| TESS | Ricker, G. R.; et al., 2014, Transiting Exoplanet Survey Satellite (TESS) | `2014SPIE.9143E..20R` |
| TESS QLP | Huang, C. X.; et al., 2020, Photometry of 10 Million Stars from the First Two Years of TESS Full Frame Images: Part I | `2020RNAAS...4..204H` |
| APASS | Henden, A. A.; et al., 2018, AAVSO Photometric All Sky Survey (APASS) DR10 | `2018AAS...23222306H` |
| Pan-STARRS1 | Chambers, K. C.; et al., 2016, The Pan-STARRS1 Surveys | `2016arXiv161205560C` |

The FAQ also lists ASAS-3, ATLAS, CRTS, HIPPARCOS, K2, NSVS and SuperWASP; not needed for
v1. The revision form takes one reference; add the others by saving a draft and editing.

### Phase plots

- Show more than one cycle. Pulsators: phase 0 to 2 so the maximum is in the middle.
  Eclipsing binaries: phase −0.25 to 1.25 (or −0.5 to 1.5) so the secondary eclipse is in
  the middle.
- Phase 0 must be the epoch given in the table.
- Epoch of **maximum** for most pulsators (exceptions: RV Tau and SR stars showing long
  secondary periods) and for hot-spotted stars; epoch of **minimum** for eclipsing
  binaries, ellipsoidal variables and dark-spotted (ROT) stars.
- A JD light curve is the attachment for irregular variables; a finding chart for
  crowded fields or stars without reliable cross-identifications.

### Magnitude range

- Take scatter into account and exclude outliers. Use **mean** magnitudes at maximum and
  minimum read from the phase plot for strictly periodic stars.
- For stars whose mean level or amplitude changes between cycles (SR, M, RS …), give the
  mean magnitude of the brightest and of the faintest recorded cycles.
- Survey magnitudes are not to be taken at face value: check VizieR for companions
  inside each survey's aperture, correct the range if needed (VSX offers a COMBLEND
  spreadsheet), and say so in Remarks, e.g. `ASAS-3 magnitudes contaminated by
  GSC 00000-00000 (V= 14.5; sep. 17"). Range has been corrected.`
- V magnitudes for the mean come from APASS below V ≈ 10, ASAS-3 for brighter southern
  stars; transformed values (UCAC3, CMC15, SDSS, Pan-STARRS1) are acceptable when nothing
  else exists. "clear" is never a passband for the maximum field.

### Remarks and Revision comment

- Remarks hold additional information on the star or photometry only. **No URLs or
  references** (they belong in the Reference fields) and **nothing that belongs in a
  table field** (an improved position replaces the table value; it is not a remark).
- Revision comment lists every change made and the source for each, e.g.
  `Type, period, epoch and range from ASAS-3 data. UCAC4 position.`

### Form hygiene

- Provide as much as possible; always a range (or mean + amplitude), always the
  passband, always a classification (mark Uncertain if unconfirmed).
- Do **not** mark Uncertain on an empty field. Do **not** fill fields irrelevant to the
  object (no nova year for a non-nova).
- Identifier formats: `J` before coordinate-based ids (`2MASS J23171981+3102538`); GSC
  uses leading zeros, Tycho does not (`GSC 01800-02202`, `TYC 1800-2202-1`). Check the
  CDS Dictionary of Nomenclature for anything else. Faint stars get survey-era ids
  (2MASS, UCAC4, GSC2.3, Gaia), not classical catalogue names.
- Discovery credit: if a star is published in VizieR but not in VSX, the discoverers are
  credited; a wrongly classified published star lets the submitter add themselves as
  co-discoverer. Our revisions of existing VSX entries do not touch Discoverer.
- Help: the AAVSO VSX Forum (needs an aavso.org website account, which Tom has).
- AUIDs are not needed for revisions.

## Implications recorded in the spec on 2026-10-19

- M3 submits one revision, waits for approval, then the remaining one or two.
- `submission/form.py` converts the epoch from BJD_TDB to HJD.
- Magnitude range is reported in V where ASAS-SN V data exist; otherwise a Gaia G or
  APASS mean plus amplitude in the band observed, band always stated.
- Blend checks use per-survey contamination radii (ZTF 2″, ASAS-SN 17″, TESS 120″),
  so the Gaia neighbour query is widened and TESS gets its own contamination flag.
- The tracker warns when a `submitted` item awaiting modification approaches 60 days.
- Plots include a combined multi-survey light curve with surveys distinguishable.
- Phase plots are laid out per type (0–2 for pulsators, −0.25–1.25 for eclipsers) with
  phase 0 at the table epoch; the epoch convention (max vs min) follows the type.
- Proposal max and min magnitudes are means from the binned phase curve with outliers
  excluded, not extreme points; SR/M use the brightest and faintest cycle means.
- Survey references are constants in the form renderer; Remarks never carry references
  or table values; the Revision comment is generated from the list of changed fields and
  their sources.
