"""`.env.example` lists every setting `config.py` reads, so a new setting cannot go undocumented."""

from pathlib import Path

from argelander.config import Settings

ENV_EXAMPLE = Path(__file__).resolve().parent.parent / ".env.example"


def test_env_example_lists_every_setting() -> None:
    lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    names = {line.split("=", 1)[0] for line in lines if line and not line.startswith("#")}

    missing = {field.upper() for field in Settings.model_fields} - names

    assert not missing, f".env.example is missing {sorted(missing)}"
