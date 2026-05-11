"""FastAPI routes for payment module."""

from fastapi import APIRouter, Header, Request, status

from app.core.deps import CustomerDep, PaymentServiceDep
from app.modules.payment.schemas import (
    InitiatePaymentRequest,
    InitiatePaymentResponse,
    VNPayReturnResponse,
)

router = APIRouter(tags=["payment"])


@router.post(
    "/payments/initiate",
    response_model=InitiatePaymentResponse,
    status_code=status.HTTP_200_OK,
)
async def initiate_payment(
    data: InitiatePaymentRequest,
    request: Request,
    customer: CustomerDep,
    payment_service: PaymentServiceDep,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> InitiatePaymentResponse:
    client_ip = request.client.host if request.client else "127.0.0.1"
    return await payment_service.initiate(data, customer, idempotency_key, client_ip)


@router.post("/payments/vnpay-ipn")
async def vnpay_ipn(
    request: Request,
    payment_service: PaymentServiceDep,
) -> dict:
    params = dict(request.query_params)
    return await payment_service.handle_vnpay_ipn(params)


@router.get("/payments/vnpay-return", response_model=VNPayReturnResponse)
async def vnpay_return(
    request: Request,
    payment_service: PaymentServiceDep,
) -> VNPayReturnResponse:
    params = dict(request.query_params)
    return await payment_service.handle_vnpay_return(params)
