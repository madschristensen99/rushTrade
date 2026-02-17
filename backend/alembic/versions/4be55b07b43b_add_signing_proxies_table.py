"""add_signing_proxies_table

Revision ID: 4be55b07b43b
Revises: 7fbef342481f
Create Date: 2025-01-24

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4be55b07b43b'
down_revision = '7fbef342481f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'signing_proxies',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('kalshi_api_key', sa.String(), nullable=False),
        sa.Column('encrypted_private_key', sa.Text(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id')
    )
    op.create_index(op.f('ix_signing_proxies_user_id'), 'signing_proxies', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_signing_proxies_user_id'), table_name='signing_proxies')
    op.drop_table('signing_proxies')