"""HTTP routes for booking module."""

from uuid import UUID

from fastapi import APIRouter, Header, Query, status

from app.core.deps import BookingServiceDep, CurrentUserDep, CustomerDep, OwnerDep
from app.modules.booking.schemas import (
    BookingResponse,
    CancelBookingRequest,
    CancelBookingResponse,
    CheckInResponse,
    CreateBookingRequest,
    PaginatedBookingResponse,
    WalkInBookingRequest,
)

router = APIRouter(tags=["booking"])


# ===== Bookings: static paths BEFORE parameterized =====


@router.get("/bookings/me", response_model=PaginatedBookingResponse)
async def list_my_bookings(
    customer: CustomerDep,
    booking_service: BookingServiceDep,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
) -> PaginatedBookingResponse:
    return await booking_service.list_mine(customer, page=page, limit=limit)


@router.post(
    "/bookings/walk-in",
    status_code=status.HTTP_201_CREATED,
    response_model=BookingResponse,
)
async def create_walkin_booking(
    data: WalkInBookingRequest,
    owner: OwnerDep,
    booking_service: BookingServiceDep,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> BookingResponse:
    return await booking_service.create_walkin(data, owner, idempotency_key)


# ===== Parameterized =====


@router.post(
    "/bookings",
    status_code=status.HTTP_201_CREATED,
    response_model=BookingResponse,
)
async def create_booking(
    data: CreateBookingRequest,
    customer: CustomerDep,
    booking_service: BookingServiceDep,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> BookingResponse:
    return await booking_service.create(data, customer, idempotency_key)


@router.get("/bookings/{booking_id}", response_model=BookingResponse)
async def get_booking(
    booking_id: UUID,
    user: CurrentUserDep,
    booking_service: BookingServiceDep,
) -> BookingResponse:
    return await booking_service.get(booking_id, user)


@router.get("/bookings", response_model=PaginatedBookingResponse)
async def list_bookings(
    owner: OwnerDep,
    booking_service: BookingServiceDep,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
) -> PaginatedBookingResponse:
    return await booking_service.list_all(owner, page=page, limit=limit)


@router.post("/bookings/{booking_id}/cancel", response_model=CancelBookingResponse)
async def cancel_booking(
    booking_id: UUID,
    data: CancelBookingRequest,
    customer: CustomerDep,
    booking_service: BookingServiceDep,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> CancelBookingResponse:
    return await booking_service.cancel(booking_id, data, customer, idempotency_key)


@router.post("/bookings/{booking_id}/force-cancel", response_model=CancelBookingResponse)
async def force_cancel_booking(
    booking_id: UUID,
    data: CancelBookingRequest,
    owner: OwnerDep,
    booking_service: BookingServiceDep,
) -> CancelBookingResponse:
    return await booking_service.force_cancel(booking_id, data, owner)


@router.post("/bookings/{booking_id}/check-in", response_model=CheckInResponse)
async def check_in_booking(
    booking_id: UUID,
    owner: OwnerDep,
    booking_service: BookingServiceDep,
) -> CheckInResponse:
    return await booking_service.check_in(booking_id, owner)
