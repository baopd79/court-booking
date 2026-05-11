"""add bookings booking_slots idempotency_keys

Revision ID: be7c56902adb
Revises: 839fadfbfb2d
Create Date: 2026-05-11 11:49:46.410252

"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'be7c56902adb'
down_revision: Union[str, Sequence[str], None] = '839fadfbfb2d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'idempotency_keys',
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('endpoint', sa.String(length=100), nullable=False),
        sa.Column('request_hash', sa.String(length=64), nullable=True),
        sa.Column('response_status', sa.Integer(), nullable=True),
        sa.Column('response_body', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('key', 'user_id'),
    )
    op.create_index('idx_idempotency_expires', 'idempotency_keys', ['expires_at'], unique=False)

    op.create_table(
        'bookings',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('customer_id', sa.Uuid(), nullable=True),
        sa.Column('court_id', sa.Uuid(), nullable=False),
        sa.Column(
            'status',
            sa.Enum(
                'pending_payment', 'payment_processing', 'confirmed', 'in_use',
                'completed', 'expired', 'payment_failed', 'cancelled',
                name='bookingstatus',
            ),
            nullable=False,
        ),
        sa.Column('total_amount', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column(
            'booking_type',
            sa.Enum('online', 'walkin', name='bookingtype'),
            nullable=False,
        ),
        sa.Column('walkin_name', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('walkin_phone', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('hold_expires_at', sa.DateTime(), nullable=True),
        sa.Column(
            'cancelled_by',
            sa.Enum('customer', 'owner', 'system', name='cancelledby'),
            nullable=True,
        ),
        sa.Column('cancellation_reason', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('cancelled_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "(booking_type = 'online'"
            "  AND customer_id IS NOT NULL"
            "  AND walkin_name IS NULL"
            "  AND walkin_phone IS NULL)"
            " OR "
            "(booking_type = 'walkin'"
            "  AND customer_id IS NULL"
            "  AND walkin_name IS NOT NULL"
            "  AND walkin_phone IS NOT NULL)",
            name='chk_booking_owner',
        ),
        sa.CheckConstraint('total_amount > 0', name='chk_booking_amount'),
        sa.ForeignKeyConstraint(['court_id'], ['courts.id']),
        sa.ForeignKeyConstraint(['customer_id'], ['users.id']),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'booking_slots',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('booking_id', sa.Uuid(), nullable=False),
        sa.Column('slot_id', sa.BigInteger(), nullable=False),
        sa.Column('price_at_booking', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.CheckConstraint('price_at_booking > 0', name='chk_booking_slot_price'),
        sa.ForeignKeyConstraint(['booking_id'], ['bookings.id']),
        sa.ForeignKeyConstraint(['slot_id'], ['slots.id']),
        sa.PrimaryKeyConstraint('id'),
    )

    # Add FK from slots.held_by_booking_id → bookings.id (deferred from Slice 4)
    op.create_foreign_key(
        'fk_slots_held_by_booking_id',
        'slots', 'bookings',
        ['held_by_booking_id'], ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_slots_held_by_booking_id', 'slots', type_='foreignkey')
    op.drop_table('booking_slots')
    op.drop_table('bookings')
    op.drop_index('idx_idempotency_expires', table_name='idempotency_keys')
    op.drop_table('idempotency_keys')
    sa.Enum(name='bookingstatus').drop(op.get_bind())
    sa.Enum(name='bookingtype').drop(op.get_bind())
    sa.Enum(name='cancelledby').drop(op.get_bind())
