"""Pretend to be the MT5 EA so the whole platform can be demoed without MetaTrader.

It calls the real EA Gateway (handshake, heartbeat every 2 s, trade open/close events), obeys
pause / resume / close_all / kill commands, applies config versions, and verifies every signature.

Usage:
  python scripts/ea_simulator.py --api http://localhost:8000 --key AUSC-XXXX-XXXX-XXXX-XXXX --login 5102948 --server ICMarketsSC-Demo
"""
import argparse
import hashlib
import hmac
import json
import os
import random
import time

import httpx

SIGN_SECRET = os.environ.get("EA_SIGNING_SECRET", "dev-only-ea-signing-secret-change-me")


def verify(resp: dict) -> dict:
    raw, sig = resp["payload"], resp["sig"]
    good = hmac.compare_digest(hmac.new(SIGN_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest(), sig)
    body = json.loads(raw)
    if not good or body["exp"] < time.time():
        raise SystemExit("Signature check failed: refusing to trade (this is what the real EA does).")
    return body


class SimEA:
    def __init__(self, api: str, key: str, login: str, server: str, account_type: str):
        self.c = httpx.Client(base_url=api, timeout=10)
        self.auth = {"license_key": key, "login": login, "server": server, "ea_version": "sim-1.0", "account_type": account_type}
        self.price, self.balance = 3412.0, 10000.0
        self.positions: dict[str, dict] = {}
        self.cfg: dict = {}
        self.cfg_version, self.applied, self.enabled, self.valid = 0, 0, True, False
        self.acks: list[int] = []
        self.ticket = int(time.time())

    def post(self, path: str, body: dict) -> dict:
        r = self.c.post(path, json={**self.auth, **body})
        if r.status_code != 200:
            raise SystemExit(f"{path} -> {r.status_code}: {r.text}")
        return verify(r.json())

    def handshake(self) -> None:
        b = self.post("/ea/v1/handshake", {})
        self.valid = b["license"]["valid"]
        self.cfg = b["config"]
        self.cfg_version = self.applied = b["config"]["config_version"]
        self.enabled = b["config"]["trading_enabled"]
        print(f"handshake: license={b['license']['reason']} preset={self.cfg['preset']} risk={self.cfg['risk_pct']}% enabled={self.enabled}")
        self.run_commands(b["commands"])

    def run_commands(self, cmds: list[dict]) -> None:
        for c in cmds:
            print("command:", c["type"])
            if c["type"] in ("close_all", "kill"):
                for t in list(self.positions):
                    self.close(t, "Kill switch" if c["type"] == "kill" else "Manual close")
            if c["type"] in ("pause", "kill"):
                self.enabled = False
            if c["type"] == "resume":
                self.enabled = True
            self.acks.append(c["id"])

    def pnl(self, p: dict) -> float:
        d = 1 if p["side"] == "BUY" else -1
        return (self.price - p["entry"]) * d * p["lots"] * 100

    def equity(self) -> float:
        return self.balance + sum(self.pnl(p) for p in self.positions.values())

    def open(self) -> None:
        side = random.choice(["BUY", "SELL"])
        sl_pts, tp_pts = self.cfg.get("sl_pts", 160), self.cfg.get("tp_pts", 320)
        sl_dist = sl_pts / 100
        lots = max(0.01, min(self.cfg.get("max_lot", 1.0), int(self.equity() * self.cfg.get("risk_pct", 1) / 100 / (sl_dist * 100) * 100) / 100))
        slip = round(abs(random.gauss(0.2, 0.25)), 1)
        entry = self.price + (0.01 * slip if side == "BUY" else -0.01 * slip)
        self.ticket += 1
        t = str(self.ticket)
        d = 1 if side == "BUY" else -1
        self.positions[t] = {"side": side, "lots": lots, "entry": entry, "sl": entry - d * sl_dist, "tp": entry + d * tp_pts / 100,
                             "opened": time.time(), "module": random.choice(["momentum", "breakout", "vwap"])}
        p = self.positions[t]
        self.post("/ea/v1/trades", {"events": [{"type": "open", "ticket": t, "side": side, "volume": lots, "price": round(entry, 2),
                                                 "requested_price": round(self.price, 2), "sl": round(p["sl"], 2), "tp": round(p["tp"], 2),
                                                 "time": int(time.time()), "slippage_pts": slip, "spread": self.spread,
                                                 "latency_ms": random.randint(3, 25), "module": p["module"]}]})
        print(f"open  {side} {lots:.2f} @ {entry:.2f}")

    def close(self, t: str, reason: str) -> None:
        p = self.positions.pop(t)
        profit = round(self.pnl(p), 2)
        commission = round(-3.5 * p["lots"], 2)
        self.balance += profit + commission
        self.post("/ea/v1/trades", {"events": [{"type": "close", "ticket": t, "side": p["side"], "volume": p["lots"],
                                                 "price": round(self.price, 2), "time": int(time.time()), "profit": profit,
                                                 "commission": commission, "reason": reason}]})
        print(f"close {p['side']} {p['lots']:.2f} {profit:+.2f} ({reason})")

    def step(self) -> None:
        self.price += random.gauss(0.0, 0.35)
        self.spread = max(12, int(random.gauss(19, 4)))
        for t, p in list(self.positions.items()):
            d = 1 if p["side"] == "BUY" else -1
            if (self.price - p["tp"]) * d >= 0:
                self.close(t, "TP")
            elif (self.price - p["sl"]) * d <= 0:
                self.close(t, "SL")
            elif time.time() - p["opened"] > self.cfg.get("time_stop_sec", 180):
                self.close(t, "Time stop")
        can_trade = self.valid and self.enabled and self.spread <= self.cfg.get("max_spread_pts", 25)
        if can_trade and len(self.positions) < self.cfg.get("max_open", 2) and random.random() < 0.12:
            self.open()

    def heartbeat(self) -> None:
        half = self.spread / 200
        b = self.post("/ea/v1/heartbeat", {"balance": round(self.balance, 2), "equity": round(self.equity(), 2),
                                           "bid": round(self.price - half, 2), "ask": round(self.price + half, 2),
                                           "spread": self.spread, "ping_ms": random.randint(3, 9),
                                           "open_positions": len(self.positions),
                                           "state": "running" if self.enabled else "paused",
                                           "applied_config_version": self.applied, "acked_commands": self.acks})
        self.acks = []
        self.valid = b["license"]["valid"]
        self.run_commands(b["commands"])
        if b["trading_enabled"] is False and self.enabled:
            self.enabled = False
        if b["config_version"] > self.applied:
            self.handshake()  # fetch the new config, then report it applied on the next heartbeat

    def upload_history(self, n: int = 240) -> None:
        """Like the real EA's CopyRates() upload: send the last n M1 bars once at start."""
        now = int(time.time()) // 60 * 60
        p, bars = self.price - random.gauss(0, 3), []
        for i in range(n, 0, -1):
            o = p
            c = o + random.gauss(0.02, 1.2)
            bars.append({"time": now - i * 60, "open": round(o, 2), "high": round(max(o, c) + abs(random.gauss(0, .6)), 2),
                         "low": round(min(o, c) - abs(random.gauss(0, .6)), 2), "close": round(c, 2)})
            p = c
        self.price = p
        self.post("/ea/v1/candles", {"timeframe": "M1", "bars": bars})
        print(f"uploaded {n} M1 bars")

    def run(self) -> None:
        self.spread = 18
        self.handshake()
        self.upload_history()
        while True:
            for _ in range(4):
                self.step()
                time.sleep(0.5)
            self.heartbeat()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--key", required=True)
    ap.add_argument("--login", required=True)
    ap.add_argument("--server", required=True)
    ap.add_argument("--account-type", default="demo")
    a = ap.parse_args()
    SimEA(a.api, a.key, a.login, a.server, a.account_type).run()
