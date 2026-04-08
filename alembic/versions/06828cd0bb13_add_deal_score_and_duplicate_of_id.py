"""add deal_score and duplicate_of_id columns

Revision ID: 06828cd0bb13
Revises: c3d4e5f6a7b8
Create Date: 2026-04-08 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "06828cd0bb13"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("listings", sa.Column("deal_score", sa.Float, nullable=True))
    op.create_index("ix_listings_deal_score", "listings", ["deal_score"])

    op.add_column("listings", sa.Column("duplicate_of_id", sa.Uuid, nullable=True))
    op.create_foreign_key(
        "fk_listings_duplicate_of_id",
        "listings",
        "listings",
        ["duplicate_of_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_listings_duplicate_of_id", "listings", type_="foreignkey")
    op.drop_column("listings", "duplicate_of_id")
    op.drop_index("ix_listings_deal_score", "listings")
    op.drop_column("listings", "deal_score")
