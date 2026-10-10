"""Migrations: fresh upgrade/downgrade, upgrading a legacy create_all database in place, PostgreSQL DDL (offline)."""
import io
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

API = Path(__file__).resolve().parents[1]


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": url}
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=API, env=env, capture_output=True, text=True, timeout=120)


def test_fresh_upgrade_downgrade_upgrade():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        url = f"sqlite:///{Path(d) / 'm.db'}"
        for step in (("upgrade", "head"), ("downgrade", "base"), ("upgrade", "head")):
            r = _alembic(url, *step)
            assert r.returncode == 0, r.stderr
        eng = create_engine(url)
        names = set(inspect(eng).get_table_names())
        eng.dispose()
        assert {"bots", "bot_versions", "bot_instances", "bot_runs", "bot_events", "order_intents", "workers", "worker_jobs",
                "auth_sessions", "ai_agents", "ai_tasks", "platform_settings", "users", "positions"} <= names
        r = _alembic(url, "check")
        assert r.returncode == 0, r.stdout + r.stderr  # models and migrations agree


def test_legacy_create_all_database_is_upgraded_in_place_keeping_data():
    """Databases created by the old `create_all` start-up get stamped at 0001 and upgraded, rows preserved."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        url = f"sqlite:///{Path(d) / 'legacy.db'}"
        assert _alembic(url, "upgrade", "0001").returncode == 0
        eng = create_engine(url)
        with eng.begin() as c:
            c.execute(text("DROP TABLE alembic_version"))  # exactly what an old create_all database looks like
            c.execute(text("INSERT INTO tenants (id, name, status, created_at) VALUES (1, 'Legacy Co', 'active', '2026-01-01')"))
        code = ("import sys; sys.path.insert(0, '.'); from app.db import migrate_to_head, current_revision, head_revision;"
                "migrate_to_head(); assert current_revision() == head_revision(); print('ok')")
        r = subprocess.run([sys.executable, "-c", code], cwd=API, env={**os.environ, "DATABASE_URL": url}, capture_output=True, text=True, timeout=120)
        assert r.returncode == 0 and "ok" in r.stdout, r.stderr
        with eng.connect() as c:
            assert c.execute(text("SELECT name FROM tenants WHERE id = 1")).scalar() == "Legacy Co"
            assert c.execute(text("SELECT trial_consumed_at FROM tenants WHERE id = 1")).scalar() is None
            assert "bot_instances" in inspect(c).get_table_names()
        eng.dispose()


def test_postgresql_ddl_offline():
    """Render the PostgreSQL migration SQL without a server: JSONB documents, partial unique index, JSON->JSONB."""
    r = _alembic("postgresql+psycopg://u:p@offline/db", "upgrade", "head", "--sql")
    assert r.returncode == 0, r.stderr
    sql = r.stdout
    assert "config JSONB NOT NULL" in sql and "manifest JSONB NOT NULL" in sql
    assert "CREATE UNIQUE INDEX uq_instance_account_active ON bot_instances (account_id) WHERE archived_at IS NULL" in sql
    assert "ALTER TABLE plans ALTER COLUMN features TYPE JSONB USING features::jsonb" in sql
    assert "fk_bots_active_version" in sql
    _ = io, redirect_stdout
