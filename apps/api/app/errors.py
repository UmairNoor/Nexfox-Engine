"""Error categories shared by the API, scheduler and workers.

Customer-facing responses carry {detail, code, correlation_id}; internal exception text is logged, never returned.
"""
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from .observability import correlation_id, log_event

log = logging.getLogger("errors")


class ErrorCode:
    AUTH_FAILED = "AUTH_FAILED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    INVALID_CONFIG = "INVALID_CONFIG"
    VALIDATION = "VALIDATION"
    DB_ERROR = "DB_ERROR"
    DB_UNAVAILABLE = "DB_UNAVAILABLE"
    MT5_CONNECTION = "MT5_CONNECTION"
    MT5_AUTH = "MT5_AUTH"
    ACCOUNT_MISMATCH = "ACCOUNT_MISMATCH"
    ORDER_REJECTED = "ORDER_REJECTED"
    ORDER_UNCERTAIN = "ORDER_UNCERTAIN"
    EXECUTION_TIMEOUT = "EXECUTION_TIMEOUT"
    RISK_LIMIT = "RISK_LIMIT"
    WORKER_CRASH = "WORKER_CRASH"
    LEASE_LOST = "LEASE_LOST"
    ENTITLEMENT = "ENTITLEMENT"
    PAYMENT = "PAYMENT"
    EXTERNAL_API = "EXTERNAL_API"
    AI_FAILURE = "AI_FAILURE"
    RATE_LIMITED = "RATE_LIMITED"
    TRADING_HALTED = "TRADING_HALTED"
    INTERNAL = "INTERNAL"


_STATUS_CODES = {400: ErrorCode.VALIDATION, 401: ErrorCode.AUTH_FAILED, 402: ErrorCode.ENTITLEMENT, 403: ErrorCode.FORBIDDEN,
                 404: ErrorCode.NOT_FOUND, 409: ErrorCode.CONFLICT, 422: ErrorCode.VALIDATION, 423: ErrorCode.TRADING_HALTED,
                 429: ErrorCode.RATE_LIMITED, 503: ErrorCode.DB_UNAVAILABLE}


class AppError(HTTPException):
    """HTTPException with an explicit error category."""

    def __init__(self, status_code: int, detail: str, code: str):
        super().__init__(status_code, detail)
        self.code = code


def _body(detail, code: str) -> dict:
    return {"detail": detail, "code": code, "correlation_id": correlation_id()}


def install(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        code = getattr(exc, "code", None) or _STATUS_CODES.get(exc.status_code, ErrorCode.INTERNAL)
        return JSONResponse(_body(exc.detail, code), status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        errors = [{"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return JSONResponse(_body(errors, ErrorCode.VALIDATION), status_code=422)

    @app.exception_handler(OperationalError)
    async def db_down(request: Request, exc: OperationalError):
        log_event(log, logging.ERROR, "db.unavailable", "database operation failed", error=type(exc.orig).__name__ if exc.orig else "OperationalError")
        return JSONResponse(_body("The service is temporarily unavailable. Please retry shortly.", ErrorCode.DB_UNAVAILABLE), status_code=503)

    @app.exception_handler(SQLAlchemyError)
    async def db_error(request: Request, exc: SQLAlchemyError):
        log.exception("database error")
        return JSONResponse(_body("A database error occurred. It has been logged.", ErrorCode.DB_ERROR), status_code=500)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(_body("Something went wrong on our side. It has been logged.", ErrorCode.INTERNAL), status_code=500)
