"""Health check endpoints — smoke test cho test infrastructure."""

from httpx import AsyncClient


async def test_liveness(client: AsyncClient) -> None:
    """Liveness check trả 200."""
    response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


async def test_readiness(client: AsyncClient) -> None:
    """Readiness check trả 200 với DB + Redis ok."""
    response = await client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["redis"] == "ok"
