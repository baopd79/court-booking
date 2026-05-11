"""VNPay payment gateway client (sandbox + production compatible)."""

import hashlib
import hmac
import logging
import urllib.parse
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_URL_TTL_MINUTES = 15


class VNPayClient:
    def __init__(
        self,
        tmn_code: str,
        hash_secret: str,
        payment_url: str,
        api_url: str,
    ) -> None:
        self.tmn_code = tmn_code
        self.hash_secret = hash_secret
        self.payment_url = payment_url
        self.api_url = api_url

    def _sign(self, params: dict) -> str:
        """HMAC-SHA512 over sorted key=value pairs, excluding vnp_SecureHash fields."""
        clean = {
            k: v for k, v in params.items() if k not in ("vnp_SecureHash", "vnp_SecureHashType")
        }
        query = "&".join(f"{k}={v}" for k, v in sorted(clean.items()))
        return hmac.new(self.hash_secret.encode(), query.encode(), hashlib.sha512).hexdigest()

    def build_payment_url(
        self,
        booking_id: uuid.UUID,
        amount: Decimal,
        txn_ref: str,
        return_url: str,
        client_ip: str,
    ) -> tuple[str, datetime]:
        """Build VNPay payment redirect URL. Returns (url, expires_at)."""
        now = datetime.now(UTC).replace(tzinfo=None)
        expires_at = now + timedelta(minutes=_URL_TTL_MINUTES)

        params: dict[str, str] = {
            "vnp_Version": "2.1.0",
            "vnp_Command": "pay",
            "vnp_TmnCode": self.tmn_code,
            "vnp_Amount": str(int(amount * 100)),
            "vnp_CurrCode": "VND",
            "vnp_TxnRef": txn_ref,
            "vnp_OrderInfo": f"Dat san {booking_id}",
            "vnp_OrderType": "billpayment",
            "vnp_Locale": "vn",
            "vnp_ReturnUrl": return_url,
            "vnp_IpAddr": client_ip,
            "vnp_CreateDate": now.strftime("%Y%m%d%H%M%S"),
            "vnp_ExpireDate": expires_at.strftime("%Y%m%d%H%M%S"),
        }
        params["vnp_SecureHash"] = self._sign(params)
        url = self.payment_url + "?" + urllib.parse.urlencode(params)
        return url, expires_at

    def verify_signature(self, params: dict) -> bool:
        """Verify VNPay IPN / return callback signature."""
        received = params.get("vnp_SecureHash", "")
        expected = self._sign(params)
        return hmac.compare_digest(received.lower(), expected.lower())

    async def query_transaction(
        self,
        txn_ref: str,
        txn_date: str,
        client_ip: str = "127.0.0.1",
    ) -> dict:
        """Call VNPay querydr API for reconciliation. txn_date: YYYYMMDDHHMMSS."""
        now = datetime.now(UTC).replace(tzinfo=None)
        params: dict[str, str] = {
            "vnp_RequestId": uuid.uuid4().hex[:32],
            "vnp_Version": "2.1.0",
            "vnp_Command": "querydr",
            "vnp_TmnCode": self.tmn_code,
            "vnp_TxnRef": txn_ref,
            "vnp_OrderInfo": f"Query {txn_ref}",
            "vnp_TransactionDate": txn_date,
            "vnp_CreateDate": now.strftime("%Y%m%d%H%M%S"),
            "vnp_IpAddr": client_ip,
        }
        params["vnp_SecureHash"] = self._sign(params)

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(self.api_url, json=params)
            resp.raise_for_status()
            return resp.json()


def get_vnpay_client() -> VNPayClient:
    from app.core.config import get_settings

    s = get_settings()
    return VNPayClient(
        tmn_code=s.vnpay_tmn_code,
        hash_secret=s.vnpay_hash_secret,
        payment_url=s.vnpay_url,
        api_url=s.vnpay_api_url,
    )
