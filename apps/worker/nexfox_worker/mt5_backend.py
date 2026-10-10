"""Choose the MetaTrader5 implementation for this process (real package or simulator) and do the connection checks.

The MetaTrader5 package keeps ONE terminal connection per Python process, so every bot instance runs in its own
process with its own terminal installation (account-to-terminal association, see terminals.py).
"""
import sys
from dataclasses import dataclass

TRADE_MODES = {0: "demo", 1: "contest", 2: "real"}


def install(backend: str, fake_config: dict | None = None):
    """Install the MetaTrader5 module into sys.modules before any bot code imports it."""
    if backend == "fake":
        from .fake_mt5 import FakeMT5
        mod = FakeMT5(fake_config or {})
        sys.modules["MetaTrader5"] = mod
        return mod
    import MetaTrader5 as mt5  # noqa: real package, Windows only
    return mt5


@dataclass
class ConnectResult:
    ok: bool
    status: str                 # ok | auth_failed | terminal_unavailable | account_mismatch | symbol_unavailable | error
    code: str | None = None     # platform error code
    error: str | None = None
    trade_mode: str | None = None
    login: str | None = None
    server: str | None = None
    terminal_build: int | None = None

    def connection(self) -> dict:
        return {"status": self.status, "error": self.error, "trade_mode": self.trade_mode, "login": self.login, "server": self.server}


def connect(mt5, terminal_path: str, login: str, password: str, server: str, declared_type: str, symbol: str | None,
            backend: str) -> ConnectResult:
    """initialize + login + identity + trade mode + symbol. Never logs or returns the password."""
    kwargs = {"login": int(login), "password": password, "server": server, "timeout": 60_000}
    if backend != "fake":
        kwargs.update(path=terminal_path, portable=True)  # portable: each terminal dir keeps its own account data
    try:
        ok = mt5.initialize(**kwargs)
    except Exception as e:
        return ConnectResult(False, "terminal_unavailable", "MT5_CONNECTION", f"initialize raised {type(e).__name__}")
    if not ok:
        code, msg = mt5.last_error()
        if code == -6:
            return ConnectResult(False, "auth_failed", "MT5_AUTH", f"login failed: {msg}")
        return ConnectResult(False, "terminal_unavailable", "MT5_CONNECTION", f"terminal initialize failed ({code}): {msg}")
    info = mt5.account_info()
    term = mt5.terminal_info()
    if info is None:
        code, msg = mt5.last_error()
        return ConnectResult(False, "error", "MT5_CONNECTION", f"account_info unavailable ({code}): {msg}")
    mode = TRADE_MODES.get(int(info.trade_mode), "real")
    res = ConnectResult(True, "ok", trade_mode=mode, login=str(info.login), server=str(info.server),
                        terminal_build=getattr(term, "build", None))
    if str(info.login) != str(login) or (server and str(info.server) != server):
        res.ok, res.status, res.code = False, "account_mismatch", "ACCOUNT_MISMATCH"
        res.error = f"terminal is logged in to {info.login}@{info.server}, expected {login}@{server}"
        return res
    if mode == "real" and declared_type != "live":
        res.ok, res.status, res.code = False, "account_mismatch", "ACCOUNT_MISMATCH"
        res.error = "terminal reports a REAL-money account but it was registered as demo"
        return res
    if symbol:
        if not mt5.symbol_select(symbol, True) or mt5.symbol_info(symbol) is None or mt5.symbol_info_tick(symbol) is None:
            res.ok, res.status, res.code = False, "symbol_unavailable", "MT5_CONNECTION"
            res.error = f"symbol {symbol} is not available on this account"
            return res
    return res
