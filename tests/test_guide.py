"""The guide (`docs/guide/`, NEU-1800): every figure a chapter shows exists and was written
by the walkthrough notebook, each chapter links the glossary, the docs index links the
guide, and the committed notebooks carry no outputs.

The figures are produced by executing `notebooks/02_guide_one_star.ipynb`, which takes a
few minutes, so this does not run it; it checks what the notebook left behind, as
`test_fixtures.py` does for `scripts/build_fixtures.py`.
"""

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
GUIDE = REPO / "docs" / "guide"
FIGURES = GUIDE / "figures"
CHAPTERS = sorted(GUIDE.glob("0*.md"))
NOTEBOOKS = sorted((REPO / "notebooks").glob("*.ipynb"))
IMAGE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


def images_in(chapter: Path) -> list[Path]:
    return [chapter.parent / target for target in IMAGE.findall(chapter.read_text())]


def test_the_guide_has_four_chapters_and_an_index() -> None:
    assert [p.name[:2] for p in CHAPTERS] == ["01", "02", "03", "04"]
    assert (GUIDE / "README.md").exists()


@pytest.mark.parametrize("chapter", CHAPTERS, ids=lambda p: p.stem)
def test_every_figure_a_chapter_shows_exists_under_figures(chapter: Path) -> None:
    images = images_in(chapter)
    missing = [p for p in images if not p.exists()]
    assert missing == []
    outside = [p for p in images if FIGURES not in p.resolve().parents]
    assert outside == [], "every figure comes from the walkthrough notebook"


@pytest.mark.parametrize("chapter", [*CHAPTERS, GUIDE / "README.md"], ids=lambda p: p.stem)
def test_every_chapter_links_the_glossary(chapter: Path) -> None:
    assert "CONTEXT.md" in chapter.read_text()


def test_the_figures_belong_to_the_walkthrough_stars() -> None:
    notebook = json.loads((REPO / "notebooks" / "02_guide_one_star.ipynb").read_text())
    source = "".join("".join(c["source"]) for c in notebook["cells"] if c["cell_type"] == "code")
    stars = set(re.findall(r'run\("([^"]+)"\)', source))
    slugs = {re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") for s in stars}
    assert {p.name for p in FIGURES.iterdir() if p.is_dir()} == slugs


def test_the_docs_index_links_the_guide() -> None:
    assert "guide/README.md" in (REPO / "docs" / "README.md").read_text()


@pytest.mark.parametrize("notebook", NOTEBOOKS, ids=lambda p: p.stem)
def test_notebooks_are_committed_without_outputs(notebook: Path) -> None:
    cells = json.loads(notebook.read_text())["cells"]
    assert all(not c.get("outputs") for c in cells if c["cell_type"] == "code")
