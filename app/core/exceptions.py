"""Custom exceptions cho toàn bộ app.

Tách riêng để các module không phải import lib bên ngoài (jose, bcrypt, ...).
Đổi lib chỉ sửa 1 chỗ ở module gốc.
"""


class AppException(Exception):  # noqa: N818
    """Base exception cho domain errors."""

    code: str = "INTERNAL_ERROR"
    http_status: int = 500

    def __init__(self, message: str | None = None) -> None:
        self.message = message or "An unexpected error occurred"
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
