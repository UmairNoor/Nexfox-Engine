"""Regression tests for the defects found in the 2026-10-10 security / trading-safety review (each was reproduced first)."""
import uuid

import pytest

from conftest import make_admin, post_webhook, signup
from test_platform import _bind, _sync, _worker


@pytest.fixture(scope="module")
def admin(client):
    return make_admin(client, f"sec-admin-{uuid.uuid4().hex[:6]}@example.com")


def _email():
    return f"sec-{uuid.uuid4().hex[:8]}@example.com"


def _running_instance(client, admin, login):
    o = signup(client, _email())
    acct = _bind(client, o["h"], login)
    client.put(f"/api/v1/accounts/{acct['id']}/credentials", json={"password": "pw-demo"}, headers=o["h"])
    iid = acct["instance"]["id"]
    assert client.post(f"/api/v1/instances/{iid}/actions", json={"op": "start"}, headers=o["h"]).status_code == 200
    w, _ = _worker(client, admin["h"])
    spec = next(x for x in _sync(client, w, free=50)["instances"] if x["instance_id"] == iid)
    rep = {"id": iid, "lease_epoch": spec["lease_epoch"], "run_id": spec["run_id"], "state": "running"}
    _sync(client, w, [rep])
    return o, acct, iid, w, spec, rep


# ---------------------------------------------------------------- sessions
def test_old_refresh_token_cannot_sign_out_new_sessions_after_reset(client):
    o = signup(client, _email(), plan=None)
    stolen = o["refresh"]
    assert client.post("/api/v1/auth/password/forgot", json={"email": o["email"]}).status_code == 200
    assert client.post("/api/v1/auth/password/reset", json={"email": o["email"], "code": "123456",
                                                            "password": "another-long-password-2!"}).status_code == 200
    fresh = client.post("/api/v1/auth/login", json={"email": o["email"], "password": "another-long-password-2!"}).json()
    h = {"Authorization": f"Bearer {fresh['access_token']}"}
    # the attacker replays the pre-reset token: refused, and it must NOT revoke the new session
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": stolen}).status_code == 401
    assert client.get("/api/v1/auth/me", headers=h).status_code == 200
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": fresh["refresh_token"]}).status_code == 200


def test_login_does_not_reveal_accounts_or_lock_state(client):
    o = signup(client, _email(), plan=None)
    for _ in range(5):
        assert client.post("/api/v1/auth/login", json={"email": o["email"], "password": "wrong-password-xx"}).status_code == 401
    locked = client.post("/api/v1/auth/login", json={"email": o["email"], "password": "wrong-password-xx"})
    missing = client.post("/api/v1/auth/login", json={"email": _email(), "password": "wrong-password-xx"})
    assert locked.status_code == missing.status_code == 401
    assert locked.json()["detail"] == missing.json()["detail"]


def test_reset_code_dies_after_five_wrong_guesses(client):
    o = signup(client, _email(), plan=None)
    client.post("/api/v1/auth/password/forgot", json={"email": o["email"]})
    for _ in range(5):
        r = client.post("/api/v1/auth/password/reset", json={"email": o["email"], "code": "000000", "password": "brute-force-password-1"})
        assert r.status_code == 400
    # even the right code no longer works: a new one must be requested
    r = client.post("/api/v1/auth/password/reset", json={"email": o["email"], "code": "123456", "password": "brute-force-password-1"})
    assert r.status_code == 400


# ---------------------------------------------------------------- billing
def test_zero_amount_invoice_does_not_turn_trial_into_paid(client):
    o = signup(client, _email())  # mock checkout starts the 7-day trial
    tid = o["user"]["tenant_id"]
    assert client.get("/api/v1/auth/me", headers=o["h"]).json()["subscription"]["status"] == "trialing"
    post_webhook(client, {"id": f"evt_{uuid.uuid4().hex}", "type": "invoice.paid",
                          "data": {"tenant_id": tid, "provider_invoice_id": f"in_{uuid.uuid4().hex[:8]}", "amount_cents": 0}})
    assert client.get("/api/v1/auth/me", headers=o["h"]).json()["subscription"]["status"] == "trialing"
    r = client.post("/api/v1/accounts", json={"login": "7300001", "server": "Fake-Live", "account_type": "live"}, headers=o["h"])
    assert r.status_code == 403  # trial stays demo-only
    post_webhook(client, {"id": f"evt_{uuid.uuid4().hex}", "type": "invoice.paid",
                          "data": {"tenant_id": tid, "provider_invoice_id": f"in_{uuid.uuid4().hex[:8]}", "amount_cents": 9900}})
    assert client.get("/api/v1/auth/me", headers=o["h"]).json()["subscription"]["status"] == "active"


# ---------------------------------------------------------------- bot config / lifecycle
def test_customer_rollback_keeps_admin_only_values(client, admin):
    o = signup(client, _email())
    iid = _bind(client, o["h"], "7300002")["instance"]["id"]
    url = f"/api/v1/instances/{iid}/config"
    r = client.put(f"/api/v1/admin/instances/{iid}/config", json={"config": {"magic_number": 55555}, "reason": "magic clash"},
                   headers=admin["h"])
    assert r.status_code == 200, r.text
    r = client.post(f"{url}/rollback", json={"config_version": 1}, headers=o["h"])
    assert r.status_code in (200,)
    assert client.get(f"/api/v1/instances/{iid}", headers=o["h"]).json()["config"]["magic_number"] == 55555


def test_close_all_refused_while_stopped_and_never_inherited(client, admin):
    o, acct, iid, w, spec, rep = _running_instance(client, admin, "7300003")
    act = f"/api/v1/instances/{iid}/actions"
    # queued while running, then the bot is stopped before the worker acks it
    assert client.post(act, json={"op": "close_all", "confirm": "CLOSE"}, headers=o["h"]).status_code == 200
    client.post(act, json={"op": "stop"}, headers=o["h"])
    _sync(client, w, [{**rep, "state": "stopped"}])
    r = client.post(act, json={"op": "close_all", "confirm": "CLOSE"}, headers=o["h"])
    assert r.status_code == 409 and "stopped" in r.json()["detail"]
    assert client.post("/api/v1/kill-switch", headers=o["h"]).status_code == 200  # skips the stopped bot instead of failing
    # next start: the old close-all must not ride along into the new run
    assert client.post(act, json={"op": "start"}, headers=o["h"]).status_code == 200
    fresh = next(x for x in _sync(client, w, free=50)["instances"] if x["instance_id"] == iid)
    assert fresh["fresh"] and fresh["commands"] == []


def test_unacked_commands_are_redelivered(client, admin):
    o, acct, iid, w, spec, rep = _running_instance(client, admin, "7300004")
    cid = client.post(f"/api/v1/instances/{iid}/actions", json={"op": "close_all", "confirm": "CLOSE"}, headers=o["h"]).json()["command_id"]
    first = next(x for x in _sync(client, w, [rep])["instances"] if x["instance_id"] == iid)
    again = next(x for x in _sync(client, w, [rep])["instances"] if x["instance_id"] == iid)  # spec lost, no ack yet
    assert {"id": cid, "type": "close_all"} in first["commands"] and {"id": cid, "type": "close_all"} in again["commands"]
    _sync(client, w, [{**rep, "acked_commands": [cid]}])
    after = next(x for x in _sync(client, w, [rep])["instances"] if x["instance_id"] == iid)
    assert all(c["id"] != cid for c in after["commands"])


# ---------------------------------------------------------------- order intents
def test_intent_replay_and_stop_loss_side(client, admin):
    o, acct, iid, w, spec, rep = _running_instance(client, admin, "7300005")
    url = f"/worker/v1/instances/{iid}/intents"
    base = {"lease_epoch": spec["lease_epoch"], "run_id": spec["run_id"], "symbol": "GOLD", "side": "BUY", "volume": 0.02,
            "price": 2400.0, "sl": 2390.0, "tp": 2420.0}
    assert client.post(url, json={**base, "client_order_id": "sec00000wrong1", "sl": 2410.0}, headers=w).json()["code"] == "RISK_LIMIT"
    assert client.post(url, json={**base, "client_order_id": "sec00000wrong2", "sl": -5}, headers=w).json()["code"] == "RISK_LIMIT"
    assert client.post(url, json={**base, "client_order_id": "sec00000wrong3", "tp": 2380.0}, headers=w).json()["code"] == "RISK_LIMIT"
    coid = "sec00000ok0001"
    assert client.post(url, json={**base, "client_order_id": coid}, headers=w).json()["approved"] is True
    # same id, different order: refused
    assert client.post(url, json={**base, "client_order_id": coid, "volume": 0.05}, headers=w).status_code == 409
    client.post(f"{url}/{coid}/result", json={"lease_epoch": spec["lease_epoch"], "status": "filled", "broker_order": "9"}, headers=w)
    # replay after the order already has an outcome: never approved for sending again
    r = client.post(url, json={**base, "client_order_id": coid}, headers=w).json()
    assert r["approved"] is False and r["status"] == "filled"


def test_intent_replay_rechecks_pause(client, admin):
    o, acct, iid, w, spec, rep = _running_instance(client, admin, "7300006")
    url = f"/worker/v1/instances/{iid}/intents"
    body = {"lease_epoch": spec["lease_epoch"], "symbol": "GOLD", "side": "SELL", "volume": 0.02, "price": 2400.0, "sl": 2410.0,
            "client_order_id": "sec00000pause1"}
    assert client.post(url, json=body, headers=w).json()["approved"] is True
    client.post(f"/api/v1/instances/{iid}/actions", json={"op": "pause"}, headers=o["h"])
    r = client.post(url, json=body, headers=w)  # retry after a lost response, but the owner paused meanwhile
    assert r.status_code == 409
    orders = client.get(f"/api/v1/instances/{iid}/orders", headers=o["h"]).json()
    assert orders["counts"].get("rejected") == 1 and not orders["counts"].get("approved")


# ---------------------------------------------------------------- accounts / EA
def test_revoked_account_cannot_trade_via_ea_or_be_reconfigured(client, admin):
    o = signup(client, _email())
    post_webhook(client, {"id": f"evt_{uuid.uuid4().hex}", "type": "invoice.paid",
                          "data": {"tenant_id": o["user"]["tenant_id"], "provider_invoice_id": f"in_{uuid.uuid4().hex[:8]}", "amount_cents": 9900}})
    acct = client.post("/api/v1/accounts", json={"login": "7300007", "server": "Broker-Demo", "account_type": "demo"}, headers=o["h"]).json()
    key = client.get("/api/v1/billing/subscription", headers=o["h"]).json()["license"]["key"]
    hs = {"license_key": key, "login": "7300007", "server": "Broker-Demo", "account_type": "demo"}
    assert client.post("/ea/v1/handshake", json=hs).status_code == 200
    assert client.post(f"/api/v1/admin/accounts/{acct['id']}/revoke", json={"reason": "abuse report"}, headers=admin["h"]).status_code == 200
    r = client.post("/ea/v1/handshake", json=hs)
    assert r.status_code == 403 and r.json()["detail"] == "account_revoked"
    assert client.put(f"/api/v1/accounts/{acct['id']}/credentials", json={"password": "pw-demo"}, headers=o["h"]).status_code == 409


def test_viewer_cannot_run_diagnostics(client):
    from app.db import SessionLocal
    from app.models import User
    o = signup(client, _email())
    iid = _bind(client, o["h"], "7300008")["instance"]["id"]
    v = signup(client, _email(), plan=None)
    with SessionLocal() as db:  # make the second user a viewer in the owner's workspace
        u = db.get(User, v["user"]["id"])
        u.tenant_id, u.role = o["user"]["tenant_id"], "viewer"
        db.commit()
    vh = {"Authorization": f"Bearer {client.post('/api/v1/auth/login', json={'email': v['email'], 'password': v['password']}).json()['access_token']}"}
    assert client.post(f"/api/v1/instances/{iid}/diagnose", headers=vh).status_code == 403
    assert client.post(f"/api/v1/instances/{iid}/diagnose", headers=o["h"]).status_code == 200


def test_websocket_refuses_access_token_in_url(client):
    from starlette.websockets import WebSocketDisconnect
    o = signup(client, _email(), plan=None)
    token = o["h"]["Authorization"].split()[1]
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws?token={token}") as ws:
            ws.receive_json()
