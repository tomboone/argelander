# Argelander

A self-hosted pipeline that proposes corrections to the AAVSO's
[VSX](https://www.aavso.org/vsx/) variable star catalog.

VSX holds roughly 166,000 entries with Gaia DR3 IDs that are typed only as
"VAR" (variable, type unknown), and many short-period ones carry wrong periods.
Argelander selects those entries one sky slice at a time, fetches public survey
light curves (ZTF, TESS, Gaia DR3 epoch photometry, ASAS-SN), derives periods,
proposes a variability type with a confidence score, and queues each proposal
with evidence plots for human review. Approved proposals are prepared for
manual submission through the VSX revision forms, and moderator verdicts loop
back to tune the classifier rules.

The deliverable is accepted VSX revisions, each a verified claim with traceable
evidence. The pipeline is a private instrument for producing them, not a
product. It is named for Friedrich Argelander, who devised the variable-star
naming scheme VSX still uses.

## Status

Planning. See [docs/handover.md](docs/handover.md) for the design handover that
drives the implementation plan.

## Principles

- **Rules before machine learning.** Moderators need explainable reasoning.
- **Provenance on every claim.** Survey data, date ranges, parameters and
  pipeline version are stored with each result.
- **Multi-survey agreement.** A period is accepted when at least two
  independent sources agree, after testing the usual aliases.
- **Human review is mandatory.** Nothing reaches the submission queue
  unapproved.
- **Never script the VSX web forms.** Submission is manual by design.

## Stack

Python 3.12, FastAPI with server-rendered templates, Postgres (results and the
job queue), Parquet light-curve cache, Docker Compose on a Mac mini behind a
Tailscale sidecar.

## Development

Docker only; nothing runs in a host virtualenv. Needs Docker, [go-task](https://taskfile.dev)
and the [pre-commit](https://pre-commit.com) framework (`brew install go-task pre-commit`).

```sh
task up          # postgres, app and jupyter; installs the git hooks
task test        # offline tests in the app container
task lint        # ruff check + format check
task typecheck   # ty
task workers     # the fetch and analyze workers, for a pipeline run
task down
```

`docs/runbook.md` walks through a pipeline run on the dev stack, command by command.

JupyterLab is at <http://localhost:8888/lab> and Postgres at `localhost:5433` (5432 is left
to the shared localdev Postgres); copy `.env.example` to `.env` to change either. The
pre-commit and pre-push hooks run inside the app container, so the stack must be up to
commit.

## License

[MIT](LICENSE).
