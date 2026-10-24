# notebooks/

Prototype notebooks, served by the `jupyter` service at
<http://localhost:8888/lab> after `task up`.

Import from `argelander`; never define science here (spec §3.1). Anything a notebook
works out moves into the package, with a test, before the pipeline relies on it.

| Notebook | What it does |
| --- | --- |
| `01_prototype.ipynb` | The prototype gate (NEU-1711): every fixture star through the pipeline, with the summary table of derived against published periods. |
| `02_guide_one_star.ipynb` | The guide's walkthrough (NEU-1800): three stars from raw points to a verdict, writing every figure in `docs/guide/figures/`. |

Both run offline on the committed fixtures. Commit notebooks without outputs
(`tests/test_guide.py` checks).
