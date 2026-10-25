"""no-op: prove the Watchtower schema wait

Revision ID: 6239149ad9a7
Revises: a6e2fcd9cb24
Create Date: 2026-10-25 20:32:26.649007
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "6239149ad9a7"
down_revision: str | Sequence[str] | None = "a6e2fcd9cb24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
