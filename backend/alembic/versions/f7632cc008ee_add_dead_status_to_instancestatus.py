"""add_dead_status_to_instancestatus

Revision ID: f7632cc008ee
Revises: b10aeacf0554
Create Date: 2026-02-10 23:52:07.602905

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7632cc008ee'
down_revision: Union[str, None] = 'b10aeacf0554'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add DEAD to instancestatus enum
    op.execute("ALTER TYPE instancestatus ADD VALUE IF NOT EXISTS 'DEAD'")


def downgrade() -> None:
    pass
