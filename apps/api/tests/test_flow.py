"""End-to-end API flow: signup -> trial -> bind -> EA handshake/heartbeat/trades -> config -> commands -> billing states."""
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone


import pyotp
import pytest

from app.config import get_settings
from app.services.risk import lot_size
from conftest import post_webhook

SECRET = get_settings().ea_signing_secret


def verified(resp):
    assert resp.status_code == 200, resp.text
    j = resp.json()
    sig = hmac.new(SECRET.encode(), j["payload"].encode(), hashlib.sha256).hexdigest()
    assert hmac.compare_digest(sig, j["sig"]), "bad signature"
    body = json.loads(j["payload"])
    assert body["exp"] > time.time()
    return body


@pytest.fixture(scope="module")
def ctx(client):
    r = client.post("/api/v1/auth/signup", json={"name": "Umair Noor", "email": "umair@example.com",
                                                 "password": "a-very-long-password-1!", "accept_risk": True})
    assert r.status_code == 200, r.text
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    return {"h": h}


def test_lot_size_matches_formula():
    # 10,000 equity, 1% risk, SL 6.00 away, 100 oz contract -> 0.16 lots (rounded down)
    assert lot_size(10000, 1.0, 6.0) == 0.16
    assert lot_size(500, 0.1, 6.0) == 0.0  # below min lot -> no trade
    assert lot_size(1_000_000, 3.0, 1.0, max_lot=5.0) == 5.0


def test_signup_validation(client):
    r = client.post("/api/v1/auth/signup", json={"name": "X Y", "email": "x@example.com", "password": "short", "accept_risk": True})
    assert r.status_code == 422
    r = client.post("/api/v1/auth/signup", json={"name": "X Y", "email": "x@example.com", "password": "long-enough-password", "accept_risk": False})
    assert r.status_code == 422


def test_verify_and_trial(client, ctx):
    h = ctx["h"]
    assert client.post("/api/v1/auth/verify-email", json={"code": "000000"}, headers=h).status_code == 400
    assert client.post("/api/v1/auth/verify-email", json={"code": "123456"}, headers=h).status_code == 200
    r = client.post("/api/v1/billing/checkout", json={"plan": "pro"}, headers=h)
    assert r.status_code == 200 and r.json()["mock"]
    s = client.get("/api/v1/billing/subscription", headers=h).json()
    assert s["subscription"]["status"] == "trialing" and s["license"]["valid"] and s["license"]["trial"]
    ctx["key"] = s["license"]["key"]
    assert ctx["key"].startswith("AUSC-")


def test_bind_rules(client, ctx):
    h = ctx["h"]
    r = client.post("/api/v1/accounts", json={"login": "5102948", "server": "ICMarketsSC-Live", "account_type": "live"}, headers=h)
    assert r.status_code == 403 and "trial" in r.json()["detail"]
    r = client.post("/api/v1/accounts", json={"login": "5102948", "server": "ICMarketsSC-Demo", "account_type": "demo"}, headers=h)
    assert r.status_code == 200, r.text
    ctx["acct"] = r.json()["id"]
    r = client.post("/api/v1/accounts", json={"login": "5102948", "server": "ICMarketsSC-Demo"}, headers=h)
    assert r.status_code in (403, 409)
    r = client.post("/api/v1/accounts", json={"login": "7730215", "server": "X-Demo", "mode": "hosted"}, headers=h)
    assert r.status_code == 403  # hosted needs Elite


def ea(ctx, **extra):
    return {"license_key": ctx["key"], "login": "5102948", "server": "ICMarketsSC-Demo", "ea_version": "2.4.1", "account_type": "demo", **extra}


def test_ea_auth(client, ctx):
    assert client.post("/ea/v1/handshake", json={**ea(ctx), "license_key": "AUSC-AAAA-BBBB-CCCC-DDDD"}).status_code == 401
    assert client.post("/ea/v1/handshake", json={**ea(ctx), "login": "99999"}).status_code == 403
    b = verified(client.post("/ea/v1/handshake", json=ea(ctx)))
    assert b["license"]["valid"] and b["config"]["preset"] == "balanced" and b["config"]["risk_pct"] == 1.0
    ctx["cfgv"] = b["config"]["config_version"]


def test_heartbeat_and_trades(client, ctx):
    h = ctx["h"]
    hb = ea(ctx, balance=10000, equity=10012.5, bid=3412.1, ask=3412.3, spread=20, ping_ms=4, open_positions=1,
            applied_config_version=ctx["cfgv"])
    verified(client.post("/ea/v1/heartbeat", json=hb))
    now = int(time.time())
    ev_open = {"type": "open", "ticket": "1001", "side": "BUY", "volume": 0.16, "price": 3412.30, "time": now - 90,
               "requested_price": 3412.28, "sl": 3410.7, "tp": 3415.5, "slippage_pts": 2, "spread": 20, "module": "momentum"}
    verified(client.post("/ea/v1/trades", json=ea(ctx, events=[ev_open])))
    verified(client.post("/ea/v1/trades", json=ea(ctx, events=[ev_open])))  # idempotent resend
    d = client.get("/api/v1/dashboard", headers=h).json()
    assert d["account"]["online"] and d["account"]["status"] == "running" and len(d["open_positions"]) == 1
    ev_close = {"type": "close", "ticket": "1001", "side": "BUY", "volume": 0.16, "price": 3415.5, "time": now,
                "profit": 51.2, "commission": -0.56, "reason": "TP"}
    loss_open = {**ev_open, "ticket": "1002", "side": "SELL", "time": now - 60}
    loss_close = {**ev_close, "ticket": "1002", "side": "SELL", "profit": -25.6, "reason": "SL"}
    verified(client.post("/ea/v1/trades", json=ea(ctx, events=[ev_close, loss_open, loss_close])))
    t = client.get("/api/v1/trades?range=today", headers=h).json()
    assert t["count"] == 2 and round(t["net"], 2) == round(51.2 - 0.56 - 25.6 - 0.56, 2)
    p = client.get("/api/v1/performance?range=30d", headers=h).json()
    assert p["trades"] == 2 and p["win_rate"] == 50.0 and p["profit_factor"] == round(50.64 / 26.16, 2)
    assert client.get("/api/v1/trades.csv", headers=h).text.count("\n") == 3
    c = client.get("/api/v1/market/candles", headers=h).json()
    assert c and c[-1]["close"] == 3412.2


def test_config_versioning(client, ctx):
    h, a = ctx["h"], ctx["acct"]
    assert client.put(f"/api/v1/accounts/{a}/config", json={"overrides": {"risk_pct": 9}}, headers=h).status_code == 422
    assert client.put(f"/api/v1/accounts/{a}/config", json={"overrides": {"sl_pts": 9}}, headers=h).status_code == 422
    r = client.put(f"/api/v1/accounts/{a}/config", json={"preset": "prop", "overrides": {"risk_pct": 0.4, "news_filter": True}}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "pending"
    v = r.json()["version"]
    hb = verified(client.post("/ea/v1/heartbeat", json=ea(ctx, balance=10025, equity=10025, applied_config_version=ctx["cfgv"])))
    assert hb["config_version"] == v
    b = verified(client.post("/ea/v1/handshake", json=ea(ctx)))
    assert b["config"]["preset"] == "prop" and b["config"]["risk_pct"] == 0.4
    verified(client.post("/ea/v1/heartbeat", json=ea(ctx, balance=10025, equity=10025, applied_config_version=v)))
    assert client.get(f"/api/v1/accounts/{a}/config", headers=h).json()["status"] == "applied"


def test_commands_and_kill(client, ctx):
    h, a = ctx["h"], ctx["acct"]
    cid = client.post(f"/api/v1/accounts/{a}/commands", json={"type": "pause"}, headers=h).json()["id"]
    hb = verified(client.post("/ea/v1/heartbeat", json=ea(ctx, balance=1, equity=1)))
    assert hb["commands"] == [{"id": cid, "type": "pause"}] and hb["trading_enabled"] is False
    verified(client.post("/ea/v1/heartbeat", json=ea(ctx, balance=1, equity=1, acked_commands=[cid], state="paused")))
    assert client.post("/api/v1/kill-switch", headers=h).json()["accounts"] == 1
    hb = verified(client.post("/ea/v1/heartbeat", json=ea(ctx, balance=1, equity=1)))
    assert hb["commands"][0]["type"] == "kill"
    client.post(f"/api/v1/accounts/{a}/commands", json={"type": "resume"}, headers=h)
    hb = verified(client.post("/ea/v1/heartbeat", json=ea(ctx, balance=1, equity=1)))
    assert hb["trading_enabled"] is True


def test_payment_failure_grace_then_suspend(client, ctx):
    from app.db import SessionLocal
    from app.models import Subscription
    from app.services.billing import enforce_grace
    h = ctx["h"]
    me = client.get("/api/v1/auth/me", headers=h).json()
    evt = {"id": "evt_fail_1", "type": "invoice.payment_failed", "data": {"tenant_id": me["tenant_id"], "provider_invoice_id": "in_1", "amount_cents": 9900}}
    post_webhook(client, {"id": "evt_paid_0", "type": "invoice.paid",
                          "data": {"tenant_id": me["tenant_id"], "provider_invoice_id": "in_0", "amount_cents": 9900}})
    assert post_webhook(client, evt).json()["processed"] is True
    assert post_webhook(client, evt).json()["processed"] is False  # idempotent
    # unsigned / forged webhooks are refused
    assert client.post("/api/v1/billing/webhook", json=evt).status_code == 400
    b = verified(client.post("/ea/v1/handshake", json=ea(ctx)))
    assert b["license"]["valid"] and b["license"]["reason"] == "past_due_grace"
    with SessionLocal() as db:
        sub = db.query(Subscription).filter_by(tenant_id=me["tenant_id"]).order_by(Subscription.id.desc()).first()
        sub.past_due_since = datetime.now(timezone.utc) - timedelta(hours=80)
        db.commit()
        assert enforce_grace(db) == 1
    b = verified(client.post("/ea/v1/handshake", json=ea(ctx)))
    assert b["license"]["valid"] is False
    assert post_webhook(client, {"id": "evt_paid_2", "type": "invoice.paid",
                                 "data": {"tenant_id": me["tenant_id"], "provider_invoice_id": "in_1", "amount_cents": 9900}}).status_code == 200
    b = verified(client.post("/ea/v1/handshake", json=ea(ctx)))
    assert b["license"]["valid"] is True
    inv = client.get("/api/v1/billing/invoices", headers=h).json()
    assert {i["status"] for i in inv} == {"paid"}


def test_two_factor_login(client, ctx):
    h = ctx["h"]
    pw = "a-very-long-password-1!"
    # re-authentication is required: a bare access token cannot enrol 2FA
    assert client.post("/api/v1/auth/2fa/setup", json={"current_password": "wrong-password-xx"}, headers=h).status_code == 400
    secret = client.post("/api/v1/auth/2fa/setup", json={"current_password": pw}, headers=h).json()["secret"]
    assert client.post("/api/v1/auth/2fa/enable", json={"code": "000000", "current_password": pw}, headers=h).status_code == 400
    r = client.post("/api/v1/auth/2fa/enable", json={"code": pyotp.TOTP(secret).now(), "current_password": pw}, headers=h)
    assert r.status_code == 200
    # enabling 2FA signs out older sessions; this device continues with the new one
    assert client.get("/api/v1/auth/me", headers=h).status_code == 401
    h["Authorization"] = f"Bearer {r.json()['access_token']}"
    assert client.get("/api/v1/auth/me", headers=h).json()["totp_enabled"] is True
    r = client.post("/api/v1/auth/login", json={"email": "umair@example.com", "password": "wrong-password-xx"})
    assert r.status_code == 401
    r = client.post("/api/v1/auth/login", json={"email": "umair@example.com", "password": "a-very-long-password-1!"}).json()
    assert r["two_factor_required"]
    r2 = client.post("/api/v1/auth/2fa/verify", json={"challenge": r["challenge"], "code": pyotp.TOTP(secret).now()})
    assert r2.status_code == 200 and "access_token" in r2.json()


def test_tenant_isolation_and_starter_limits(client, ctx):
    r = client.post("/api/v1/auth/signup", json={"name": "Other Co", "email": "other@example.com",
                                                 "password": "another-long-password", "accept_risk": True})
    h2 = {"Authorization": f"Bearer {r.json()['access_token']}"}
    client.post("/api/v1/billing/checkout", json={"plan": "starter"}, headers=h2)
    assert client.get(f"/api/v1/accounts/{ctx['acct']}/config", headers=h2).status_code == 404
    assert client.get("/api/v1/trades", headers=h2).json()["count"] == 0
    a = client.post("/api/v1/accounts", json={"login": "4400112", "server": "Exness-MT5Trial"}, headers=h2).json()["id"]
    assert client.put(f"/api/v1/accounts/{a}/config", json={"overrides": {"risk_pct": 2}}, headers=h2).status_code == 403
    assert client.put(f"/api/v1/accounts/{a}/config", json={"preset": "prop"}, headers=h2).status_code == 403
    assert client.post("/api/v1/accounts", json={"login": "4400113", "server": "Exness-MT5Trial"}, headers=h2).status_code == 403


def test_alerts(client, ctx):
    h = ctx["h"]
    a = client.get("/api/v1/alerts", headers=h).json()
    assert len(a["prefs"]) == 7
    r = client.put("/api/v1/alerts", json={"event": "daily_loss", "telegram": False, "email": False, "push": False}, headers=h)
    assert r.status_code == 422
    u1 = client.post("/api/v1/alerts/telegram/link", headers=h).json()["url"]
    assert u1.startswith("https://t.me/NexFoxTestBot?start=")
    u2 = client.post("/api/v1/alerts/telegram/link", headers=h).json()["url"]
    assert u1 != u2  # every request issues a new link
    cb = lambda url, chat: client.post("/api/v1/alerts/telegram/callback", headers={"X-Telegram-Callback-Secret": "test-telegram-secret"},  # noqa: E731
                                       json={"token": url.split("start=")[1], "chat_id": chat})
    assert cb(u1, "111").status_code == 404   # superseded link
    assert cb(u2, "222").status_code == 200
    assert cb(u2, "333").status_code == 404   # single use: cannot be replayed to redirect alerts


def test_admin_guard(client, ctx):
    assert client.get("/api/v1/admin/overview", headers=ctx["h"]).status_code == 403
