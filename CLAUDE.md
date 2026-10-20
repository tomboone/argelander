# Argelander

Self-hosted pipeline that proposes evidence-backed corrections to the AAVSO VSX
variable star catalog. Personal project (not business work). Single repo.

linear_initiative: Argelander
linear_team: Neuroticsasquatch
linear_repo: argelander
specs_dir: docs

## Where things are

Read in this order when starting work:

1. `docs/handover.md`: the settled design (scope, data sources, architecture,
   infrastructure, build order). Treat its decisions as decided unless Tom
   reopens them.
2. `docs/specs/argelander-initial-buildout-project-spec.md`: the project-wide
   spec every ticket is built from (package layout, schema, light-curve
   contract, analysis order, classifier, review app, infra, testing).
3. `CONTEXT.md`: the glossary. Use its terms in code, tables and templates.
4. `docs/adr/`: decisions that are hard to reverse and would look odd without
   context (manual VSX submission; Postgres as the job queue).
5. `docs/reference/`: VSX's own rules, captured 2026-10-19 after Tom registered.
   These override anything in the handover or spec that disagrees with them.
   - `vsx-submission-rules.md`: the submission notice, FAQ and Manual rules
     (rate limits, epoch in HJD, passbands, contamination radii, survey
     bibcodes, precision, remarks and revision-comment rules).
   - `vsx-revision-form.md`: every field on the revision form by `name`, the
     type dropdown vocabulary and the passband vocabulary.
   - `vsx-type-ranges.md`: period, amplitude and shape definitions per type;
     the source for `classify/types.py` and the rules.
   - `Manual.pdf` is gitignored (AAVSO copyright); Tom has a local copy.

Linear: initiative "Argelander", project "Argelander: Initial Buildout"
(P-NEU-98). Tickets NEU-1694..1735 carry dated "Update" blocks where the
VSX sources changed them; the spec is the merged result.

## Design handover

Decisions made after the handover was written (2026-10-19):

- Project name: Argelander (repo `tomboone/argelander`)
- First slice: Draco, magnitude 13–16
- Review app frontend: server-rendered (FastAPI + Jinja2 + htmx), no separate
  frontend build
- One repo, one Python package, one image with a different entrypoint per
  Compose service

## Conventions

- Python 3.12, uv + pyproject.toml, ruff, ty. Type hints use `X | None`, never
  `Optional[X]`. No `from __future__ import annotations`.
- Docker-only local dev; no local venvs.
- Base image `python:3.12-slim`. `MPLBACKEND=Agg`. Polars needs
  `polars-lts-cpu` on the target host (2011 Mac mini).
- Never call the VSX web forms from code. Reads are programmatic; submission is
  manual.
