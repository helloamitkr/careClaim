"""Step 13 — rate limiter. Tested against a tiny standalone FastAPI app so
the tests need neither Postgres nor Ollama — the middleware is pure
in-process logic."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from carebridge.middleware import RateLimitMiddleware


def _make_client(limit: int) -> TestClient:
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, limit=limit)

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/cases")
    async def cases():
        return []

    return TestClient(app)


def test_requests_under_the_limit_pass():
    client = _make_client(limit=3)
    for _ in range(3):
        assert client.get("/api/cases").status_code == 200


def test_request_over_the_limit_gets_429_with_retry_after():
    client = _make_client(limit=3)
    for _ in range(3):
        client.get("/api/cases")

    response = client.get("/api/cases")
    assert response.status_code == 429
    assert "Retry-After" in response.headers
    assert "Rate limit exceeded" in response.json()["detail"]


def test_health_endpoint_is_exempt():
    client = _make_client(limit=1)
    client.get("/api/cases")  # uses up the whole window
    for _ in range(5):
        assert client.get("/api/health").status_code == 200
    # but the limited path is still blocked
    assert client.get("/api/cases").status_code == 429
