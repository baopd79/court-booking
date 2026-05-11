"""add slots table

Revision ID: 839fadfbfb2d
Revises: 5fa47f3c12c3
Create Date: 2026-05-11 02:44:18.313071

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '839fadfbfb2d'
down_revision: Union[str, Sequence[str], None] = '5fa47f3c12c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'slots',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('court_id', sa.Uuid(), nullable=False),
        sa.Column('slot_start', sa.DateTime(), nullable=False),
        sa.Column('slot_end', sa.DateTime(), nullable=False),
        sa.Column(
            'status',
            sa.Enum('available', 'held', 'booked', 'closed', name='slotstatus'),
            nullable=False,
        ),
        sa.Column('held_until', sa.DateTime(), nullable=True),
        sa.Column('held_by_booking_id', sa.Uuid(), nullable=True),
        sa.Column('version', sa.Integer(), server_default='0', nullable=False),
        sa.ForeignKeyConstraint(['court_id'], ['courts.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('court_id', 'slot_start', name='uq_slot_court_start'),
    )
    # Partial index for cron cleanup of expired holds
    op.create_index(
        'idx_slots_held_until',
        'slots',
        ['held_until'],
        postgresql_where=sa.text("status = 'held'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_slots_held_until', table_name='slots')
    op.drop_table('slots')
    sa.Enum(name='slotstatus').drop(op.get_bind())
