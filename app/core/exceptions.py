"""Custom exceptions cho toàn bộ app.

Tách riêng để các module không phải import lib bên ngoài (jose, bcrypt, ...).
Đổi lib chỉ sửa 1 chỗ ở module gốc.
"""


class AppException(Exception):  # noqa: N818
    """Base exception cho domain errors."""


# ===== Auth =====


class TokenExpiredError(AppException):
    """JWT đã hết hạn (exp < now)."""


class InvalidTokenError(AppException):
    """JWT sai signature, malformed, hoặc claims không hợp lệ."""
