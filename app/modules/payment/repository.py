"""Database query layer for payment module."""

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.modules.payment.models import Payment, PaymentStatus, Refund


class PaymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, payment: Payment) -> Payment:
        self._session.add(payment)
        await self._session.flush()
        await self._session.refresh(payment)
        return payment

    async def get_by_booking(self, booking_id: UUID) -> Payment | None:
        result = await self._session.execute(
            select(Payment).where(Payment.booking_id == booking_id)
        )
        return result.scalar_one_or_none()

    async def get_by_booking_locked(self, booking_id: UUID) -> Payment | None:
        result = await self._session.execute(
            select(Payment).where(Payment.booking_id == booking_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_by_txn_ref(self, txn_ref: str) -> Payment | None:
        result = await self._session.execute(
            select(Payment).where(Payment.vnpay_txn_ref == txn_ref)
        )
        return result.scalar_one_or_none()

    async def get_by_txn_ref_locked(self, txn_ref: str) -> Payment | None:
        result = await self._session.execute(
            select(Payment).where(Payment.vnpay_txn_ref == txn_ref).with_for_update()
        )
        return result.scalar_one_or_none()

    async def mark_success(
        self, payment: Payment, response_code: str, paid_at: datetime
    ) -> Payment:
        payment.status = PaymentStatus.success
        payment.vnpay_response_code = response_code
        payment.paid_at = paid_at
        self._session.add(payment)
        await self._session.flush()
        return payment

    async def mark_failed(self, payment: Payment, response_code: str) -> Payment:
        payment.status = PaymentStatus.failed
        payment.vnpay_response_code = response_code
        self._session.add(payment)
        await self._session.flush()
        return payment

    async def list_pending_before(self, cutoff: datetime) -> list[Payment]:
        result = await self._session.execute(
            select(Payment).where(
                Payment.status == PaymentStatus.pending,
                Payment.created_at < cutoff,
                col(Payment.vnpay_txn_ref).isnot(None),
            )
        )
        return list(result.scalars().all())


class RefundRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, refund: Refund) -> Refund:
        self._session.add(refund)
        await self._session.flush()
        await self._session.refresh(refund)
        return refund
