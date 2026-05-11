"""FastAPI routes for notification module."""

from uuid import UUID

from fastapi import APIRouter, Query, status

from app.core.deps import CurrentUserDep, NotificationServiceDep
from app.modules.notification.schemas import PaginatedNotificationResponse

router = APIRouter(tags=["notification"])


@router.get("/notifications", response_model=PaginatedNotificationResponse)
async def list_notifications(
    user: CurrentUserDep,
    notification_service: NotificationServiceDep,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
) -> PaginatedNotificationResponse:
    return await notification_service.list_for_user(user, page=page, limit=limit)


@router.post("/notifications/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_notification_read(
    notification_id: UUID,
    user: CurrentUserDep,
    notification_service: NotificationServiceDep,
) -> None:
    await notification_service.mark_read(notification_id, user)
