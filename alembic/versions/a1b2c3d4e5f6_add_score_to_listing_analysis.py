"""add score to listing_analysis

Revision ID: a1b2c3d4e5f6
Revises: b6998ad6c869
Create Date: 2026-03-18 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "b6998ad6c869"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("listing_analysis", sa.Column("score", sa.Float, nullable=True))


def downgrade() -> None:
    op.drop_column("listing_analysis", "score")
