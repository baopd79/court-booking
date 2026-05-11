"""Custom exceptions cho toàn bộ app.

Tách riêng để các module không phải import lib bên ngoài (jose, bcrypt, ...).
Đổi lib chỉ sửa 1 chỗ ở module gốc.
"""


class AppException(Exception):  # noqa: N818
    """Base exception cho domain errors."""

    code: str = "INTERNAL_ERROR"
    http_status: int = 500

    def __init__(self, message: str | None = None, details: dict | None = None) -> None:
        self.message = message or "An unexpected error occurred"
        self.details = details
        super().__init__(self.message)


# ===== Auth =====


class TokenExpiredError(AppException):
    """JWT đã hết hạn."""

    code = "TOKEN_EXPIRED"
    http_status = 401


class InvalidTokenError(AppException):
    """JWT sai signature, malformed, hoặc claims không hợp lệ."""

    code = "INVALID_TOKEN"
    http_status = 401


class EmailAlreadyExistsError(AppException):
    code = "EMAIL_ALREADY_EXISTS"
    http_status = 409


class InvalidVerificationTokenError(AppException):
    code = "INVALID_VERIFICATION_TOKEN"
    http_status = 400


class TenantNotFoundError(AppException):
    code = "TENANT_NOT_FOUND"
    http_status = 500


class InvalidCredentialsError(AppException):
    code = "INVALID_CREDENTIALS"
    http_status = 401


class EmailNotVerifiedError(AppException):
    code = "EMAIL_NOT_VERIFIED"
    http_status = 403


class AccountSuspendedError(AppException):
    code = "ACCOUNT_SUSPENDED"
    http_status = 403


# ===== Facility =====


class FacilityNotFoundError(AppException):
    code = "FACILITY_NOT_FOUND"
    http_status = 404


class CourtNotFoundError(AppException):
    code = "COURT_NOT_FOUND"
    http_status = 404


class ForbiddenError(AppException):
    code = "FORBIDDEN"
    http_status = 403


class DuplicateNameError(AppException):
    code = "DUPLICATE_NAME"
    http_status = 409


# ===== Booking =====


class BookingNotFoundError(AppException):
    code = "BOOKING_NOT_FOUND"
    http_status = 404


class SlotNotAvailableError(AppException):
    code = "SLOT_NOT_AVAILABLE"
    http_status = 409

    def __init__(self, unavailable_slot_ids: list[int]) -> None:
        super().__init__(
            "Some slots are not available",
            details={"unavailable_slot_ids": unavailable_slot_ids},
        )


class InvalidSlotsError(AppException):
    code = "INVALID_SLOTS"
    http_status = 400


class MissingIdempotencyKeyError(AppException):
    code = "MISSING_IDEMPOTENCY_KEY"
    http_status = 400


class IdempotencyKeyReusedError(AppException):
    code = "IDEMPOTENCY_KEY_REUSED_DIFFERENT_BODY"
    http_status = 409


class TooManyPendingError(AppException):
    code = "TOO_MANY_PENDING"
    http_status = 429
