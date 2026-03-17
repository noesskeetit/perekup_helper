"""create listings and listing_analysis tables

Revision ID: b6998ad6c869
Revises:
Create Date: 2026-03-18 00:55:43.651586

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b6998ad6c869'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "listings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.String(50), nullable=False, index=True),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("brand", sa.String(100), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("year", sa.Integer, nullable=False),
        sa.Column("mileage", sa.Integer, nullable=True),
        sa.Column("price", sa.Integer, nullable=False),
        sa.Column("market_price", sa.Integer, nullable=True),
        sa.Column("price_diff_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("photos", postgresql.ARRAY(sa.String), nullable=True),
        sa.Column("raw_data", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_listings_source_external_id", "listings", ["source", "external_id"])

    op.create_table(
        "listing_analysis",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "listing_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("ai_summary", sa.Text, nullable=True),
        sa.Column("flags", postgresql.ARRAY(sa.String), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("listing_analysis")
    op.drop_constraint("uq_listings_source_external_id", "listings", type_="unique")
    op.drop_table("listings")
