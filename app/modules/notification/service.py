"""Business logic for notification module."""

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.email import EmailSender, NoOpEmailSender
from app.core.exceptions import ForbiddenError, BookingNotFoundError
from app.modules.auth.models import User
from app.modules.auth.repository import UserRepository
from app.modules.booking.models import Booking
from app.modules.notification.models import (
    Notification,
    NotificationChannel,
    NotificationStatus,
)
from app.modules.notification.repository import NotificationRepository
from app.modules.notification.schemas import NotificationItem, PaginatedNotificationResponse

logger = logging.getLogger(__name__)

_MAX_RETRY = 3

_EMAIL_TEMPLATES: dict[str, tuple[str, str]] = {
    "booking_confirmed": (
        "Đặt sân thành công",
        "Booking của bạn đã được xác nhận. Tổng tiền: {total_amount}đ. Mã booking: {booking_id}",
    ),
    "payment_failed": (
        "Thanh toán thất bại",
        "Thanh toán cho booking {booking_id} thất bại. Slot đã được giải phóng.",
    ),
    "booking_cancelled": (
        "Booking đã bị huỷ",
        "Booking {booking_id} đã bị huỷ. Hoàn tiền: {refund_amount}đ",
    ),
}


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class NotificationService:
    def __init__(
        self,
        session: AsyncSession,
        email_sender: EmailSender | NoOpEmailSender | None = None,
    ) -> None:
        self._session = session
        self._repo = NotificationRepository(session)
        self._user_repo = UserRepository(session)
        self._email_sender = email_sender

    # ===== Trigger methods =====

    async def notify_booking_confirmed(self, booking: Booking) -> None:
        if not booking.customer_id:
            return
        user = await self._user_repo.get_by_id(booking.customer_id)
        if not user:
            return
        payload = {
            "booking_id": str(booking.id),
            "court_id": str(booking.court_id),
            "total_amount": str(booking.total_amount),
        }
        await self._notify(user, booking.id, "booking_confirmed", payload)

    async def notify_payment_failed(self, booking: Booking) -> None:
        if not booking.customer_id:
            return
        user = await self._user_repo.get_by_id(booking.customer_id)
        if not user:
            return
        payload = {"booking_id": str(booking.id), "court_id": str(booking.court_id)}
        await self._notify(user, booking.id, "payment_failed", payload)

    async def notify_booking_cancelled(
        self, booking: Booking, refund_amount: str = "0"
    ) -> None:
        if not booking.customer_id:
            return
        user = await self._user_repo.get_by_id(booking.customer_id)
        if not user:
            return
        payload = {
            "booking_id": str(booking.id),
            "refund_amount": refund_amount,
        }
        await self._notify(user, booking.id, "booking_cancelled", payload)

    # ===== Read endpoints =====

    async def list_for_user(
        self, user: User, page: int = 1, limit: int = 20
    ) -> PaginatedNotificationResponse:
        items, total = await self._repo.list_by_user(user.id, page=page, limit=limit)
        return PaginatedNotificationResponse(
            items=[NotificationItem.model_validate(n, from_attributes=True) for n in items],
            total=total,
            page=page,
            limit=limit,
            has_next=(page * limit) < total,
        )

    async def mark_read(self, notification_id: UUID, user: User) -> None:
        notif = await self._repo.get_by_id(notification_id)
        if not notif:
            raise BookingNotFoundError()  # reuse 404
        if notif.user_id != user.id:
            raise ForbiddenError()
        if notif.read_at is None:
            notif.read_at = _utcnow()
            await self._repo.save(notif)
            await self._session.commit()

    # ===== Retry (called by cron job) =====

    async def retry_failed(self) -> int:
        failed = await self._repo.list_failed_retryable(max_retry=_MAX_RETRY)
        count = 0
        for notif in failed:
            user = await self._user_repo.get_by_id(notif.user_id)
            if user:
                await self._send(notif, user.email)
                count += 1
        return count

    # ===== Private =====

    async def _notify(
        self,
        user: User,
        booking_id: UUID,
        event_type: str,
        payload: dict,
    ) -> None:
        """Create in_app + email notifications."""
        in_app = Notification(
            user_id=user.id,
            booking_id=booking_id,
            channel=NotificationChannel.in_app,
            event_type=event_type,
            payload=payload,
        )
        in_app = await self._repo.create(in_app)
        # in_app is "sent" as soon as it's in DB
        in_app.status = NotificationStatus.sent
        in_app.sent_at = _utcnow()
        await self._repo.save(in_app)

        email_notif = Notification(
            user_id=user.id,
            booking_id=booking_id,
            channel=NotificationChannel.email,
            event_type=event_type,
            payload=payload,
        )
        email_notif = await self._repo.create(email_notif)
        await self._send(email_notif, user.email)

    async def _send(self, notif: Notification, to_email: str) -> None:
        """Try to send email; update status regardless of outcome."""
        if notif.channel != NotificationChannel.email:
            return

        template = _EMAIL_TEMPLATES.get(notif.event_type)
        if not template:
            logger.warning("No email template for event_type=%s", notif.event_type)
            return

        subject, body_tpl = template
        body = body_tpl.format(**notif.payload)

        sender = self._email_sender
        if sender is None:
            from app.core.email import get_email_sender
            sender = get_email_sender()

        try:
            await sender.send(to=to_email, subject=subject, body=body)
            notif.status = NotificationStatus.sent
            notif.sent_at = _utcnow()
        except Exception as exc:
            logger.warning("Email send failed (retry_count=%d): %s", notif.retry_count, exc)
            notif.status = NotificationStatus.failed
            notif.retry_count += 1

        await self._repo.save(notif)
