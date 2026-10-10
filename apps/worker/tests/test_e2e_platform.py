"""End-to-end: real API server (uvicorn, own venv, temp SQLite) + real worker supervisor + bot processes on the
SIMULATED MT5 backend. Customer journey from signup to safe shutdown, plus admin monitoring, worker crash recovery
and bad credentials. No broker, no terminal, no money is involved.

Skips if the API virtualenv (apps/api/.venv) is not installed.
"""
import base64
import hashlib
import hmac
import os
import re
import socket
import struct
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import httpx
import pytest
from conftest import BOTS, DEMO, REPO

from nexfox_worker.config import WorkerConfig
from nexfox_worker.supervisor import Supervisor

API_DIR = REPO / "apps" / "api"
API_PY = API_DIR / ".venv" / "Scripts" / "python.exe"
if not API_PY.exists():
    API_PY = API_DIR / ".venv" / "bin" / "python"
pytestmark = pytest.mark.skipif(not API_PY.exists(), reason="API virtualenv not installed")


def totp(secret: str) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    h = hmac.new(key, struct.pack(">Q", int(time.time()) // 30), hashlib.sha1).digest()
    o = h[-1] & 15
    return f"{(struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def wait(fn, timeout=90.0, every=0.4, what="condition"):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = fn()
        if last:
            return last
        time.sleep(every)
    raise AssertionError(f"timed out waiting for {what}; last={last!r}")


@pytest.fixture(scope="module")
def stack():
    tmp = Path(tempfile.mkdtemp(prefix="nexfox-e2e-"))
    port = socket.socket()
    port.bind(("127.0.0.1", 0))
    p = port.getsockname()[1]
    port.close()
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{tmp / 'e2e.db'}", "ENV": "test", "BILLING_PROVIDER": "mock",
           "RUN_SCHEDULER_IN_API": "false", "RATE_LIMIT_AUTH_PER_MIN": "10000", "RATE_LIMIT_GATEWAY_PER_MIN": "100000",
           "WORKER_LEASE_SECONDS": "4", "DEV_SHOW_EMAIL_CODES": "true", "LOG_JSON": "false", "PYTHONUNBUFFERED": "1"}
    log = (tmp / "api.log").open("w", encoding="utf-8")
    srv = subprocess.Popen([str(API_PY), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(p)], cwd=API_DIR, env=env,
                           stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{p}"
    c = httpx.Client(base_url=base, timeout=20)
    wait(lambda: _ok(c, "/health/ready"), 60, what="API ready")
    out = subprocess.run([str(API_PY), "-m", "app.cli", "create-worker", "e2e-worker", "2"], cwd=API_DIR, env=env, capture_output=True, text=True)
    token = re.search(r"WORKER_TOKEN \(shown once\): (\S+)", out.stdout).group(1)
    out = subprocess.run([str(API_PY), "-m", "app.cli", "create-admin", "ops@example.com", "Ops"], cwd=API_DIR,
                         env={**env, "ADMIN_PASSWORD": "ops-password-123"}, capture_output=True, text=True)
    admin_secret = re.search(r"TOTP secret .*?: (\S+)", out.stdout).group(1)
    cfg = WorkerConfig(api_url=base, token=token, name="e2e", terminals=["fake-terminal-a", "fake-terminal-b"], data_dir=tmp / "worker",
                       bots_dir=BOTS, mt5_backend="fake", sync_interval=0.5, heartbeat_interval=0.5, log_json=False, fast=True,
                       fake_config={"accounts": DEMO, "seed": 7, "exact_start": True, "start_minute": 87000})
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    sup = Supervisor(cfg)
    th = threading.Thread(target=sup.run_forever, daemon=True)
    th.start()
    yield {"c": c, "sup": sup, "log": tmp / "api.log", "admin_secret": admin_secret}
    sup.stop_event.set()
    th.join(timeout=60)
    srv.terminate()
    srv.wait(timeout=20)
    log.close()


def _ok(c, path):
    try:
        return c.get(path).status_code == 200
    except httpx.HTTPError:
        return False


def _code_from_log(log: Path, email: str, purpose: str = "verify") -> str:
    m = re.findall(rf"DEV email to {re.escape(email)}: .*?\| {purpose} code (\d{{6}})", log.read_text(encoding="utf-8"))
    return m[-1]


def _signup(s, email):
    c = s["c"]
    r = c.post("/api/v1/auth/signup", json={"name": "E2E User", "email": email, "password": "e2e-password-123!", "accept_risk": True})
    assert r.status_code == 200, r.text
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    code = wait(lambda: _code_from_log(s["log"], email), 10, what="verification email")
    assert c.post("/api/v1/auth/verify-email", json={"code": code}, headers=h).status_code == 200
    r = c.post("/api/v1/billing/checkout", json={"plan": "pro"}, headers=h)
    assert r.status_code == 200 and r.json()["trial"] is True
    return h


def test_customer_journey_admin_crash_recovery_and_safe_shutdown(stack):
    c = stack["c"]
    h = _signup(stack, "trader@example.com")
    me = c.get("/api/v1/auth/me", headers=h).json()
    assert me["entitlement"]["valid"] and me["entitlement"]["trial"]

    # 1) connect an MT5 account and pick Rehman Bot
    acct = c.post("/api/v1/accounts", json={"login": "7100001", "server": "Fake-Demo", "account_type": "demo", "bot": "rehman_liquidity"}, headers=h).json()
    iid, aid = acct["instance"]["id"], acct["id"]
    assert acct["connection_status"] == "unverified"  # a DB row is not a connection
    assert c.put(f"/api/v1/accounts/{aid}/credentials", json={"password": "demo-pass"}, headers=h).status_code == 200

    # 2) real connection check on the worker (simulated terminal)
    assert c.post(f"/api/v1/accounts/{aid}/verify", headers=h).status_code == 200
    a = wait(lambda: next((x for x in c.get("/api/v1/accounts", headers=h).json() if x["id"] == aid and x["connection_status"] == "ok"), None),
             what="account verified")
    assert a["reported_trade_mode"] == "demo" and a["last_verified_at"]

    # 3) configure (validated) and start
    assert c.put(f"/api/v1/instances/{iid}/config", json={"config": {"risk_percent": 9}}, headers=h).status_code == 422
    assert c.put(f"/api/v1/instances/{iid}/config", json={"config": {"risk_percent": 1.0}}, headers=h).status_code == 200
    assert c.post(f"/api/v1/instances/{iid}/actions", json={"op": "start"}, headers=h).json()["state"] == "pending"
    wait(lambda: c.get(f"/api/v1/instances/{iid}", headers=h).json()["state"] == "running", what="instance running")

    # 4) the bot trades through server-approved intents; events and positions are recorded
    orders = wait(lambda: (o := c.get(f"/api/v1/instances/{iid}/orders", headers=h).json())["counts"].get("filled", 0) >= 2 and o,
                  timeout=180, what="orders filled")
    assert all(x["sl"] for x in orders["items"]), "every order carried a stop loss"
    trades = wait(lambda: (d := c.get(f"/api/v1/dashboard?account_id={aid}", headers=h).json())["open_positions"] and d, timeout=60,
                  what="open positions")
    assert trades["account"]["balance"] is not None  # reported by the bot heartbeat, not assumed
    ev = c.get(f"/api/v1/instances/{iid}/events?limit=200", headers=h).json()["items"]
    msgs = " | ".join(e["message"] for e in ev)
    assert "Connected to 7100001@Fake-Demo" in msgs and "Order approved" in msgs
    runs = c.get(f"/api/v1/instances/{iid}/runs", headers=h).json()
    assert runs[0]["runtime"]["backend"] == "fake" and runs[0]["runtime"]["strategy_files"]["simple_bot.py"]

    # 5) admin monitors it
    lg = c.post("/api/v1/auth/login", json={"email": "ops@example.com", "password": "ops-password-123"}).json()
    ah = {"Authorization": f"Bearer {c.post('/api/v1/auth/2fa/verify', json={'challenge': lg['challenge'], 'code': totp(stack['admin_secret'])}).json()['access_token']}"}
    inst = next(i for i in c.get("/api/v1/admin/instances", headers=ah).json() if i["id"] == iid)
    assert inst["state"] == "running" and inst["worker_id"]
    w = c.get("/api/v1/admin/workers", headers=ah).json()[0]
    assert w["online"] and w["capabilities"]["bots"]["rehman_liquidity"] == ["1.0.0"]
    assert "password" not in c.get(f"/api/v1/admin/instances/{iid}", headers=ah).text.lower().replace("has_credentials", "")

    # 6) another customer sees nothing of it
    h2 = _signup(stack, "other@example.com")
    assert c.get(f"/api/v1/instances/{iid}", headers=h2).status_code == 404
    assert c.post(f"/api/v1/instances/{iid}/actions", json={"op": "stop"}, headers=h2).status_code == 404

    # 7) close all = explicit operation: positions closed at market and recorded
    assert c.post(f"/api/v1/instances/{iid}/actions", json={"op": "close_all", "confirm": "CLOSE"}, headers=h).status_code == 200
    wait(lambda: not c.get(f"/api/v1/dashboard?account_id={aid}", headers=h).json()["open_positions"], timeout=60, what="positions closed")
    closed = c.get("/api/v1/trades?range=7d", headers=h).json()  # simulated clock may sit on the last weekday
    assert closed["count"] >= 2

    # 8) worker process crash: bot process killed -> detected -> re-claimed with a new lease epoch and a new run
    sup = stack["sup"]
    child = wait(lambda: sup.children.get(iid), 10, what="child process")
    old_epoch = child.spec["lease_epoch"]
    child.proc.kill()
    wait(lambda: (ch := sup.children.get(iid)) is not None and ch.spec["lease_epoch"] > old_epoch and ch.proc.is_alive(), timeout=60,
         what="instance re-claimed after crash")
    wait(lambda: c.get(f"/api/v1/instances/{iid}", headers=h).json()["state"] == "running", what="running again")
    assert len(c.get(f"/api/v1/instances/{iid}/runs", headers=h).json()) >= 2
    ev = c.get(f"/api/v1/instances/{iid}/events?limit=200", headers=h).json()["items"]
    assert any("process exited unexpectedly" in e["message"] for e in ev)

    # 9) diagnostics assistant summarises it (labelled as generated)
    d = c.post(f"/api/v1/instances/{iid}/diagnose", headers=h).json()
    assert d["status"] == "succeeded" and d["output"]["generated"] is True

    # 10) safe stop: the process ends, the lease is released, nothing is closed by stopping
    assert c.post(f"/api/v1/instances/{iid}/actions", json={"op": "stop"}, headers=h).status_code == 200
    final = wait(lambda: (i := c.get(f"/api/v1/instances/{iid}", headers=h).json())["state"] == "stopped" and i, what="stopped")
    assert final["worker_id"] is None
    wait(lambda: iid not in sup.children, 30, what="child reaped")
    ev = c.get(f"/api/v1/instances/{iid}/events?limit=50", headers=h).json()["items"]
    assert any("Open positions keep their broker-side SL/TP" in e["message"] for e in ev)


def test_bad_mt5_password_fails_cleanly(stack):
    c = stack["c"]
    h = _signup(stack, "badpw@example.com")
    acct = c.post("/api/v1/accounts", json={"login": "7100001", "server": "Fake-Demo-2", "bot": "rehman_liquidity"}, headers=h)
    # the same login on another server is a different account; the simulated terminal only knows Fake-Demo
    acct = acct.json()
    iid = acct["instance"]["id"]
    c.put(f"/api/v1/accounts/{acct['id']}/credentials", json={"password": "wrong-pass"}, headers=h)
    c.post(f"/api/v1/instances/{iid}/actions", json={"op": "start"}, headers=h)
    i = wait(lambda: (x := c.get(f"/api/v1/instances/{iid}", headers=h).json())["state"] == "failed" and x, timeout=60, what="failed")
    assert i["account"]["connection_status"] == "auth_failed" and "login failed" in i["last_error"]
    assert c.get(f"/api/v1/instances/{iid}/orders", headers=h).json()["items"] == []  # nothing was ever sent
    c.post(f"/api/v1/instances/{iid}/actions", json={"op": "stop"}, headers=h)
