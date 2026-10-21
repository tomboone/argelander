# The Argelander guide

A guide for a reader with no astronomy background to the science behind Argelander and
to the evidence it produces. It exists because Tom reviews every
[Proposal](../../CONTEXT.md#proposals-and-review) and submits every revision himself, and
that human check is the only one between the pipeline and a VSX moderator. Nothing should
go out under his name on evidence he cannot read.

| Chapter | What it answers |
| --- | --- |
| [1. Why variable stars matter](01-why-variable-stars-matter.md) | What makes a star vary, what a period and a type tell astronomers, who uses VSX, and what a revision is. |
| [2. The problem Argelander addresses](02-the-vsx-problem.md) | How VSX came to hold wrong periods and types: bulk imports, the 1-day alias, half periods, and neighbouring stars blending into each other. |
| [3. How to read the evidence](03-reading-the-evidence.md) | One section per step the pipeline takes, each tied to the plot the review app will show. |
| [4. Can you trust it?](04-can-you-trust-it.md) | The prototype gate's results on 27 known stars, told plainly: what passes, the one known failure, the thresholds, and what the pipeline cannot know. |

Read them in order the first time. Chapter 3 is the one to come back to with a Proposal
open.

## The walkthrough notebook

Every figure in the guide comes from
[`notebooks/02_guide_one_star.ipynb`](../../notebooks/02_guide_one_star.ipynb), which
runs the pipeline on three real stars from the committed fixture set and writes the
figures to [`figures/`](figures/). Each star's directory holds the full set of plots the
review app will show for it, not only the ones the chapters pick out. No figure is a
screenshot of a survey website, which
VSX forbids attaching to a submission, and each can be regenerated from the data it came
from. To regenerate them with the dev stack up:

```sh
docker compose exec -w /app/notebooks jupyter \
  jupyter nbconvert --to notebook --execute --output /tmp/02_executed.ipynb 02_guide_one_star.ipynb
```

or open the notebook at <http://localhost:8888/lab> and run it top to bottom. The three
stars take about three minutes together.

## What this guide does not cover

- **Operating the app**: selecting a Slice, the workers, the review app and the submission
  tracker. That is part 2 of the guide (NEU-1801), written once those exist.
- **Type classification** beyond what a type means. The classifier rules arrive in
  milestone 4.
- **ASAS-SN evidence.** Sky Patrol was down while the fixtures were built, so ASAS-SN is a
  coverage gap in every figure here. Chapter 3 says where it will appear once NEU-1796
  adds it.

Terms in **bold** on first use are the project's shared vocabulary, defined in
[`CONTEXT.md`](../../CONTEXT.md). The technical contract behind every section is the
[project spec](../specs/argelander-initial-buildout-project-spec.md), and VSX's own rules are
in [`docs/reference/`](../reference/).
