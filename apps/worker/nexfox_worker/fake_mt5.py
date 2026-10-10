"""Simulated MetaTrader5 module for automated tests and admin self-tests. It never connects to a broker.

It mirrors the parts of the MetaTrader5 Python API the bots use (same function names, constants and return shapes):
a seeded synthetic XAUUSD market (M1 random walk aggregated to M3/M5/M15/H1/H4/D1), a broker that fills market
orders, moves SL/TP, hits stops on each simulated minute, and records deals. Failure injection lets tests exercise
bad credentials, terminal unavailability, disconnects, rejected orders and order_send() returning None (unknown outcome).
"""
from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np

TIMEFRAME_M1, TIMEFRAME_M3, TIMEFRAME_M5, TIMEFRAME_M15 = 1, 3, 5, 15
TIMEFRAME_H1, TIMEFRAME_H4, TIMEFRAME_D1 = 16385, 16388, 16408
_TF_MIN = {TIMEFRAME_M1: 1, TIMEFRAME_M3: 3, TIMEFRAME_M5: 5, TIMEFRAME_M15: 15, TIMEFRAME_H1: 60, TIMEFRAME_H4: 240, TIMEFRAME_D1: 1440}
ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
TRADE_ACTION_DEAL, TRADE_ACTION_SLTP = 1, 6
ORDER_TIME_GTC, ORDER_FILLING_FOK, ORDER_FILLING_IOC = 0, 0, 1
TRADE_RETCODE_DONE, TRADE_RETCODE_REJECT, TRADE_RETCODE_INVALID_STOPS, TRADE_RETCODE_MARKET_CLOSED = 10009, 10006, 10016, 10018
DEAL_TYPE_BUY, DEAL_TYPE_SELL, DEAL_ENTRY_IN, DEAL_ENTRY_OUT = 0, 1, 0, 1
ACCOUNT_TRADE_MODE_DEMO, ACCOUNT_TRADE_MODE_CONTEST, ACCOUNT_TRADE_MODE_REAL = 0, 1, 2
RES_S_OK, RES_E_FAIL, RES_E_AUTH_FAILED, RES_E_INTERNAL_FAIL = 1, -1, -6, -10000
CONTRACT = 100.0
_DTYPE = [("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"), ("close", "<f8"),
          ("tick_volume", "<u8"), ("spread", "<i4"), ("real_volume", "<u8")]
__version__ = "fake-1.0"
_fake = True


class FakeMT5:
    """One instance = one simulated terminal. Install with `sys.modules['MetaTrader5'] = FakeMT5(...)`."""
    _fake = True
    __version__ = __version__

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.cfg = cfg
        g = globals()
        for k, v in g.items():
            if k.isupper():
                setattr(self, k, v)
        self.accounts = {str(k): v for k, v in cfg.get("accounts", {}).items()}
        self.symbol = cfg.get("symbol", "GOLD")
        self.spread_pts = cfg.get("spread_pts", 20)
        self.seed = cfg.get("seed", 7)
        self.days = cfg.get("days", 120)
        # The strategy reads the real clock (D1 bar completion), so the simulated "now" starts at the real time.
        real_now = datetime.now(timezone.utc)
        while real_now.weekday() >= 5:  # the strategy does not trade weekends: anchor the simulation on a weekday
            real_now -= timedelta(days=1)
        midnight = real_now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_day = int(cfg.get("start_minute", 60 * 1440)) // 1440
        start = midnight - timedelta(days=start_day)
        if not cfg.get("exact_start"):  # default: simulated time of day = real time of day
            cfg = {**cfg, "start_minute": start_day * 1440 + (real_now - midnight).seconds // 60}
        self.cfg = cfg
        n = self.days * 1440
        rng = np.random.default_rng(self.seed)
        drift = {"trend_up": 0.004, "trend_down": -0.004}.get(cfg.get("scenario"), 0.0)
        steps = rng.normal(drift, cfg.get("vol", 0.55), n)
        if cfg.get("scenario") == "range":
            steps -= np.convolve(steps, np.ones(240) / 240, mode="same")
        close = 2400 + np.cumsum(steps)
        opn = np.concatenate([[2400.0], close[:-1]])
        wick = np.abs(rng.normal(0, 0.35, n))
        self.m1_time = np.array([int((start + timedelta(minutes=i)).timestamp()) for i in range(n)], dtype=np.int64)
        self.m1 = {"open": opn, "high": np.maximum(opn, close) + wick, "low": np.minimum(opn, close) - wick, "close": close,
                   "vol": rng.integers(50, 500, n).astype(np.uint64)}
        self.cursor = int(cfg.get("start_minute", 60 * 1440))  # index of the current (forming) M1 bar
        self._initialized = False
        self._login: str | None = None
        self._err = (RES_S_OK, "Success")
        self.positions: dict[int, SimpleNamespace] = {}
        self.deals: list[SimpleNamespace] = []
        self._ticket = itertools.count(5_000_000)
        self.balance = 0.0
        self.order_calls = 0
        self.disconnected = False

    # ---------------------------------------------------------------- connection
    def initialize(self, path=None, login=None, password=None, server=None, timeout=None, portable=False, **_):
        if self.cfg.get("fail_initialize"):
            self._err = (RES_E_INTERNAL_FAIL, "IPC initialize failed, MetaTrader 5 x64 not found")
            return False
        self._initialized = True
        if login is not None:
            return self.login(login, password=password, server=server)
        return True

    def login(self, login, password=None, server=None, timeout=None):
        acc = self.accounts.get(str(login))
        if not self._initialized:
            self._err = (RES_E_FAIL, "terminal not initialized")
            return False
        if not acc or acc.get("password") != password or (server and acc.get("server") != server):
            self._err = (RES_E_AUTH_FAILED, "Authorization failed")
            return False
        self._login = str(login)
        if not self.balance:
            self.balance = float(acc.get("balance", 10_000.0))
        self._err = (RES_S_OK, "Success")
        return True

    def shutdown(self):
        self._initialized = False
        return True

    def last_error(self):
        return self._err

    def version(self):
        return (500, 5735, "fake")

    def _ok(self) -> bool:
        if not self._initialized or self._login is None or self.disconnected:
            self._err = (RES_E_FAIL, "terminal not connected")
            return False
        return True

    def terminal_info(self):
        if not self._initialized:
            return None
        return SimpleNamespace(connected=not self.disconnected, trade_allowed=True, build=5735, name="FakeMT5", path="fake")

    def account_info(self):
        if not self._ok():
            return None
        acc = self.accounts[self._login]
        mode = {"demo": ACCOUNT_TRADE_MODE_DEMO, "contest": ACCOUNT_TRADE_MODE_CONTEST, "real": ACCOUNT_TRADE_MODE_REAL}[acc.get("trade_mode", "demo")]
        eq = self.balance + sum(self._pnl(p) for p in self.positions.values())
        return SimpleNamespace(login=int(self._login), server=acc.get("server"), trade_mode=mode, balance=round(self.balance, 2),
                               equity=round(eq, 2), margin_free=round(eq, 2), currency="USD", leverage=500, trade_allowed=True)

    # ---------------------------------------------------------------- market data
    def now(self) -> datetime:
        return datetime.fromtimestamp(int(self.m1_time[self.cursor]), timezone.utc) + timedelta(seconds=30)

    def advance(self, minutes: int = 1) -> None:
        """Move the simulated clock and process SL/TP hits on the bars that passed."""
        for _ in range(minutes):
            if self.cursor + 1 >= len(self.m1_time):
                return
            self.cursor += 1
            self._check_stops(self.m1["high"][self.cursor], self.m1["low"][self.cursor])
        if self.cfg.get("disconnect_after_minutes") and self.cursor - int(self.cfg.get("start_minute", 60 * 1440)) >= self.cfg["disconnect_after_minutes"]:
            self.disconnected = True

    def symbol_select(self, symbol, enable=True):
        return symbol == self.symbol

    def symbol_info(self, symbol):
        if symbol != self.symbol:
            return None
        return SimpleNamespace(name=symbol, point=0.01, digits=2, trade_stops_level=0, trade_tick_value=1.0, trade_tick_size=0.01,
                               volume_min=0.01, volume_max=50.0, volume_step=0.01, trade_contract_size=CONTRACT, visible=True)

    def _mid(self) -> float:
        return float(self.m1["close"][self.cursor])

    def symbol_info_tick(self, symbol):
        if symbol != self.symbol or not self._ok():
            return None
        half = self.spread_pts * 0.01 / 2
        m = self._mid()
        return SimpleNamespace(time=int(self.m1_time[self.cursor]), bid=round(m - half, 2), ask=round(m + half, 2), last=m, volume=1)

    def copy_rates_from_pos(self, symbol, timeframe, start_pos, count):
        if symbol != self.symbol or not self._ok():
            return None
        k = _TF_MIN[timeframe]
        end = self.cursor + 1  # include forming bar
        t0 = self.m1_time[0]
        # bucket M1 bars by timeframe boundaries (aligned to the epoch like MT5)
        first = max(0, end - k * (count + start_pos + 2))
        sl = slice(first, end)
        times = self.m1_time[sl]
        buckets = (times // (k * 60)) * (k * 60)
        uniq, idx = np.unique(buckets, return_index=True)
        out = np.zeros(len(uniq), dtype=_DTYPE)
        o, h, lo, c, v = (self.m1[x][sl] for x in ("open", "high", "low", "close", "vol"))
        last = np.append(idx[1:], len(times)) - 1
        out["time"], out["open"], out["close"] = uniq, o[idx], c[last]
        out["high"], out["low"] = np.maximum.reduceat(h, idx), np.minimum.reduceat(lo, idx)
        out["tick_volume"], out["spread"] = np.add.reduceat(v, idx), self.spread_pts
        _ = t0
        if start_pos:
            out = out[:-start_pos]
        return out[-count:]

    def copy_rates_range(self, symbol, timeframe, date_from, date_to):
        return self.copy_rates_from_pos(symbol, timeframe, 0, 5000)

    # ---------------------------------------------------------------- trading
    def _pnl(self, p) -> float:
        tick_bid = self._mid() - self.spread_pts * 0.005
        tick_ask = self._mid() + self.spread_pts * 0.005
        px = tick_bid if p.type == ORDER_TYPE_BUY else tick_ask
        d = 1 if p.type == ORDER_TYPE_BUY else -1
        return (px - p.price_open) * d * p.volume * CONTRACT

    def positions_get(self, symbol=None, ticket=None, **_):
        if not self._ok():
            return None
        ps = [p for p in self.positions.values() if (symbol is None or p.symbol == symbol) and (ticket is None or p.ticket == ticket)]
        for p in ps:
            p.profit = round(self._pnl(p), 2)
        return tuple(ps)

    def history_deals_get(self, date_from=None, date_to=None, group=None, position=None, ticket=None, **_):
        if not self._ok():
            return None
        out = self.deals
        if position is not None:
            out = [d for d in out if d.position_id == position]
        elif date_from is not None:
            f = int(date_from.timestamp()) if hasattr(date_from, "timestamp") else int(date_from)
            out = [d for d in out if d.time >= f]
        return tuple(out)

    def _result(self, retcode, comment, order=0, deal=0, price=0.0, volume=0.0, request=None):
        return SimpleNamespace(retcode=retcode, comment=comment, order=order, deal=deal, price=price, volume=volume,
                               request=SimpleNamespace(**(request or {})))

    def order_send(self, request: dict):
        self.order_calls += 1
        if not self._ok():
            return None
        every = self.cfg.get("order_send_none_every")
        if every and self.order_calls % every == 0:
            if self.cfg.get("none_but_filled"):
                self._fill(request)  # the order DID execute but the caller never learns it: must be reconciled
            self._err = (RES_E_INTERNAL_FAIL, "IPC timeout")
            return None
        if self.cfg.get("reject_retcode"):
            return self._result(int(self.cfg["reject_retcode"]), "Rejected by fake broker", request=request)
        action = request.get("action")
        if action == TRADE_ACTION_SLTP:
            p = self.positions.get(request.get("position"))
            if not p:
                return self._result(TRADE_RETCODE_REJECT, "Position not found", request=request)
            p.sl, p.tp = float(request.get("sl") or 0), float(request.get("tp") or 0)
            return self._result(TRADE_RETCODE_DONE, "Request executed", request=request)
        if action == TRADE_ACTION_DEAL:
            if request.get("position"):
                return self._close(request)
            return self._fill(request)
        return self._result(TRADE_RETCODE_REJECT, "Unsupported action", request=request)

    def _fill(self, request):
        tick = self.symbol_info_tick(request["symbol"])
        buy = request["type"] == ORDER_TYPE_BUY
        price = tick.ask if buy else tick.bid
        sl, tp = float(request.get("sl") or 0), float(request.get("tp") or 0)
        if (buy and sl and sl >= price) or (not buy and sl and sl <= price):
            return self._result(TRADE_RETCODE_INVALID_STOPS, "Invalid stops", request=request)
        t = next(self._ticket)
        p = SimpleNamespace(ticket=t, identifier=t, type=request["type"], volume=float(request["volume"]), price_open=price, sl=sl, tp=tp,
                            magic=request.get("magic", 0), comment=(request.get("comment") or "")[:31], symbol=request["symbol"],
                            time=int(self.m1_time[self.cursor]), profit=0.0)
        self.positions[t] = p
        d = next(self._ticket)
        self.deals.append(SimpleNamespace(ticket=d, order=t, position_id=t, time=p.time, type=DEAL_TYPE_BUY if buy else DEAL_TYPE_SELL,
                                          entry=DEAL_ENTRY_IN, magic=p.magic, volume=p.volume, price=price, profit=0.0, swap=0.0,
                                          commission=0.0, comment=p.comment, symbol=p.symbol, reason=0))
        return self._result(TRADE_RETCODE_DONE, "Request executed", order=t, deal=d, price=price, volume=p.volume, request=request)

    def _close(self, request, reason_comment: str | None = None, price: float | None = None):
        p = self.positions.get(request["position"])
        if not p:
            return self._result(TRADE_RETCODE_REJECT, "Position not found", request=request)
        vol = min(float(request.get("volume", p.volume)), p.volume)
        tick = self.symbol_info_tick(p.symbol) if price is None else None
        px = price if price is not None else (tick.bid if p.type == ORDER_TYPE_BUY else tick.ask)
        dsign = 1 if p.type == ORDER_TYPE_BUY else -1
        profit = round((px - p.price_open) * dsign * vol * CONTRACT, 2)
        self.balance += profit
        d = next(self._ticket)
        self.deals.append(SimpleNamespace(ticket=d, order=d, position_id=p.ticket, time=int(self.m1_time[self.cursor]),
                                          type=DEAL_TYPE_SELL if p.type == ORDER_TYPE_BUY else DEAL_TYPE_BUY, entry=DEAL_ENTRY_OUT,
                                          magic=p.magic, volume=vol, price=px, profit=profit, swap=0.0, commission=0.0,
                                          comment=reason_comment or (request.get("comment") or ""), symbol=p.symbol, reason=0))
        p.volume = round(p.volume - vol, 2)
        if p.volume <= 0:
            self.positions.pop(p.ticket, None)
        return self._result(TRADE_RETCODE_DONE, "Request executed", order=d, deal=d, price=px, volume=vol, request=request)

    def _check_stops(self, high: float, low: float) -> None:
        for p in list(self.positions.values()):
            buy = p.type == ORDER_TYPE_BUY
            if p.sl and ((buy and low <= p.sl) or (not buy and high >= p.sl)):
                self._close({"position": p.ticket, "volume": p.volume}, "[sl]", price=p.sl)
            elif p.tp and ((buy and high >= p.tp) or (not buy and low <= p.tp)):
                self._close({"position": p.ticket, "volume": p.volume}, "[tp]", price=p.tp)
