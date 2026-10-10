"""Per-IP sliding-window rate limits for sensitive endpoints (login, signup, reset, EA/worker gateways).

In-process: with several API processes each one counts separately, so nginx `limit_req` (deploy/linux/nginx) is the
first line of defence and this is the second.
"""
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import Request

from .config import get_settings
from .errors import AppError, ErrorCode

settings = get_settings()
_hits: dict[str, deque] = defaultdict(deque)
_lock = Lock()


def check(key: str, limit: int, window: float = 60.0) -> None:
    now = time.monotonic()
    with _lock:
        q = _hits[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            raise AppError(429, "Too many requests. Wait a minute and try again.", ErrorCode.RATE_LIMITED)
        q.append(now)


def reset() -> None:
    with _lock:
        _hits.clear()


def limiter(bucket: str, per_min: int | None = None):
    def dep(request: Request) -> None:
        ip = request.client.host if request.client else "unknown"
        limit = per_min or (settings.rate_limit_gateway_per_min if bucket in ("ea", "worker") else settings.rate_limit_auth_per_min)
        check(f"{bucket}:{ip}", limit)
    return dep
