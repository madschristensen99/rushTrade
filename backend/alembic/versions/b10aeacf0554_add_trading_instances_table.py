"""add_trading_instances_table

Revision ID: b10aeacf0554
Revises: 4be55b07b43b
Create Date: 2025-01-24

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON


# revision identifiers, used by Alembic.
revision = 'b10aeacf0554'
down_revision = '4be55b07b43b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'trading_instances',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('script', sa.Enum('KEYS1', 'KEYS2', 'AUTO1', 'AUTO2', name='scripttype'), nullable=False),
        sa.Column('markets', JSON, nullable=False),
        sa.Column('config', JSON, nullable=False),
        sa.Column('status', sa.Enum('PENDING', 'RUNNING', 'PAUSED', 'STOPPED', 'ERROR', name='instancestatus'), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('pnl', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('celery_task_id', sa.String(), nullable=True),
        sa.Column('start_time', sa.String(), nullable=True),
        sa.Column('orderbook_data', JSON, nullable=True),
        sa.Column('current_increment', JSON, nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_trading_instances_user_id'), 'trading_instances', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_trading_instances_user_id'), table_name='trading_instances')
    op.drop_table('trading_instances')
    op.execute('DROP TYPE scripttype')
    op.execute('DROP TYPE instancestatus')