# VSX type definitions that the rule set encodes

Source: "Variable Star Type Designations in VSX" (Otero, Watson, Wils), the live
`about.vartypes` page on vsx.aavso.org, read 2026-10-19. It is the GCVS type document
with VSX's additions. This file extracts the numbers and shape statements that
`classify/types.py` and the rules in `classify/rules/` must agree with. Period is in days,
amplitude in V unless stated. "Usually" and "mainly" in the source are kept as soft
bounds; hard bounds are the source's explicit ranges.

Notation: `:` uncertain; `|` logical OR (`ELL|DSCT`); `+` logical AND (`ELL+DSCT`);
`/` subtype (`EA/SD`). Compound types go in the form's custom-type box.

## The thirteen target types plus CST and VAR

| Code | Group | Period | Amplitude | Shape and other discriminators |
| --- | --- | --- | --- | --- |
| `EA` | eclipsing | 0.2 to ≥ 10 000 | up to several mag | Beginning and end of eclipses identifiable; light nearly constant between eclipses; secondary minimum may be absent. Only type for which the form's eclipse duration applies. |
| `EB` | eclipsing | mainly > 0.5 | usually < 2 | Continuous variation between eclipses (contact times not identifiable); secondary minimum **always present** and considerably shallower than primary; components early type (B–A). |
| `EW` | eclipsing | usually < 1 | usually < 0.8 | Continuous variation; primary and secondary minima of almost equal depth; components F–G and later. |
| `ELL` | rotating | = orbital | usually ≤ 0.1, up to 0.3 | No eclipses; ellipsoidal light change at the orbital period (two near-equal humps per orbit, so the photometric period is P/2 if treated as sinusoidal). |
| `ROT` | rotating | (not stated) | (not stated) | Catch-all for spotted stars not placed in BY, RS, ACV, FKCOM etc. Shape changes between seasons. Use BY's ranges as guidance: fraction of a day to 120 d, several hundredths to 0.5 mag. |
| `RRAB` | pulsating | 0.3 to 1.0 | 0.5 to 2 | Asymmetric, steep ascending branch; fundamental mode. Epoch of maximum. Rise duration required by the form. |
| `RRC` | pulsating | 0.16 to 0.5 | ≤ 0.8 | Nearly symmetric, sometimes sinusoidal; first overtone. Epoch of maximum. |
| `DSCT` | pulsating | 0.01 to 0.2 | 0.003 to 0.9 | A0–F5 III–V; shapes, periods and amplitudes vary, often multiperiodic. Those with amplitude > 0.15 **and** asymmetric curves are HADS instead. |
| `HADS` | pulsating | 0.01 to 0.2 (inherits DSCT) | > 0.15 | Radial pulsators with asymmetric curves (steep ascending branch). Rise duration required by the form. |
| `DCEP` | pulsating | 1 to 135 (CEP range) | several hundredths to 2 | Classical Cepheid, fundamental mode; F at maximum, G–K at minimum; period–luminosity relation (so Gaia absolute G is a strong check). Shape correlates with period. |
| `SR` | pulsating | 20 to > 2000 | several hundredths to several mag, usually 1–2 | Giants or supergiants of intermediate and late type; noticeable periodicity interrupted by irregularities. Subtypes: SRA persistent periodicity, < 2.5 mag, 35–1200 d; SRB poorly defined cycles 20–2300 d; SRS short periods, days to a month. |
| `L` | pulsating | none | (not stated) | Slow irregular; no periodicity or only occasionally. LB late-type giants, LC late-type supergiants (~1 mag). Many L stars are really SR. |
| `M` | pulsating | 80 to 1000 | 2.5 to 11 | Mira; late-type emission spectra (Me, Ce, Se); well-pronounced periodicity. The 2.5 mag amplitude is the SR/M boundary. |
| `CST` | other | none | none | Constant; formerly suspected variable, not confirmed. Our "not variable" verdict. |
| `VAR` | other | any | any | Variable of unspecified type; suspected variables lacking deeper study. The Target population and the fallback Proposal when Agreement fails. |

Also in the pulsating group and worth a rule each because they share our period range:

| Code | Period | Amplitude | Why it matters |
| --- | --- | --- | --- |
| `DCEPS` | ≤ 7 | < 0.5 | First-overtone Cepheid with almost symmetric curve (M−m ≈ 0.4–0.5 periods). A low-amplitude symmetric 1–7 d pulsator is DCEPS, not DCEP. |
| `CWB` / `CWA` | < 8 / > 8 (CW overall 0.8–35) | 0.3 to 1.2 | Type II Cepheids, 0.7–2 mag fainter than DCEP at the same period; humps on descending branch. Gaia parallax separates them from DCEP. |
| `ACEP` | 0.6 to 3 | large, RRAB-like | Anomalous Cepheid, rise duration < 25 %; ~1 mag brighter than CW at the same period. |
| `GDOR` | 0.25 to 4 | usually ≤ 0.1 (HAGDOR to 0.4) | A7–F7 dwarfs, multiperiodic g-modes. Low-amplitude 0.25–4 d candidates are GDOR before they are ROT. |
| `SPB` | 0.4 to 5 | < 0.1 | B2–B9 main sequence; blue colour separates from GDOR. |
| `BCEP` | 0.1 to 0.6 | 0.01 to 0.3 | O8–B6 non-supergiants; colour separates from DSCT. |
| `SXPHE` | 0.04 to 0.08 | up to 0.7 | Old-population HADS look-alike; high galactic latitude and parallax (subdwarf) hint. |
| `RRD` | P0 ≈ 0.5, ratio 0.74 | | Double-mode RR Lyrae; two peaks in the periodogram with ratio 0.74 (or 0.80). |
| `BLAP` | 20–40 min (0.014–0.028 d) | 0.2 to 0.4 | Blue, RRAB-shaped; outside our period floor but inside DSCT-like searches if the grid goes short. |

And in the rotating and eclipsing groups:

| Code | Period | Amplitude | Why it matters |
| --- | --- | --- | --- |
| `BY` | fraction of a day to 120 | several hundredths to 0.5 | Spotted dKe–dMe dwarfs; red colour. The most common real identity of a `ROT` proposal. |
| `RS` | (orbital) | wave up to 0.5 | Late F–K giants with migrating distortion wave; often eclipsing (`E/RS`). |
| `ACV` | 0.5 to 160 | 0.01 to 0.1 | B8p–A7p; blue, low amplitude, strictly periodic. |
| `HB` | (orbital) | usually < 0.01, up to 0.3 | Heartbeat stars; periastron spike shape. |
| `EP` | | | Planetary transits: EA-like but tiny depth and no secondary. |
| `R` | = orbital | up to 1 | Reflection effect: sinusoidal at the orbital period, maximum when the hot star is in front. |

## Alias and confusion traps the document names

- ASAS-3 `DSCTr` stars "are actually contact binaries with twice the period given". A
  DSCT-looking signal at 0.1–0.2 d whose doubled period gives two unequal (or equal) minima
  is an EW at 2P. This is the P-vs-2P test in `analysis/aliases.py`.
- `ELL|DSCT`: an ellipsoidal binary or a DSCT "with half the given period". Same test from
  the other side.
- OGLE `SIN` includes ELL at half the orbital period and chromospherically active stars.
- `SR` vs `M`: amplitude 2.5 mag in V. `SR` vs `L`: whether any periodicity is found.
- `DSCT` vs `HADS`: 0.15 mag and asymmetry. `RRC` vs `DSCT`: period above 0.16 d and the
  RR Lyrae colour/absolute magnitude (horizontal branch). `HADS` vs `RRC` near 0.16–0.2 d:
  asymmetry (HADS steep rise) vs near-sinusoid (RRC).
- `DCEP` vs `CW`: same period range, Type II Cepheids 0.7–2 mag fainter; use Gaia parallax
  when significant. `DCEP` vs `DCEPS`: amplitude 0.5 mag and symmetry.
- `EW` vs `EB` vs `EA`: equality of minima (EW), presence and relative depth of the
  secondary (EB), and whether contact times can be read (EA). An `EW` with an unequal
  secondary is `EB`; a continuously varying curve is never `EA`.
- `ELL` requires absence of eclipses; a small-amplitude double-humped curve with a
  flat-bottomed dip is `EW` or `EB`, not `ELL`.

## Epoch and rise-duration conventions by target type

| Code | Epoch is time of | Form `riseDur` |
| --- | --- | --- |
| EA | primary minimum | eclipse duration, first to last contact, % of period |
| EB, EW | primary minimum | blank |
| ELL, ROT | minimum | blank |
| RRAB, RRC, DCEP, DCEPS, HADS | maximum | rise duration minimum → maximum, % of period (required for RRAB, DCEP, HADS; harmless for RRC) |
| DSCT | maximum | blank (sinusoidal) |
| SR, M | maximum | blank; epoch only when periodicity is well defined |
| L, CST, VAR | none | blank |

## Implications for `classify/types.py`

- One `TypeSpec` per code with `period_range`, `amplitude_range`, `asymmetric: bool | None`,
  `secondary_minimum: str` (`absent-ok`, `required-shallower`, `equal`), `colour_hint`
  (blue / intermediate / red / any), `epoch_at` (`max` / `min` / `none`) and
  `rise_duration_rule` (`eclipse`, `rise`, `none`).
- Soft bounds ("usually", "mainly") carry a weight of about 0.5 in the corresponding
  rule; hard bounds carry 1.0. Values outside a hard bound oppose the type outright.
- The confusion classes above are scored too, so a Proposal that fits `GDOR` better than
  `ROT` says so in its evidence even though `GDOR` is not in the initial rule set; the
  best-scoring type is proposed only if it is in the set, otherwise the Proposal is `VAR`
  with the evidence attached.
