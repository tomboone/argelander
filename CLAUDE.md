# Argelander

Self-hosted pipeline that proposes evidence-backed corrections to the AAVSO VSX
variable star catalog. Personal project (not business work). Single repo.

- linear_initiative: Argelander
- linear_team: Neuroticsasquatch
- linear_repo: argelander
- specs_dir: docs

## Design handover

`docs/handover.md` is the settled design: scope, data sources, architecture,
infrastructure, storage budget, design principles and build order. Treat its
decisions as decided unless Tom reopens them.

Decisions made after the handover was written (2026-10-19):

- Project name: Argelander (repo `tomboone/argelander`)
- First slice: Draco, magnitude 13–16
- Review app frontend: server-rendered (FastAPI + Jinja2 + htmx), no separate
  frontend build
- One repo, one Python package, one image with a different entrypoint per
  Compose service

## Conventions

- Python 3.12, uv + pyproject.toml, ruff. Type hints use `X | None`, never
  `Optional[X]`. No `from __future__ import annotations`.
- Docker-only local dev; no local venvs.
- Base image `python:3.12-slim`. `MPLBACKEND=Agg`. Polars needs
  `polars-lts-cpu` on the target host (2011 Mac mini).
- Never call the VSX web forms from code. Reads are programmatic; submission is
  manual.
