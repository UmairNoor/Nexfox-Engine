"""Multi-bot platform: bots, instances, worker leases, order-intent safety, admin, auth hardening, trial, agents."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from conftest import make_admin, post_webhook, signup


def _bind(client, h, login, bot="rehman_liquidity", server="Fake-Demo", account_type="demo"):
    r = client.post("/api/v1/accounts", json={"login": login, "server": server, "account_type": account_type, "bot": bot}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _worker(client, admin_h, name=None):
    r = client.post("/api/v1/admin/workers", json={"name": name or f"w-{uuid.uuid4().hex[:6]}", "max_instances": 4}, headers=admin_h)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}, r.json()["id"]


def _sync(client, wh, instances=(), free=2, bots=None):
    r = client.post("/worker/v1/sync", json={"version": "1.0.0", "host": "test", "bots": bots or {"rehman_liquidity": ["1.0.0"]},
                                              "free_slots": free, "instances": list(instances)}, headers=wh)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def admin(client):
    return make_admin(client, "root@example.com")


@pytest.fixture(scope="module")
def owner(client):
    o = signup(client, "owner@example.com")
    acct = _bind(client, o["h"], "7000001")
    o["acct"] = acct
    o["iid"] = acct["instance"]["id"]
    return o


# ---------------------------------------------------------------- catalogue, assignment, config
def test_catalogue_lists_registered_bots(client, owner):
    bots = {b["code"]: b for b in client.get("/api/v1/bots", headers=owner["h"]).json()}
    assert {"rehman_liquidity", "aurumscalp"} <= set(bots)
    rb = bots["rehman_liquidity"]
    assert rb["execution_model"] == "mt5_python" and rb["version"] == "1.0.0" and rb["available"]
    keys = {f["key"] for f in rb["config_schema"]["fields"]}
    assert {"symbol", "risk_percent", "max_lot", "dry_run"} <= keys
    # defaults equal the original bots/rehman_bot/config_new.py values
    assert rb["default_config"]["symbol"] == "GOLD" and rb["default_config"]["risk_percent"] == 1.0
    assert rb["default_config"]["max_lot"] == 0.10 and rb["default_config"]["magic_number"] == 88880001


def test_assigned_instance_starts_stopped(client, owner):
    i = client.get(f"/api/v1/instances/{owner['iid']}", headers=owner["h"]).json()
    assert i["bot"]["code"] == "rehman_liquidity" and i["desired_state"] == "stopped" and i["state"] == "stopped"
    assert i["account"]["has_credentials"] is False and i["account"]["connection_status"] == "unverified"
    # one bot per account
    r = client.post("/api/v1/instances", json={"account_id": owner["acct"]["id"], "bot": "aurumscalp"}, headers=owner["h"])
    assert r.status_code == 409


def test_config_validation(client, owner):
    url = f"/api/v1/instances/{owner['iid']}/config"
    bad = [({"risk_percent": 9}, "between"), ({"nope": 1}, "not a setting"), ({"max_lot": 50}, "between"),
           ({"min_lot": 0.5, "max_lot": 0.2}, "Minimum lot"), ({"magic_number": 5}, "administrator"),
           ({"lot_step": 0.1}, "administrator"), ({"dry_run": "yes"}, "true or false"), ({"symbol": "bad symbol!"}, "invalid")]
    for cfg, msg in bad:
        r = client.put(url, json={"config": cfg}, headers=owner["h"])
        assert r.status_code == 422 and r.json()["code"] == "INVALID_CONFIG" and msg in r.json()["detail"], (cfg, r.text)
    v = client.post(f"/api/v1/instances/{owner['iid']}/validate", json={"config": {"risk_percent": 9}}, headers=owner["h"]).json()
    assert v["valid"] is False and v["problems"]
    r = client.put(url, json={"config": {"risk_percent": 0.5, "dry_run": True}}, headers=owner["h"])
    assert r.status_code == 200 and r.json()["config"]["risk_percent"] == 0.5 and r.json()["config_version"] == 2
    hist = client.get(f"{url}/history", headers=owner["h"]).json()
    assert [h["config_version"] for h in hist] == [2, 1]
    r = client.post(f"{url}/rollback", json={"config_version": 1}, headers=owner["h"])
    assert r.status_code == 200 and r.json()["config"]["risk_percent"] == 1.0 and r.json()["config_version"] == 3


def test_start_requires_credentials_then_goes_pending(client, owner):
    act = f"/api/v1/instances/{owner['iid']}/actions"
    r = client.post(act, json={"op": "start"}, headers=owner["h"])
    assert r.status_code == 409 and r.json()["code"] == "MT5_AUTH"
    r = client.put(f"/api/v1/accounts/{owner['acct']['id']}/credentials", json={"password": "demo-pass"}, headers=owner["h"])
    assert r.status_code == 200
    # the password is never returned anywhere
    for path in ("/api/v1/accounts", f"/api/v1/instances/{owner['iid']}"):
        assert "demo-pass" not in client.get(path, headers=owner["h"]).text
    r = client.post(act, json={"op": "start"}, headers=owner["h"])
    assert r.status_code == 200 and r.json()["desired_state"] == "running" and r.json()["state"] == "pending"
    assert client.post(act, json={"op": "close_all"}, headers=owner["h"]).status_code == 422  # needs typed confirmation


# ---------------------------------------------------------------- worker leases & intents
def test_worker_auth(client):
    assert client.post("/worker/v1/sync", json={"version": "x", "free_slots": 1}).status_code == 401
    assert client.post("/worker/v1/sync", json={"version": "x", "free_slots": 1}, headers={"Authorization": "Bearer nfw_bad"}).status_code == 401


def test_claim_lease_fencing_and_intents(client, owner, admin):
    w1, _ = _worker(client, admin["h"])
    w2, _ = _worker(client, admin["h"])
    # a worker that does not run this bot version gets nothing
    assert _sync(client, w2, bots={"rehman_liquidity": ["0.9.0"]})["instances"] == []
    s = _sync(client, w1)
    spec = next(x for x in s["instances"] if x["instance_id"] == owner["iid"])
    assert spec["fresh"] and spec["account"]["password"] == "demo-pass" and spec["lease_epoch"] == 1 and spec["run_id"]
    assert spec["config"]["symbol"] == "GOLD" and spec["trading"]["live_allowed"] is False
    # second worker cannot claim the same instance while the lease is valid
    assert all(x["instance_id"] != owner["iid"] for x in _sync(client, w2)["instances"])
    rep = {"id": owner["iid"], "lease_epoch": 1, "run_id": spec["run_id"], "state": "running",
           "heartbeat": {"balance": 10000, "equity": 10010}, "connection": {"status": "ok", "trade_mode": "demo", "login": "7000001"}}
    s = _sync(client, w1, [rep])
    renewal = next(x for x in s["instances"] if x["instance_id"] == owner["iid"])
    assert renewal["fresh"] is False and "password" not in renewal["account"]
    i = client.get(f"/api/v1/instances/{owner['iid']}", headers=owner["h"]).json()
    assert i["state"] == "running" and i["account"]["connection_status"] == "ok"
    # a stale worker reporting the instance is told to stop
    assert any(x["instance_id"] == owner["iid"] and x["reason"] == "lease_lost" for x in _sync(client, w2, [rep])["stop"])

    url = f"/worker/v1/instances/{owner['iid']}/intents"
    base = {"lease_epoch": 1, "run_id": spec["run_id"], "symbol": "GOLD", "side": "BUY", "volume": 0.05, "price": 2400.2, "sl": 2390.0, "tp": 2420.0}
    # fencing: wrong epoch and wrong worker are refused
    assert client.post(url, json={**base, "client_order_id": "aaaaaaaa0001", "lease_epoch": 0}, headers=w1).json()["code"] == "LEASE_LOST"
    assert client.post(url, json={**base, "client_order_id": "aaaaaaaa0002"}, headers=w2).json()["code"] == "LEASE_LOST"
    # risk limits
    assert client.post(url, json={**base, "client_order_id": "aaaaaaaa0003", "volume": 0.5}, headers=w1).json()["code"] == "RISK_LIMIT"
    assert client.post(url, json={**base, "client_order_id": "aaaaaaaa0004", "sl": None}, headers=w1).json()["code"] == "RISK_LIMIT"
    assert client.post(url, json={**base, "client_order_id": "aaaaaaaa0005", "symbol": "EURUSD"}, headers=w1).json()["code"] == "RISK_LIMIT"
    # approved, and idempotent on retry
    r1 = client.post(url, json={**base, "client_order_id": "bbbbbbbb0001"}, headers=w1)
    r2 = client.post(url, json={**base, "client_order_id": "bbbbbbbb0001"}, headers=w1)
    assert r1.status_code == 200 and r1.json()["approved"] and r2.json()["client_order_id"] == "bbbbbbbb0001"
    # while the first is unresolved, no second order
    assert client.post(url, json={**base, "client_order_id": "bbbbbbbb0002"}, headers=w1).json()["code"] == "ORDER_UNCERTAIN"
    res = f"{url}/bbbbbbbb0001/result"
    assert client.post(res, json={"lease_epoch": 1, "status": "uncertain", "error": "timeout"}, headers=w1).status_code == 200
    assert client.post(url, json={**base, "client_order_id": "bbbbbbbb0003"}, headers=w1).json()["code"] == "ORDER_UNCERTAIN"
    assert client.post(res, json={"lease_epoch": 1, "status": "filled"}, headers=w1).status_code == 409  # illegal transition
    assert client.post(res, json={"lease_epoch": 1, "status": "reconciled_filled", "broker_order": "555"}, headers=w1).status_code == 200
    r = client.post(url, json={**base, "client_order_id": "bbbbbbbb0004"}, headers=w1)
    assert r.status_code == 200
    assert client.post(f"{url}/bbbbbbbb0004/result", json={"lease_epoch": 1, "status": "simulated"}, headers=w1).status_code == 409  # not dry run
    assert client.post(f"{url}/bbbbbbbb0004/result", json={"lease_epoch": 1, "status": "filled", "broker_order": "556", "fill_price": 2400.3},
                       headers=w1).status_code == 200
    # trades + events through the worker channel
    ev = {"lease_epoch": 1, "run_id": spec["run_id"], "events": [{"level": "info", "kind": "signal", "message": "SETUP H1 sweep"}],
          "trades": [{"type": "open", "ticket": "556", "side": "BUY", "volume": 0.05, "price": 2400.3, "time": int(datetime.now(timezone.utc).timestamp()),
                      "sl": 2390, "tp": 2420, "client_order_id": "bbbbbbbb0004"}],
          "runtime": {"python": "3.14", "metatrader5": "5.0.5735"}}
    assert client.post(f"/worker/v1/instances/{owner['iid']}/events", json=ev, headers=w1).status_code == 200
    orders = client.get(f"/api/v1/instances/{owner['iid']}/orders", headers=owner["h"]).json()
    assert orders["counts"].get("filled") == 1 and orders["counts"].get("reconciled_filled") == 1
    runs = client.get(f"/api/v1/instances/{owner['iid']}/runs", headers=owner["h"]).json()
    assert runs[0]["runtime"]["metatrader5"] == "5.0.5735" and runs[0]["bot_version"] == "1.0.0"
    owner.update(w1=w1, epoch=1, run_id=spec["run_id"])


def test_platform_halt_blocks_new_orders(client, owner, admin):
    assert client.post("/api/v1/admin/platform/halt", json={"halted": True, "reason": "incident drill"}, headers=admin["h"]).status_code == 200
    url = f"/worker/v1/instances/{owner['iid']}/intents"
    body = {"lease_epoch": owner["epoch"], "symbol": "GOLD", "side": "SELL", "volume": 0.02, "sl": 2410.0, "client_order_id": "cccccccc0001"}
    assert client.post(url, json=body, headers=owner["w1"]).json()["code"] == "TRADING_HALTED"
    assert client.post(f"/api/v1/instances/{owner['iid']}/actions", json={"op": "start"}, headers=owner["h"]).json()["code"] == "TRADING_HALTED"
    client.post("/api/v1/admin/platform/halt", json={"halted": False, "reason": "drill over"}, headers=admin["h"])


def test_pause_close_all_commands_and_stop(client, owner):
    act = f"/api/v1/instances/{owner['iid']}/actions"
    assert client.post(act, json={"op": "pause"}, headers=owner["h"]).json()["desired_state"] == "paused"
    url = f"/worker/v1/instances/{owner['iid']}/intents"
    body = {"lease_epoch": owner["epoch"], "symbol": "GOLD", "side": "SELL", "volume": 0.02, "sl": 2410.0, "client_order_id": "dddddddd0001"}
    assert client.post(url, json=body, headers=owner["w1"]).status_code == 409  # paused: no new entries
    cid = client.post(act, json={"op": "close_all", "confirm": "CLOSE"}, headers=owner["h"]).json()["command_id"]
    rep = {"id": owner["iid"], "lease_epoch": owner["epoch"], "run_id": owner["run_id"], "state": "paused"}
    spec = next(x for x in _sync(client, owner["w1"], [rep])["instances"] if x["instance_id"] == owner["iid"])
    assert {"id": cid, "type": "close_all"} in spec["commands"] and spec["desired_state"] == "paused"
    _sync(client, owner["w1"], [{**rep, "acked_commands": [cid]}])
    assert client.post(act, json={"op": "stop"}, headers=owner["h"]).json()["desired_state"] == "stopped"
    s = _sync(client, owner["w1"], [rep])
    assert any(x["instance_id"] == owner["iid"] and x["reason"] == "stop_requested" for x in s["stop"])
    _sync(client, owner["w1"], [{**rep, "state": "stopped"}])
    i = client.get(f"/api/v1/instances/{owner['iid']}", headers=owner["h"]).json()
    assert i["state"] == "stopped" and i["worker_id"] is None
    # stopping does not close positions: the open position recorded earlier is still open
    assert i["open_positions"] == 1
    ev = client.get(f"/api/v1/instances/{owner['iid']}/events?limit=200", headers=owner["h"]).json()["items"]
    assert any("Stop requested" in e["message"] for e in ev) and any("Order refused" in e["message"] for e in ev)


def test_lease_expiry_reclaim_bumps_epoch_and_old_worker_is_fenced(client, admin):
    from app.db import SessionLocal
    from app.models import BotInstance
    from app.scheduler import instance_watchdog
    o = signup(client, f"lease-{uuid.uuid4().hex[:6]}@example.com")
    acct = _bind(client, o["h"], "7000101")
    client.put(f"/api/v1/accounts/{acct['id']}/credentials", json={"password": "pw-demo"}, headers=o["h"])
    iid = acct["instance"]["id"]
    client.post(f"/api/v1/instances/{iid}/actions", json={"op": "start"}, headers=o["h"])
    wa, _ = _worker(client, admin["h"])
    wb, _ = _worker(client, admin["h"])
    a = next(x for x in _sync(client, wa)["instances"] if x["instance_id"] == iid)
    with SessionLocal() as db:  # worker A goes silent: its lease expires
        inst = db.get(BotInstance, iid)
        inst.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        db.commit()
        instance_watchdog(db)
        assert db.get(BotInstance, iid).state == "offline"
    b = next(x for x in _sync(client, wb)["instances"] if x["instance_id"] == iid)
    assert b["fresh"] and b["lease_epoch"] == a["lease_epoch"] + 1 and b["run_id"] != a["run_id"]
    body = {"lease_epoch": a["lease_epoch"], "symbol": "GOLD", "side": "BUY", "volume": 0.02, "sl": 2390.0, "client_order_id": "eeeeeeee0001"}
    assert client.post(f"/worker/v1/instances/{iid}/intents", json=body, headers=wa).json()["code"] == "LEASE_LOST"
    s = _sync(client, wa, [{"id": iid, "lease_epoch": a["lease_epoch"], "state": "running"}])
    assert any(x["instance_id"] == iid for x in s["stop"])


def test_stale_approved_intent_becomes_uncertain_and_blocks(client, admin):
    from app.db import SessionLocal
    from app.models import OrderIntent
    from app.services.trading_guard import expire_stale_intents
    o = signup(client, f"stale-{uuid.uuid4().hex[:6]}@example.com")
    acct = _bind(client, o["h"], "7000102")
    client.put(f"/api/v1/accounts/{acct['id']}/credentials", json={"password": "pw-demo"}, headers=o["h"])
    iid = acct["instance"]["id"]
    client.post(f"/api/v1/instances/{iid}/actions", json={"op": "start"}, headers=o["h"])
    w, _ = _worker(client, admin["h"])
    spec = next(x for x in _sync(client, w)["instances"] if x["instance_id"] == iid)
    _sync(client, w, [{"id": iid, "lease_epoch": spec["lease_epoch"], "run_id": spec["run_id"], "state": "running"}])
    url = f"/worker/v1/instances/{iid}/intents"
    base = {"lease_epoch": spec["lease_epoch"], "symbol": "GOLD", "side": "BUY", "volume": 0.02, "sl": 2390.0}
    assert client.post(url, json={**base, "client_order_id": "ffffffff0001"}, headers=w).status_code == 200
    with SessionLocal() as db:  # worker died between approval and result
        it = db.query(OrderIntent).filter_by(client_order_id="ffffffff0001").one()
        it.created_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        db.commit()
        assert expire_stale_intents(db) == 1
    assert client.post(url, json={**base, "client_order_id": "ffffffff0002"}, headers=w).json()["code"] == "ORDER_UNCERTAIN"
    s = _sync(client, w, [{"id": iid, "lease_epoch": spec["lease_epoch"], "state": "running"}])
    renewal = next(x for x in s["instances"] if x["instance_id"] == iid)
    assert renewal["unresolved_orders"][0]["client_order_id"] == "ffffffff0001"


def test_live_account_needs_platform_gate_and_owner_authorization(client):
    o = signup(client, f"live-{uuid.uuid4().hex[:6]}@example.com")
    # pay so live accounts may be bound (trial = demo only)
    post_webhook(client, {"id": f"evt_{uuid.uuid4().hex}", "type": "invoice.paid",
                          "data": {"tenant_id": o["user"]["tenant_id"], "provider_invoice_id": f"in_{uuid.uuid4().hex[:8]}", "amount_cents": 9900}})
    acct = _bind(client, o["h"], "7000103", account_type="live", server="Fake-Live")
    iid = acct["instance"]["id"]
    client.put(f"/api/v1/accounts/{acct['id']}/credentials", json={"password": "pw-demo"}, headers=o["h"])
    r = client.post(f"/api/v1/instances/{iid}/actions", json={"op": "start"}, headers=o["h"])
    assert r.status_code == 403  # ALLOW_LIVE_TRADING is false (default)
    r = client.post(f"/api/v1/instances/{iid}/live-authorization", json={"confirm": "I ACCEPT LIVE TRADING RISK"}, headers=o["h"])
    assert r.status_code == 403
    # dry run is allowed on a live account: it never sends orders
    client.put(f"/api/v1/instances/{iid}/config", json={"config": {"dry_run": True}}, headers=o["h"])
    assert client.post(f"/api/v1/instances/{iid}/actions", json={"op": "start"}, headers=o["h"]).status_code == 200


def test_ea_refused_on_account_assigned_to_python_bot(client, owner):
    key = client.get("/api/v1/billing/subscription", headers=owner["h"]).json()["license"]["key"]
    r = client.post("/ea/v1/handshake", json={"license_key": key, "login": "7000001", "server": "Fake-Demo"})
    assert r.status_code == 409 and "other_bot" in r.json()["detail"]


# ---------------------------------------------------------------- isolation
def test_cross_tenant_isolation(client, owner):
    other = signup(client, f"other-{uuid.uuid4().hex[:6]}@example.com")
    iid = owner["iid"]
    for method, path, body in [("get", f"/api/v1/instances/{iid}", None), ("get", f"/api/v1/instances/{iid}/events", None),
                               ("get", f"/api/v1/instances/{iid}/orders", None), ("put", f"/api/v1/instances/{iid}/config", {"config": {}}),
                               ("post", f"/api/v1/instances/{iid}/actions", {"op": "start"}), ("post", f"/api/v1/instances/{iid}/diagnose", None),
                               ("put", f"/api/v1/accounts/{owner['acct']['id']}/credentials", {"password": "x" * 8}),
                               ("post", f"/api/v1/accounts/{owner['acct']['id']}/verify", None)]:
        r = getattr(client, method)(path, headers=other["h"], **({"json": body} if body is not None else {}))
        assert r.status_code == 404, (path, r.status_code, r.text)
    assert client.get("/api/v1/instances", headers=other["h"]).json() == []
    assert client.post("/api/v1/instances", json={"account_id": owner["acct"]["id"], "bot": "rehman_liquidity"}, headers=other["h"]).status_code == 404


# ---------------------------------------------------------------- admin
def test_admin_rbac(client, owner, admin):
    for path in ("/api/v1/admin/overview", "/api/v1/admin/users", "/api/v1/admin/bots", "/api/v1/admin/audit"):
        assert client.get(path, headers=owner["h"]).status_code == 403
    support = make_admin(client, "support@example.com", role="support")
    assert client.get("/api/v1/admin/overview", headers=support["h"]).status_code == 200
    r = client.post("/api/v1/admin/platform/halt", json={"halted": True, "reason": "x" * 5}, headers=support["h"])
    assert r.status_code == 403 and "read-only" in r.json()["detail"]
    o = client.get("/api/v1/admin/overview", headers=admin["h"]).json()
    assert "instances" in o and "alerts" in o and o["live_trading_enabled"] is False


def test_admin_bot_versions_rollout_and_disable(client, admin, owner):
    bots = {b["code"]: b for b in client.get("/api/v1/admin/bots", headers=admin["h"]).json()}
    rb = bots["rehman_liquidity"]
    v1 = rb["active_version_id"]
    r = client.post(f"/api/v1/admin/bots/{rb['id']}/versions", json={"version": "1.0.1", "notes": "tighter spread",
                                                                      "default_config": {"spread_max": 40}}, headers=admin["h"])
    assert r.status_code == 200
    v2 = next(v for v in r.json()["versions"] if v["version"] == "1.0.1")
    assert v2["status"] == "draft" and v2["default_config"]["spread_max"] == 40
    # cannot approve a python bot version without a passing self-test; cannot activate a draft
    assert client.post(f"/api/v1/admin/bots/{rb['id']}/versions/{v2['id']}/status", json={"status": "approved", "reason": "ship"},
                       headers=admin["h"]).status_code == 409
    assert client.post(f"/api/v1/admin/bots/{rb['id']}/active-version", json={"version_id": v2["id"], "reason": "ship"},
                       headers=admin["h"]).status_code == 409
    job = client.post(f"/api/v1/admin/bots/{rb['id']}/versions/{v2['id']}/self-test", json={"iterations": 5}, headers=admin["h"]).json()
    from app.db import SessionLocal
    from app.models import WorkerJob
    with SessionLocal() as db:  # what a worker reports after running the self-test
        j = db.get(WorkerJob, job["job_id"])
        j.status, j.result = "passed", {"ok": True, "simulated": True}
        db.commit()
    assert client.post(f"/api/v1/admin/bots/{rb['id']}/versions/{v2['id']}/status", json={"status": "approved", "reason": "self-test ok"},
                       headers=admin["h"]).status_code == 200
    assert client.post(f"/api/v1/admin/bots/{rb['id']}/active-version", json={"version_id": v2["id"], "reason": "rollout"},
                       headers=admin["h"]).json()["active_version_id"] == v2["id"]
    # rollback to the previous approved version; existing instance stays pinned until moved explicitly
    assert client.post(f"/api/v1/admin/bots/{rb['id']}/active-version", json={"version_id": v1, "reason": "rollback"},
                       headers=admin["h"]).json()["active_version_id"] == v1
    assert client.get(f"/api/v1/instances/{owner['iid']}", headers=owner["h"]).json()["version"] == "1.0.0"
    r = client.post(f"/api/v1/admin/instances/{owner['iid']}/version", json={"version_id": v2["id"], "reason": "try 1.0.1"}, headers=admin["h"])
    assert r.status_code == 200 and r.json()["version"] == "1.0.1"
    # disabling a bot blocks new assignments, not existing instances
    client.patch(f"/api/v1/admin/bots/{rb['id']}", json={"enabled": False, "reason": "maintenance"}, headers=admin["h"])
    other = signup(client, f"dis-{uuid.uuid4().hex[:6]}@example.com")
    r = client.post("/api/v1/accounts", json={"login": "7000199", "server": "Fake-Demo", "bot": "rehman_liquidity"}, headers=other["h"])
    assert r.status_code == 409
    assert client.get(f"/api/v1/instances/{owner['iid']}", headers=owner["h"]).status_code == 200
    client.patch(f"/api/v1/admin/bots/{rb['id']}", json={"enabled": True, "reason": "back"}, headers=admin["h"])
    audit = client.get("/api/v1/admin/audit?action=admin.bot", headers=admin["h"]).json()["items"]
    assert {"admin.bot_version_create", "admin.bot_active_version", "admin.bot_update"} <= {a["action"] for a in audit}


def test_admin_user_suspension_revokes_sessions(client, admin):
    u = signup(client, f"susp-{uuid.uuid4().hex[:6]}@example.com")
    me = client.get("/api/v1/auth/me", headers=u["h"]).json()
    assert client.post(f"/api/v1/admin/users/{me['id']}/status", json={"status": "suspended", "reason": "fraud check"}, headers=admin["h"]).status_code == 200
    assert client.get("/api/v1/auth/me", headers=u["h"]).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": u["email"], "password": u["password"]}).status_code == 403
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": u["refresh"]}).status_code == 401
    client.post(f"/api/v1/admin/users/{me['id']}/status", json={"status": "active", "reason": "cleared"}, headers=admin["h"])
    assert client.post("/api/v1/auth/login", json={"email": u["email"], "password": u["password"]}).status_code == 200
    users = client.get(f"/api/v1/admin/users?q={u['email'][:8]}", headers=admin["h"]).json()
    assert users["total"] >= 1


def test_admin_manual_grant_and_trial_policy(client, admin):
    u = signup(client, f"grant-{uuid.uuid4().hex[:6]}@example.com", plan=None)
    tid = u["user"]["tenant_id"]
    assert client.get("/api/v1/billing/subscription", headers=u["h"]).json()["entitlement"]["valid"] is False
    r = client.post(f"/api/v1/admin/tenants/{tid}/subscription", json={"plan": "elite", "days": 30, "note": "partner account"}, headers=admin["h"])
    assert r.status_code == 200 and r.json()["status"] == "active"
    ent = client.get("/api/v1/billing/subscription", headers=u["h"]).json()["entitlement"]
    assert ent["valid"] and ent["plan"] == "elite"
    assert client.put("/api/v1/admin/platform/trial-policy", json={"enabled": True, "days": 14}, headers=admin["h"]).status_code == 200
    v = signup(client, f"trial14-{uuid.uuid4().hex[:6]}@example.com", plan=None)
    assert client.get("/api/v1/billing/config", headers=v["h"]).json() == {"provider": "mock", "trial_days": 14, "trial_eligible": True}
    client.put("/api/v1/admin/platform/trial-policy", json={"enabled": True, "days": 7}, headers=admin["h"])


def test_admin_worker_registry_and_token_shown_once(client, admin):
    r = client.post("/api/v1/admin/workers", json={"name": "win-vps-01"}, headers=admin["h"])
    assert r.status_code == 200 and r.json()["token"].startswith("nfw_")
    listing = client.get("/api/v1/admin/workers", headers=admin["h"]).text
    assert r.json()["token"] not in listing
    wid = r.json()["id"]
    client.post(f"/api/v1/admin/workers/{wid}/status", json={"status": "disabled"}, headers=admin["h"])
    assert client.post("/worker/v1/sync", json={"version": "1", "free_slots": 1}, headers={"Authorization": f"Bearer {r.json()['token']}"}).status_code == 401


# ---------------------------------------------------------------- trial & entitlement
def test_trial_expiry_blocks_start_and_paid_reactivates(client):
    from app.db import SessionLocal
    from app.models import Subscription
    from app.scheduler import trials_and_lapse
    u = signup(client, f"trial-{uuid.uuid4().hex[:6]}@example.com")
    acct = _bind(client, u["h"], "7000104")
    client.put(f"/api/v1/accounts/{acct['id']}/credentials", json={"password": "pw-demo"}, headers=u["h"])
    tid = u["user"]["tenant_id"]
    with SessionLocal() as db:
        sub = db.query(Subscription).filter_by(tenant_id=tid).one()
        sub.trial_end = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    r = client.post(f"/api/v1/instances/{acct['instance']['id']}/actions", json={"op": "start"}, headers=u["h"])
    assert r.status_code == 402 and r.json()["code"] == "ENTITLEMENT" and "trial has ended" in r.json()["detail"]
    with SessionLocal() as db:
        trials_and_lapse(db)
        assert db.query(Subscription).filter_by(tenant_id=tid).one().status == "expired"
    # one trial per workspace: checking out again is a paid subscription, not a second trial
    r = client.post("/api/v1/billing/checkout", json={"plan": "pro"}, headers=u["h"])
    assert r.json()["trial"] is False
    assert client.get("/api/v1/billing/subscription", headers=u["h"]).json()["entitlement"]["valid"] is True
    assert client.post(f"/api/v1/instances/{acct['instance']['id']}/actions", json={"op": "start"}, headers=u["h"]).status_code == 200


def test_frontend_cannot_grant_paid_access(client):
    u = signup(client, f"forge-{uuid.uuid4().hex[:6]}@example.com", plan=None)
    forged = {"id": "evt_forged", "type": "subscription.upserted",
              "data": {"tenant_id": u["user"]["tenant_id"], "plan_code": "elite", "status": "active"}}
    assert client.post("/api/v1/billing/webhook", json=forged).status_code == 400
    assert client.post("/api/v1/billing/webhook", json=forged, headers={"X-Mock-Signature": "0" * 64}).status_code == 400
    assert client.get("/api/v1/billing/subscription", headers=u["h"]).json()["subscription"] is None


# ---------------------------------------------------------------- auth hardening
def test_refresh_rotation_and_reuse_detection(client):
    u = signup(client, f"rot-{uuid.uuid4().hex[:6]}@example.com", plan=None)
    r1 = client.post("/api/v1/auth/refresh", json={"refresh_token": u["refresh"]})
    assert r1.status_code == 200
    # re-using the rotated token is treated as theft: every session is revoked
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": u["refresh"]}).status_code == 401
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": r1.json()["refresh_token"]}).status_code == 401
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {r1.json()['access_token']}"}).status_code == 401


def test_logout_and_logout_all(client):
    u = signup(client, f"lo-{uuid.uuid4().hex[:6]}@example.com", plan=None)
    assert client.post("/api/v1/auth/logout", json={"refresh_token": u["refresh"]}).status_code == 200
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": u["refresh"]}).status_code == 401
    r = client.post("/api/v1/auth/login", json={"email": u["email"], "password": u["password"]}).json()
    h = {"Authorization": f"Bearer {r['access_token']}"}
    assert client.post("/api/v1/auth/logout-all", headers=h).status_code == 200
    assert client.get("/api/v1/auth/me", headers=h).status_code == 401


def test_password_reset_flow(client):
    u = signup(client, f"pw-{uuid.uuid4().hex[:6]}@example.com", plan=None)
    same = client.post("/api/v1/auth/password/forgot", json={"email": "nobody@example.com"}).json()
    assert same == client.post("/api/v1/auth/password/forgot", json={"email": u["email"]}).json()  # no account enumeration
    assert client.post("/api/v1/auth/password/reset", json={"email": u["email"], "code": "000000", "password": "brand-new-password-9"}).status_code == 400
    assert client.post("/api/v1/auth/password/reset", json={"email": u["email"], "code": "123456", "password": "brand-new-password-9"}).status_code == 200
    assert client.get("/api/v1/auth/me", headers=u["h"]).status_code == 401  # old sessions revoked
    assert client.post("/api/v1/auth/login", json={"email": u["email"], "password": u["password"]}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": u["email"], "password": "brand-new-password-9"}).status_code == 200
    assert client.post("/api/v1/auth/password/reset", json={"email": u["email"], "code": "123456", "password": "another-password-99"}).status_code == 400


def test_profile_change_password_and_ws_ticket(client):
    u = signup(client, f"prof-{uuid.uuid4().hex[:6]}@example.com", plan=None)
    assert client.put("/api/v1/auth/profile", json={"name": "New Name"}, headers=u["h"]).json()["name"] == "New Name"
    assert client.post("/api/v1/auth/password/change", json={"current_password": "wrong-one-xxxx", "new_password": "z" * 14}, headers=u["h"]).status_code == 400
    r = client.post("/api/v1/auth/password/change", json={"current_password": u["password"], "new_password": "z" * 14}, headers=u["h"])
    assert r.status_code == 200
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    t = client.post("/api/v1/auth/ws-ticket", headers=h).json()["ticket"]
    with client.websocket_connect(f"/ws?ticket={t}") as ws:
        assert ws.receive_json()["event"] == "hello"


def test_rate_limit():
    from app import ratelimit
    from app.errors import AppError
    ratelimit.reset()
    for _ in range(3):
        ratelimit.check("auth:1.2.3.4", 3)
    with pytest.raises(AppError) as e:
        ratelimit.check("auth:1.2.3.4", 3)
    assert e.value.status_code == 429


def test_error_responses_have_code_and_correlation_id(client):
    r = client.get("/api/v1/auth/me", headers={"X-Request-ID": "trace-123"})
    assert r.status_code == 401 and r.json()["code"] == "AUTH_FAILED" and r.json()["correlation_id"] == "trace-123"
    assert r.headers["x-request-id"] == "trace-123" and r.headers["x-content-type-options"] == "nosniff"


# ---------------------------------------------------------------- AI agents
def test_diagnostics_agent_is_labelled_and_scoped(client, owner):
    r = client.post(f"/api/v1/instances/{owner['iid']}/diagnose", headers=owner["h"])
    assert r.status_code == 200
    out = r.json()["output"]
    assert r.json()["status"] == "succeeded" and out["generated"] is True and "disclaimer" in out and out["summary"]


def test_agent_forbidden_scopes_rejected():
    from app.services.agents import validate_manifest
    assert validate_manifest({"scopes": ["bot_events.read"]}) == []
    assert validate_manifest({"scopes": ["trade.execute"]})
    assert validate_manifest({"scopes": ["secrets.read", "bot_events.read"]})


# ---------------------------------------------------------------- health & config
def test_health_endpoints(client):
    assert client.get("/health").json()["ok"] is True
    r = client.get("/health/ready").json()
    assert r["ok"] and r["migration"] == r["expected"]


def test_production_config_validation():
    from app.config import Settings
    bad = Settings(env="prod", database_url="sqlite:///x.db", billing_provider="mock", dev_show_email_codes=True)
    problems = " ".join(bad.validate_for_production())
    for needle in ("APP_SECRET", "EA_SIGNING_SECRET", "MT5_CREDENTIALS_KEY", "PostgreSQL", "mock", "DEV_SHOW_EMAIL_CODES"):
        assert needle in problems
    from cryptography.fernet import Fernet
    ok = dict(database_url="postgresql+psycopg://u:p@h/d", billing_provider="manual", dev_show_email_codes=False,
              app_secret="a" * 40, ea_signing_secret="b" * 40, mt5_credentials_key=Fernet.generate_key().decode(),
              cors_origins="https://app.example.com")
    assert Settings(env="prod", **ok).validate_for_production() == []
    # unknown / differently-cased ENV values fail closed into production checks
    for env in ("PROD", " Production ", "live"):
        assert Settings(env=env, database_url="sqlite:///x.db").validate_for_production()
    assert Settings(env="TEST", database_url="sqlite:///x.db").validate_for_production() == []
    assert "valid Fernet" in " ".join(Settings(env="prod", **{**ok, "mt5_credentials_key": "k" * 44}).validate_for_production())
    assert "must differ" in " ".join(Settings(env="prod", **{**ok, "ea_signing_secret": "a" * 40}).validate_for_production())
