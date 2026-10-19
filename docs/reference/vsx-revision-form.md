# VSX "Submit Revision" form, as captured 2026-10-19

Source: the HTML of the revision form for an existing star, copied by Tom from
vsx.aavso.org (`index.php?view=modify.stage`, hidden `oid` = the VSX OID) on
2026-10-19. This is the form Argelander's Submission values are pasted into. The New
Star form differs (two uploads, Spot check and Check name links) and is not used by
this project. Re-capture if the live form changes.

"Red bar fields are required": Primary name, Position, Revision comment. Everything
else is optional on a revision, but the supporting file is "required for unpublished
data", which is what our analyses are.

## Fields in form order

| Label | `name` | Input | Notes and examples |
| --- | --- | --- | --- |
| Primary name | `designation` | text, **required** | Pre-filled. "Primary name revisions require additional confirmation." Never change it. |
| Position (J2000.0) | `coords` | text, **required** | Pre-filled. Examples: `21 42 42.8 +43 35 10`, `07:04:04 -03:50:51`, `34.837 -2.978`. Existing record used `10 07 10.08 -40 28 16.7` (RA s to 2 dp, Dec ″ to 1 dp). |
| Std. variable type | `varType` | select | Fixed vocabulary below; the current value is appended if not in the list (e.g. `EA+ELL`). Links to "Variability types". |
| Cust. variable type | `custVarType` | text | For compound or non-listed types. "Use accepted variable type abbreviations whenever possible." |
| (type) Uncertain | `varTypeUnc` | checkbox | VSX's `:` suffix. |
| Spectral type | `specType` | text | Examples `G9.5V+K3V`, `SC2-S4e`, `M7e`. Plus `specTypeUnc` checkbox. Leave as pre-filled. |
| Maximum magnitude | `magMax` | text | "The magnitude measurement of the star at its brightest." Preceded by `maxLimFront` select: `--`, `< (fainter than)`, `> (brighter than)`. Plus `maxUnc` checkbox. |
| Max. mag. passband | `maxband` | select | Passband vocabulary below. |
| Custom max. passband | `custmaxband` | text | For bands not in the list (e.g. `TESS`). |
| Minimum magnitude | `magMin` | text | "The magnitude measurement of the star at its faintest." Preceded by `minLimFront` select as above. Plus `minUnc` checkbox. |
| Min. mag. passband | `minband` | select | Same vocabulary. |
| Custom min. passband | `custminband` | text | |
| Minimum is amplitude | `minAmp` | checkbox | When checked, `magMin` is an amplitude, not a magnitude. This is the "catalogue mean + amplitude" mode from the submission notice. |
| Period | `period` | text, days | Plus `periodUnc` checkbox. |
| Epoch | `epoch` | text, **HJD** | Plus `epochQual` "Uncertain" checkbox. |
| Rise/eclipse duration | `riseDur` | text, % | Percent of period. Plus `riseDurUnc`. |
| Outburst (nova) year | `novaYear` | text | Not used by this project. |
| Other names | `other` | textarea | "One catalog designation per line." Examples `2MASS J19595804-2258147`, `GSC 06892-01916`, `ASAS J195958-2258.3`. |
| Supporting file | `file1` | file, **1 MB max** | "Required for unpublished data. Optional for previously published data." One file on this form. |
| File title | `file1title` | text | |
| File description | `file1desc` | textarea | |
| Mark as private | `file1priv` | checkbox | |
| Discoverer | `discoverer` | text | "Used only for discovery credit on Detail Sheet." Leave as pre-filled. |
| Reference name | `xrefName` | text | "Required for previously published data. Optional for unpublished data." Examples `The All Sky Automated Survey`, `OEJV #43`. |
| Reference bibcode | `xrefCite` | text | Examples `2006AAS...208.4405P`, `1978A&A....65..363B`. Has a "Check bibcode" link. |
| Reference link | `xrefURL` | text | "Leave empty when there is a bibcode or if there is no online resource." |
| Remark | `remarks` | textarea | "Appears in Remarks section of Detail Sheet." Examples `Min II = 8.51 V.`, `Other period 93 d.`, `Also a DSCT variable with P=0.053 d.` |
| Revision comment | `comment` | textarea, **required** | "Appears in Revision History section of Detail Sheet." Examples `Type and range from NSVS data.`, `Elements from 2016JAVSO..44..156K.`, `UCAC4 position.` |
| Draft only | `draft` | checkbox | Save without submitting; drafts can be edited to add files and references. |

Footer notes on the form: input is checked for errors with a chance to correct; all
submissions must be approved before appearing; moderators may edit for accuracy and
clarity; network address and user are logged; VSX is not a permanent repository for
uploaded documents.

## Passband vocabulary (`maxband` / `minband`)

`U` (Johnson), `B` (Johnson), `V` (Johnson), `Rc` (Cousins), `Ic` (Cousins),
`u` (Sloan), `g` (Sloan), `r` (Sloan), `i` (Sloan), `z` (Sloan), `J` (2MASS),
`H` (2MASS), `Ks` (2MASS), `R1` (ROTSE-I), `G` (Gaia), `Kp` (Kepler magnitude),
`CV` (Unfiltered with V zero point), `CR` (Unfiltered with R zero point).

`TESS` appeared only because the existing record used it; it is entered through the
custom passband box.

Argelander mapping: ZTF g → `g`, ZTF r → `r`, Gaia G → `G`, ASAS-SN V → `V`,
ASAS-SN g → `g`, TESS → custom `TESS` (and only with "Minimum is amplitude", since
TESS flux is relative).

## Variability type vocabulary (`varType`)

`*`, `ACEP`, `ACEPS`, `ACV`, `ACYG`, `AGN`, `AM`, `BCEP`, `BCEPS`, `BE`, `BLAP`,
`BLLAC`, `BXCIR`, `BY`, `CBSS`, `CBSS/V`, `cPNB[e]`, `CPV`, `CST`, `CTTS`, `CTTS/ROT`,
`CV`, `CWA`, `CWB`, `DCEP`, `DCEP(B)`, `DCEPS`, `DCEPS(B)`, `DIP`, `DPV`, `DQ`, `DSCT`,
`DWLYN`, `DYPer`, `E-DO`, `EA`, `EB`, `EC`, `ELL`, `EP`, `EW`, `EXOR`, `FF`, `FKCOM`,
`FSCMa`, `FUOR`, `GCAS`, `GDOR`, `HADS`, `HADS(B)`, `HB`, `HMXB`, `IBWD`, `L`, `LB`,
`LC`, `LERI`, `LFBOT`, `LMXB`, `LPV`, `M`, `Microlens`, `N`, `NA`, `NB`, `NC`, `NL`,
`NL/VY`, `NR`, `PPN`, `PSR`, `PVTEL`, `PVTELI`, `PVTELII`, `PVTELIII`, `QSO`, `R`,
`RCB`, `roAm`, `roAp`, `ROT`, `RRAB`, `RRC`, `RRD`, `RS`, `RV`, `RVA`, `RVB`, `S`,
`SDOR`, `SN`, `SN I`, `SN Ia`, `SN Ia-86G-like`, `SN Iax`, `SN Ib`, `SN Ic`, `SN Ien`,
`SN II`, `SN II-L`, `SN II-P`, `SN IIa`, `SN IIb`, `SN IId`, `SN IIn`, `SN-pec`, `SPB`,
`SR`, `SRA`, `SRB`, `SRC`, `SRD`, `SRS`, `SXARI`, `SXPHE`, `SXPHE(B)`, `Transient`,
`TTS`, `TTS/ROT`, `UG`, `UGER`, `UGSS`, `UGSU`, `UGWZ`, `UGZ`, `UV`, `UVN`, `UXOR`,
`V1093HER`, `V361HYA`, `V838MON`, `VAR`, `VBD`, `VY`, `WCO/E`, `WCP/E`, `WR`, `WTTS`,
`WTTS/ROT`, `WWC`, `WWC/E`, `X`, `YHG`, `YSO`, `ZAND`, `ZZ`, `ZZA`, `ZZA/O`, `ZZB`,
`ZZLep`, `ZZO`.

Every code in Argelander's initial rule set (`EW`, `EA`, `EB`, `RRAB`, `RRC`, `DSCT`,
`HADS`, `DCEP`, `SR`, `L`, `M`, `ROT`, `ELL`) is in this list. `CST` (constant) is the
code for "not variable". Compound types such as `EA+ELL` or `EW/RS` go in the custom box.

## What this fixes in the design (2026-10-19)

- `FormFields` emits each magnitude as a value plus a passband code from the vocabulary
  above, plus the `minAmp` flag, rather than a "14.21 g (ZTF)" string.
- The attached plot is **one composite PNG under 1 MB** (`submission.png`: phase plot
  above, combined multi-survey light curve below). Additional images only via a draft.
- `Proposal` gains `uncertain` (maps to `varTypeUnc`, set when Confidence is below a
  threshold) and `eclipse_duration_pct` for eclipsers (maps to `riseDur`).
- `Revision comment` is generated: one sentence naming the surveys used and what
  changed, e.g. `Type and elements from ZTF, ASAS-SN and TESS data; Gaia DR3 period is
  the 1-day alias.`
- `Other names` lists the Gaia DR3, ZTF and ASAS-SN designations, one per line.
- Position is formatted `HH MM SS.ss ±DD MM SS.s` from the Gaia DR3 coordinates.
- Reference: for our own unpublished analysis the supporting file is the requirement;
  `xrefName`/`xrefCite` cite the primary survey when a bibcode exists.
