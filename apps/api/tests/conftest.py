"""Shared test setup: one throw-away SQLite database per test session, mock billing, generous rate limits.

PostgreSQL-specific behaviour (JSONB, RLS) is covered by tests/test_migrations.py rendering the PostgreSQL DDL offline;
running the suite against a real PostgreSQL is done by exporting DATABASE_URL=postgresql+psycopg://... before pytest.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_db.close()
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_db.name}")
os.environ["ENV"] = "test"
os.environ["BILLING_PROVIDER"] = "mock"
os.environ["RATE_LIMIT_AUTH_PER_MIN"] = "100000"
os.environ["RATE_LIMIT_GATEWAY_PER_MIN"] = "100000"
os.environ["RUN_SCHEDULER_IN_API"] = "false"
os.environ["DEV_SHOW_EMAIL_CODES"] = "true"
os.environ["TELEGRAM_CALLBACK_SECRET"] = "test-telegram-secret"
os.environ["TELEGRAM_BOT_USERNAME"] = "NexFoxTestBot"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pyotp  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="session")
def client():
    from app import main
    from app.routers import auth as auth_router
    auth_router.new_code = lambda: "123456"  # deterministic email code for tests
    with TestClient(main.app) as c:
        yield c


def post_webhook(client, evt: dict):
    from app.services.payments import mock_signature
    raw = json.dumps(evt).encode()
    return client.post("/api/v1/billing/webhook", content=raw,
                       headers={"Content-Type": "application/json", "X-Mock-Signature": mock_signature(raw)})


def signup(client, email: str, plan: str | None = "pro", password: str = "a-very-long-password-1!") -> dict:
    r = client.post("/api/v1/auth/signup", json={"name": "Test User", "email": email, "password": password, "accept_risk": True})
    assert r.status_code == 200, r.text
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    out = {"h": h, "refresh": r.json()["refresh_token"], "user": r.json()["user"], "email": email, "password": password}
    if plan:
        assert client.post("/api/v1/billing/checkout", json={"plan": plan}, headers=h).status_code == 200
    return out


def make_admin(client, email: str, role: str = "admin") -> dict:
    """Create a staff user directly (as `python -m app.cli create-admin` does) and log in with password + TOTP."""
    from app.db import SessionLocal
    from app.models import Tenant, User
    from app.security import encrypt, hash_password, new_totp_secret
    secret = new_totp_secret()
    with SessionLocal() as db:
        t = Tenant(name="staff")
        db.add(t)
        db.flush()
        db.add(User(tenant_id=t.id, email=email, name="Staff", password_hash=hash_password("staff-password-123"), role=role,
                    email_verified=True, totp_secret_enc=encrypt(secret), totp_enabled=True))
        db.commit()
    r = client.post("/api/v1/auth/login", json={"email": email, "password": "staff-password-123"}).json()
    r2 = client.post("/api/v1/auth/2fa/verify", json={"challenge": r["challenge"], "code": pyotp.TOTP(secret).now()})
    assert r2.status_code == 200, r2.text
    return {"h": {"Authorization": f"Bearer {r2.json()['access_token']}"}, "secret": secret}
