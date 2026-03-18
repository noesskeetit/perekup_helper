"""change listings.photos from ARRAY to JSONB

Revision ID: c1d2e3f4a5b6
Revises: a1b2c3d4e5f6
Create Date: 2026-03-18 14:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE listings ALTER COLUMN photos TYPE JSONB USING to_jsonb(photos)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE listings ALTER COLUMN photos"
        " TYPE TEXT[] USING ARRAY(SELECT jsonb_array_elements_text(photos))"
    )
