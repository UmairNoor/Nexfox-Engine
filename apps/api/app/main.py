"""FastAPI entrypoint: startup checks, migrations, routers, middleware, health checks, websocket."""
import asyncio
import contextlib
import logging
import os

import jwt
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text

from . import errors
from .config import get_settings
from .db import SessionLocal, current_revision, engine, head_revision, migrate_to_head
from .deps import user_from_token
from .observability import RequestContextMiddleware, setup_logging
from .routers import accounts, admin, auth, billing, bots, data, ea_gateway, worker_gateway
from .scheduler import loop as scheduler_loop
from .scheduler import run_once as watchdog_once  # noqa: F401  (kept for callers/tests of the old name)
from .services.bus import bus

settings = get_settings()
setup_logging()
log = logging.getLogger("api")
VERSION = "2.0.0"


def startup_checks() -> None:
    problems = settings.validate_for_production()
    if problems:
        raise RuntimeError("Unsafe production configuration:\n  - " + "\n  - ".join(problems))
    if settings.auto_migrate:
        migrate_to_head()
    elif current_revision() != head_revision():
        raise RuntimeError(f"Database at {current_revision()}, code expects {head_revision()}. Run: python -m app.cli migrate")
    from .cli import _seed
    _seed(SessionLocal)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    startup_checks()
    bus.bind_loop(asyncio.get_running_loop())
    stop = asyncio.Event()
    task = asyncio.create_task(scheduler_loop(stop)) if settings.run_scheduler_in_api else None
    log.info("NexFox Engine API %s started (env=%s, db=%s)", VERSION, settings.env, engine.dialect.name)
    yield
    stop.set()
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title=settings.app_name, version=VERSION, lifespan=lifespan,
              docs_url=None if settings.is_prod else "/docs", redoc_url=None, openapi_url=None if settings.is_prod else "/openapi.json")
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
                   allow_credentials=False, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                   allow_headers=["Authorization", "Content-Type", "X-Request-ID"], expose_headers=["X-Request-ID"])
app.add_middleware(RequestContextMiddleware, prod=settings.is_prod)
errors.install(app)
for r in (auth.router, billing.router, accounts.router, bots.router, data.router, ea_gateway.router, worker_gateway.router, admin.router):
    app.include_router(r)


@app.get("/downloads/AurumScalp.ex5")
def download_ea():
    """Serves the compiled EA. Build apps/ea/AurumScalp.mq5 in MetaEditor and copy the .ex5 into EA_DOWNLOAD_DIR."""
    path = os.path.join(settings.ea_download_dir, "AurumScalp.ex5")
    if not os.path.exists(path):
        raise HTTPException(404, "The EA file is not published yet. Compile apps/ea/AurumScalp.mq5 in MetaEditor and copy the .ex5 into EA_DOWNLOAD_DIR.")
    return FileResponse(path, filename="AurumScalp.ex5", media_type="application/octet-stream")


@app.get("/health")
def health():
    """Liveness: the process is up. Does not touch the database (use /health/ready for that)."""
    return {"ok": True, "version": VERSION, "env": settings.env}


@app.get("/health/ready")
def ready():
    """Readiness: database reachable and schema at the expected migration."""
    try:
        with engine.connect() as c:
            c.execute(text("select 1"))
        cur, head = current_revision(), head_revision()
    except Exception as e:
        return JSONResponse({"ok": False, "database": "unreachable", "error": type(e).__name__}, status_code=503)
    ok = cur == head
    return JSONResponse({"ok": ok, "database": "ok", "migration": cur, "expected": head}, status_code=200 if ok else 503)


@app.websocket("/ws")
async def ws(websocket: WebSocket, ticket: str | None = None):
    """Live channel for the dashboard. Authenticate with a 60-second ticket from POST /api/v1/auth/ws-ticket
    (access tokens are not accepted in URLs, where proxies and browsers would log them)."""
    try:
        with SessionLocal() as db:
            user = user_from_token(db, ticket or "", "ws")
            tenant_id = user.tenant_id
    except (HTTPException, jwt.PyJWTError, ValueError):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    q = bus.subscribe(tenant_id)
    try:
        await websocket.send_json({"event": "hello", "data": {"tenant_id": tenant_id}})
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=25)
                await websocket.send_json(msg)
            except asyncio.TimeoutError:
                await websocket.send_json({"event": "ping", "data": {}})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        bus.unsubscribe(tenant_id, q)
