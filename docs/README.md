# docs/

| Path | What it is | When to read it |
| --- | --- | --- |
| `handover.md` | The settled design from the planning conversation (2026-10-19), with a "Decisions since handover" section and a "Submission constraints" section updated after VSX registration. | Before changing scope or architecture. |
| `specs/argelander-initial-buildout-project-spec.md` | Project-wide spec: package layout, Postgres schema, LightCurve contract, per-survey fetch rules, analysis order, classifier, plots, review app routes, submission tracker, infrastructure, testing, conventions. Every ticket is implemented from it. | Before implementing any ticket. |
| `plans/` | Per-ticket plans written by `/planit` when the project spec does not cover a ticket well enough. Named `<TICKET>-<slug>.md`. | When a ticket has one. |
| `adr/0001-manual-vsx-submission.md` | No code ever touches the VSX web forms. | Before adding anything that posts to aavso.org. |
| `adr/0002-postgres-job-queue.md` | `SELECT … FOR UPDATE SKIP LOCKED` is the queue; no Redis. | Before adding a queue or broker. |
| `reference/vsx-submission-rules.md` | VSX's submission notice, FAQ and Manual distilled to the rules that bind this project: rate limits, deletion clock, epoch in HJD, passband preferences, contamination radii, exact survey bibcodes, precision, remarks and revision-comment rules, moderation workflow. | Before touching the form renderer, tracker, blend logic or plots. |
| `reference/vsx-revision-form.md` | The revision form field by field (`name`, input type, notes), the type dropdown vocabulary and the passband vocabulary. | Before touching `submission/form.py` or `classify/types.py`. |
| `reference/vsx-type-ranges.md` | Period, amplitude and shape definitions per VSX type, confusion classes, alias traps, epoch and rise-duration conventions. | Before touching rules or type specs. |

Reference material was captured on 2026-10-19 from the live VSX site after Tom
registered. Re-check the live pages before changing a rule, and record the date.
The Manual PDF is gitignored (AAVSO copyright, 18 MB); Tom keeps a local copy at
`reference/Manual.pdf`.

Two questions no source answered, parked on NEU-1712 for the VSX moderators:
how ZTF object ids are spelled in Other names, and whether ZTF's PS1-system g/r
magnitudes go under the Sloan g/r passband codes.
