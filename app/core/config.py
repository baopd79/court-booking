"""Application configuration loaded from environment variables.

Single source of truth cho mọi config. Đọc từ .env (dev) hoặc env vars (prod).
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # ignore env vars không khai báo trong class
    )

    # ===== App =====
    app_env: Literal["dev", "test", "staging", "prod"] = "dev"
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    sql_echo: bool = False  # log SQL queries — tách khỏi debug để không flood terminal

    # ===== Database =====
    # PostgresDsn validate format: postgresql+asyncpg://user:pass@host:port/db
    database_url: PostgresDsn

    # ===== Redis =====
    redis_url: RedisDsn

    # ===== JWT =====
    jwt_secret_key: str = Field(min_length=32)  # tối thiểu 32 ký tự cho HS256
    jwt_algorithm: str = "HS256"
    jwt_access_ttl_minutes: int = 15
    jwt_refresh_ttl_days: int = 7

    # ===== VNPay =====
    vnpay_tmn_code: str = ""
    vnpay_hash_secret: str = ""
    vnpay_url: str = "https://sandbox.vnpayment.vn/paymentv2/vpcpay.html"
    vnpay_return_url: str = "http://localhost:8000/payments/vnpay-return"

    # ===== Email (SMTP) =====
    smtp_host: str = "localhost"
    smtp_port: int = 1025  # mailhog default
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "noreply@court-booking.local"

    # ===== Business rules =====
    hold_duration_minutes: int = 10
    max_pending_bookings_per_user: int = 5
    business_hour_start: int = 6  # 06:00
    business_hour_end: int = 22  # 22:00


@lru_cache
def get_settings() -> Settings:
    """Cached singleton. Test override bằng cách clear cache."""
    return Settings()  # type: ignore[call-arg]
