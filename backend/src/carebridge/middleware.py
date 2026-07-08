"""Step 13 — in-process rate limiter. A sliding-window counter per client
IP: each request's timestamp goes into that client's window, anything older
than the window is dropped, and if the window is full the request gets a 429
with Retry-After instead of reaching the pipeline.

Deliberately dependency-free and in-memory — same trade-off as the event
bus. If the API ever runs as multiple processes the counter moves to Redis,
but the middleware interface (and every caller) stays the same.

/api/health is exempt so orchestrators and uptime checks can poll freely.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque

from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

DEFAULT_LIMIT_PER_MINUTE = 120
WINDOW_SECONDS = 60.0
EXEMPT_PATHS = frozenset({"/api/health"})


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, limit: int | None = None) -> None:
        super().__init__(app)
        self.limit = limit or int(
            os.environ.get("RATE_LIMIT_PER_MINUTE", DEFAULT_LIMIT_PER_MINUTE)
        )
        self._windows: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        key = request.client.host if request.client else "unknown"
        now = time.monotonic()
        window = self._windows[key]

        while window and now - window[0] > WINDOW_SECONDS:
            window.popleft()

        if len(window) >= self.limit:
            retry_after = max(1, int(WINDOW_SECONDS - (now - window[0])) + 1)
            logger.bind(component="rate_limiter", client=key).warning(
                "rate limit hit: {client} on {path} ({limit}/min)",
                client=key,
                path=request.url.path,
                limit=self.limit,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        f"Rate limit exceeded: {self.limit} requests per "
                        f"{int(WINDOW_SECONDS)}s. Try again in {retry_after}s."
                    )
                },
                headers={"Retry-After": str(retry_after)},
            )

        window.append(now)
        return await call_next(request)
