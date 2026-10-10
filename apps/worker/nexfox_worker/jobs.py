"""One-shot worker jobs, each run in its own process (MetaTrader5 is one connection per process)."""
import tempfile
from pathlib import Path

from . import mt5_backend


def verify_account(payload: dict, terminal: str, backend: str, fake_config: dict | None) -> dict:
    """Real connection check: initialize + login + identity + trade mode + symbol. Places no orders."""
    mt5 = mt5_backend.install(backend, fake_config)
    a = payload["account"]
    try:
        res = mt5_backend.connect(mt5, terminal, a["login"], a.get("password") or "", a["server"], a.get("declared_type", "demo"),
                                  payload.get("symbol"), backend)
    finally:
        try:
            mt5.shutdown()
        except Exception:
            pass
    return {"status": "passed" if res.ok else "failed", "connection": res.connection(),
            "result": {"ok": res.ok, "status": res.status, "error": res.error, "trade_mode": res.trade_mode,
                       "terminal_build": res.terminal_build}}


def self_test(payload: dict, bots_dir: Path, fake_overrides: dict | None = None) -> dict:
    """Run the bot version against the SIMULATED market (never a real terminal) and report what it did."""
    from .runners import RUNNERS
    from .sinks import LocalSink
    code = payload["bot_code"]
    if code not in RUNNERS or payload["bot_version"] not in RUNNERS[code]["versions"]:
        return {"status": "failed", "result": {"error": f"{code} {payload['bot_version']} is not installed on this worker"}}
    scenario = payload.get("scenario", "synthetic")
    fake = {"accounts": {"900001": {"password": "self-test", "server": "SelfTest-Demo", "trade_mode": "demo", "balance": 10_000}},
            "seed": 7, "symbol": payload["config"].get("symbol", "GOLD"),
            **({"scenario": scenario} if scenario != "synthetic" else {}), **(fake_overrides or {})}
    spec = {"instance_id": 0, "config": payload["config"], "desired_state": "running", "trading": {"entitled": True, "max_lot_hard": 5.0},
            "account": {"login": "900001", "password": "self-test", "server": "SelfTest-Demo", "declared_type": "demo"}}
    sink = LocalSink()
    with tempfile.TemporaryDirectory() as d:
        runner = RUNNERS[code]["runner"](spec, "fake", sink, bots_dir, Path(d), "fake", fake,
                                         max_iterations=int(payload.get("iterations", 60)), fast=True)
        res = runner.run()
    errors = [e for e in sink.events if e["level"] == "error"]
    ok = bool(res.get("ok")) and not errors
    return {"status": "passed" if ok else "failed",
            "result": {"ok": ok, "iterations": res.get("iterations"), "guard": res.get("guard"), "orders_approved": len(sink.intents),
                       "order_results": [r["status"] for r in sink.results][:50], "trade_events": len(sink.trades),
                       "errors": [e["message"] for e in errors][:20], "scenario": scenario, "simulated": True,
                       "runtime": getattr(sink, "runtime_info", None)}}
