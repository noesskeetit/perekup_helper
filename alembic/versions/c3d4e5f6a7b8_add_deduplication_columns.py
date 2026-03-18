"""add deduplication columns to listings

Revision ID: c3d4e5f6a7b8
Revises: a1b2c3d4e5f6
Create Date: 2026-03-18 14:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("listings", sa.Column("vin", sa.String(50), nullable=True))
    op.create_index("ix_listings_vin", "listings", ["vin"])
    op.add_column(
        "listings",
        sa.Column("is_duplicate", sa.Boolean, nullable=False, server_default="false"),
    )
    op.add_column("listings", sa.Column("canonical_id", sa.Uuid, nullable=True))


def downgrade() -> None:
    op.drop_column("listings", "canonical_id")
    op.drop_column("listings", "is_duplicate")
    op.drop_index("ix_listings_vin", "listings")
    op.drop_column("listings", "vin")
