"""Database engine, sessions, tenant scoping for PostgreSQL Row-Level Security, and Alembic helpers.

PostgreSQL is the production database. SQLite is supported only so the test-suite runs without a server.
"""
import logging
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

settings = get_settings()
log = logging.getLogger("db")
_sqlite = settings.database_url.startswith("sqlite")
_kwargs = {"connect_args": {"check_same_thread": False}} if _sqlite else {"pool_size": settings.db_pool_size, "max_overflow": 10}
engine = create_engine(settings.database_url, pool_pre_ping=True, **_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
API_DIR = Path(__file__).resolve().parents[1]


class Base(DeclarativeBase):
    pass


def is_postgres() -> bool:
    return engine.dialect.name == "postgresql"


@event.listens_for(Session, "after_begin")
def _apply_tenant_scope(session: Session, transaction, connection) -> None:
    """RLS defence-in-depth: `set_config(..., true)` is transaction-local, so re-apply it at the start of EVERY
    transaction in the session (a request may commit several times). Admin/worker sessions set no tenant."""
    tenant = session.info.get("tenant_id")
    if tenant is not None and connection.dialect.name == "postgresql":
        connection.execute(text("select set_config('app.tenant_id', :t, true)"), {"t": str(tenant)})


def scope_to_tenant(db: Session, tenant_id: int | None) -> None:
    db.info["tenant_id"] = tenant_id
    if tenant_id is not None and is_postgres() and db.in_transaction():
        db.execute(text("select set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)})


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---- migrations ----
def _alembic_config():
    from alembic.config import Config
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    cfg.attributes["configure_logger"] = False
    return cfg


def head_revision() -> str:
    from alembic.script import ScriptDirectory
    return ScriptDirectory.from_config(_alembic_config()).get_current_head()


def current_revision() -> str | None:
    from alembic.runtime.migration import MigrationContext
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def migrate_to_head() -> None:
    """Apply pending migrations. A database created by the old `create_all` (tables but no alembic_version)
    is stamped at the 0001 baseline first, so its data is kept and only new changes are applied."""
    from alembic import command
    from sqlalchemy import inspect
    cfg = _alembic_config()
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        names = set(inspect(conn).get_table_names())
        if "alembic_version" not in names and "users" in names:
            log.warning("existing schema without migration history: stamping baseline 0001")
            command.stamp(cfg, "0001")
        command.upgrade(cfg, "head")
