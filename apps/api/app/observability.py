"""Structured logging with request/bot context.

Every log line carries: ts, level, env, service, module, event, message and whatever context is bound for the
current request or job (correlation_id, tenant_id, bot_code, instance_id, bot_version, account_ref, order_ref...).
Secrets are never passed to the logger; `scrub` removes known secret keys from dicts as a second line of defence.
"""
import contextvars
import json
import logging
import time
import uuid
from datetime import datetime, timezone

from .config import get_settings

_ctx: contextvars.ContextVar[dict] = contextvars.ContextVar("log_ctx", default={})
SECRET_KEYS = {"password", "mt5_password", "credential", "credential_enc", "token", "access_token", "refresh_token",
               "license_key", "secret", "totp_secret", "authorization", "api_key", "key_enc"}


def bind(**fields) -> contextvars.Token:
    return _ctx.set({**_ctx.get(), **{k: v for k, v in fields.items() if v is not None}})


def reset(token: contextvars.Token) -> None:
    _ctx.reset(token)


def context() -> dict:
    return dict(_ctx.get())


def correlation_id() -> str | None:
    return _ctx.get().get("correlation_id")


def scrub(data):
    if isinstance(data, dict):
        return {k: ("[redacted]" if k.lower() in SECRET_KEYS else scrub(v)) for k, v in data.items()}
    if isinstance(data, list):
        return [scrub(v) for v in data]
    return data


class JsonFormatter(logging.Formatter):
    def __init__(self, env: str, service: str):
        super().__init__()
        self.env, self.service = env, service

    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
               "level": record.levelname, "env": self.env, "service": self.service, "module": record.name,
               "message": record.getMessage(), **context()}
        extra = getattr(record, "fields", None)
        if extra:
            out.update(scrub(extra))
        if record.exc_info:
            out["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            out["exc"] = self.formatException(record.exc_info)[-2000:]
        return json.dumps(out, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ctx = context()
        extra = getattr(record, "fields", None) or {}
        tail = " ".join(f"{k}={v}" for k, v in {**ctx, **scrub(extra)}.items())
        base = f"{datetime.fromtimestamp(record.created).strftime('%H:%M:%S')} {record.levelname:<7} {record.name}: {record.getMessage()}"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return f"{base}  [{tail}]" if tail else base


def setup_logging(service: str | None = None) -> None:
    s = get_settings()
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    h = logging.StreamHandler()
    h.setFormatter(JsonFormatter(s.env, service or s.service_name) if s.log_json else TextFormatter())
    root.addHandler(h)
    root.setLevel(s.log_level.upper())
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def log_event(logger: logging.Logger, level: int, event: str, message: str, **fields) -> None:
    logger.log(level, message, extra={"fields": {"event": event, **fields}})


class RequestContextMiddleware:
    """ASGI middleware: correlation id per request (honours X-Request-ID), timing, security headers."""

    def __init__(self, app, prod: bool = False):
        self.app = app
        self.prod = prod
        self.log = logging.getLogger("http")

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        rid = headers.get("x-request-id") or uuid.uuid4().hex[:16]
        rid = "".join(c for c in rid if c.isalnum() or c in "-_")[:64] or uuid.uuid4().hex[:16]
        token = bind(correlation_id=rid)
        start = time.perf_counter()
        status = {"code": 0}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                h = list(message.get("headers", []))
                h += [(b"x-request-id", rid.encode()), (b"x-content-type-options", b"nosniff"),
                      (b"x-frame-options", b"DENY"), (b"referrer-policy", b"no-referrer"),
                      (b"cross-origin-resource-policy", b"same-site")]
                path = scope.get("path", "")
                if not path.startswith(("/docs", "/redoc", "/openapi")):
                    h.append((b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"))
                if self.prod:
                    h.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                message["headers"] = h
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if scope["type"] == "http":
                ms = int((time.perf_counter() - start) * 1000)
                lvl = logging.WARNING if status["code"] >= 500 else logging.INFO
                if status["code"] >= 400 or ms > 1000:
                    log_event(self.log, lvl, "http.request", f"{scope.get('method')} {scope.get('path')} {status['code']}",
                              status=status["code"], duration_ms=ms)
            reset(token)
