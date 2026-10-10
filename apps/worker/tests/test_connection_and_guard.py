"""MT5 connection checks and the execution guard, against the simulated terminal."""
from types import SimpleNamespace

from conftest import DEMO

from nexfox_worker import mt5_backend
from nexfox_worker.fake_mt5 import FakeMT5
from nexfox_worker.guard import ExecutionGuard, coid_from_comment, tag_comment
from nexfox_worker.sinks import LocalSink


def _connect(fake_cfg, login="7100001", pw="demo-pass", server="Fake-Demo", declared="demo", symbol="GOLD"):
    mt5 = FakeMT5({"accounts": DEMO, **fake_cfg})
    return mt5_backend.connect(mt5, "fake", login, pw, server, declared, symbol, "fake")


def test_connect_ok_reports_identity_and_mode():
    r = _connect({})
    assert r.ok and r.status == "ok" and r.trade_mode == "demo" and r.login == "7100001" and r.server == "Fake-Demo"


def test_connect_failures_are_classified():
    assert _connect({}, pw="wrong").status == "auth_failed"
    assert _connect({}, pw="wrong").code == "MT5_AUTH"
    assert _connect({"fail_initialize": True}).status == "terminal_unavailable"
    assert _connect({}, symbol="XAUUSD").status == "symbol_unavailable"
    r = _connect({}, login="7100002", pw="real-pass", server="Fake-Live", declared="demo")
    assert r.status == "account_mismatch" and "REAL" in r.error  # registered as demo, terminal says real money


def test_connect_never_returns_password():
    r = _connect({}, pw="wrong")
    assert "wrong" not in str(r.connection()) and "demo-pass" not in str(_connect({}).connection())


def test_comment_tagging_fits_mt5_limit_and_keeps_prefix():
    c = tag_comment("TP1_LQ_H1_buy", "abcdef123456")
    assert len(c) <= 31 and c.startswith("TP1_") and coid_from_comment(c) == "abcdef123456"
    assert len(tag_comment("X" * 40, "abcdef123456")) <= 31
    assert coid_from_comment("no tag") is None


def _guard(state_over=None, sink=None, fake=None):
    mt5 = FakeMT5({"accounts": DEMO, **(fake or {})})
    mt5.initialize(login=7100001, password="demo-pass", server="Fake-Demo")
    state = {"desired_state": "running", "trading": {"entitled": True, "max_lot_hard": 5.0},
             "config": {"symbol": "GOLD", "max_lot": 0.10, "dry_run": False}}
    state.update(state_over or {})
    return mt5, ExecutionGuard(mt5, sink or LocalSink(), state)


REQ = {"symbol": "GOLD", "volume": 0.05, "price": 2400.0, "sl": 2390.0, "tp": 2420.0, "comment": "TP1_LQ_D1_buy", "type": 0, "action": 1}


def test_guard_local_refusals():
    for over, why in [({"desired_state": "paused"}, "paused"), ({"trading": {"halted": True}}, "halted"),
                      ({"trading": {"entitled": False, "entitlement_reason": "trial_expired"}}, "subscription")]:
        _, g = _guard(over)
        d = g.before_order(dict(REQ), "buy")
        assert not d["allowed"] and why in d["reason"]
    _, g = _guard()
    assert "max lot" in g.before_order({**REQ, "volume": 0.5}, "buy")["reason"]
    assert "stop loss" in g.before_order({**REQ, "sl": 0}, "buy")["reason"]
    assert "symbol" in g.before_order({**REQ, "symbol": "EURUSD"}, "buy")["reason"]


def test_guard_platform_refusal_is_not_sent():
    class Refusing(LocalSink):
        def authorize(self, body):
            return {"approved": False, "reason": "daily order cap reached", "code": "RISK_LIMIT"}
    _, g = _guard(sink=Refusing())
    assert not g.before_order(dict(REQ), "buy")["allowed"]


def test_guard_fill_reject_and_uncertain_with_reconciliation():
    sink = LocalSink()
    mt5, g = _guard(sink=sink)
    ask = mt5.symbol_info_tick("GOLD").ask
    REQ.update(price=ask, sl=round(ask - 10, 2), tp=round(ask + 20, 2))
    d = g.before_order(dict(REQ), "buy")
    assert d["allowed"] and d["request"]["comment"].endswith(d["client_order_id"])
    g.after_order(d, mt5.order_send(d["request"]))
    assert sink.results[-1]["status"] == "filled"
    g.after_order(d, SimpleNamespace(retcode=10016, comment="Invalid stops", order=0, deal=0, price=0))
    assert sink.results[-1]["status"] == "rejected" and sink.results[-1]["retcode"] == 10016
    # order_send returns None but the order DID execute -> uncertain, then reconciled as filled; no retry
    mt5.cfg.update(order_send_none_every=1, none_but_filled=True)
    d2 = g.before_order(dict(REQ), "buy")
    sent_before = mt5.order_calls
    g.after_order(d2, mt5.order_send(d2["request"]))
    statuses = [r["status"] for r in sink.results if r["client_order_id"] == d2["client_order_id"]]
    assert statuses == ["uncertain", "reconciled_filled"] and mt5.order_calls == sent_before + 1
    assert not g.unresolved
    # None and NOT executed -> stays unresolved (entries blocked) until 30 s, then reconciled_absent
    mt5.cfg.update(none_but_filled=False)
    d3 = g.before_order(dict(REQ), "buy")
    g.after_order(d3, mt5.order_send(d3["request"]))
    assert d3["client_order_id"] in g.unresolved and not g.before_order(dict(REQ), "buy")["allowed"]
    g.unresolved[d3["client_order_id"]]["sent_at"] -= 60
    g.reconcile()
    assert not g.unresolved and sink.results[-1]["status"] == "reconciled_absent"


def test_unresolved_from_previous_run_marked_uncertain_first():
    sink = LocalSink()
    _, g = _guard(sink=sink)
    g.load_unresolved([{"client_order_id": "deadbeef0001", "status": "approved"}, {"client_order_id": "deadbeef0002", "status": "uncertain"}])
    assert [r["status"] for r in sink.results] == ["uncertain"]
    g.reconcile()
    # same 30 s grace as a fresh order (deal history can lag after a terminal reconnect): still blocking entries
    assert len(g.unresolved) == 2 and not g.before_order(dict(REQ), "buy")["allowed"]
    for v in g.unresolved.values():
        v["sent_at"] -= 60
    g.reconcile()
    assert [r["status"] for r in sink.results][1:] == ["reconciled_absent", "reconciled_absent"] and not g.unresolved


def test_dry_run_never_calls_order_send():
    import importlib
    import sys
    from pathlib import Path
    from conftest import BOTS
    sink = LocalSink()
    mt5, g = _guard({"config": {"symbol": "GOLD", "max_lot": 0.10, "dry_run": True}}, sink=sink)
    sys.modules["MetaTrader5"] = mt5
    sys.path.insert(0, str(Path(BOTS) / "rehman_bot"))
    for m in ("config_new", "trade_engine"):
        sys.modules.pop(m, None)
    te = importlib.import_module("trade_engine")
    te.SYMBOL = "GOLD"
    te.set_execution_guard(g)
    tick = mt5.symbol_info_tick("GOLD")
    res = te.open_trade("buy", tick.ask, tick.ask - 10, tick.ask + 25, lot_size=0.02, comment="TP1_LQ_D1_buy", min_rr=2.0)
    assert res["success"] and res["dry_run"] and mt5.order_calls == 0 and sink.results[-1]["status"] == "simulated"
    te.set_execution_guard(None)


def test_guard_refuses_entries_once_local_lease_deadline_passed():
    mt5 = FakeMT5({"accounts": DEMO})
    mt5.initialize(login=7100001, password="demo-pass", server="Fake-Demo")
    state = {"desired_state": "running", "trading": {"entitled": True}, "config": {"symbol": "GOLD", "max_lot": 0.10}}
    sink = LocalSink()
    g = ExecutionGuard(mt5, sink, state, lease_valid=lambda: False)
    d = g.before_order(dict(REQ), "buy")
    assert not d["allowed"] and "lease" in d["reason"] and sink.intents == []  # not even asked for approval


def _runner_with_position(dry_run: bool, lease_until=None):
    """A RehmanRunner wired to the simulated terminal with one open position carrying the bot's magic number."""
    from conftest import BOTS
    from pathlib import Path
    from nexfox_worker.runners.rehman import RehmanRunner, _ManagedOrders
    mt5 = FakeMT5({"accounts": DEMO})
    mt5.initialize(login=7100001, password="demo-pass", server="Fake-Demo")
    t = mt5.symbol_info_tick("GOLD")
    r = mt5.order_send({"action": mt5.TRADE_ACTION_DEAL, "symbol": "GOLD", "volume": 0.02, "type": mt5.ORDER_TYPE_BUY,
                        "price": t.ask, "sl": t.ask - 10, "tp": t.ask + 20, "magic": 88880001, "comment": "TP1_test"})
    assert r.retcode == mt5.TRADE_RETCODE_DONE
    spec = {"instance_id": 1, "config": {"symbol": "GOLD", "dry_run": dry_run}, "account": {}, "lease_until": lease_until}
    runner = RehmanRunner(spec, "fake", LocalSink(), Path(BOTS), Path("."), "fake")
    runner.mt5 = mt5
    runner.cfg = SimpleNamespace(SYMBOL="GOLD", MAGIC_NUMBER=88880001)
    return runner, mt5, _ManagedOrders(mt5, runner)


def test_management_orders_are_simulated_in_dry_run():
    runner, mt5, managed = _runner_with_position(dry_run=True)
    pos = mt5.positions_get(symbol="GOLD")[0]
    calls = mt5.order_calls
    assert managed.order_send({"action": mt5.TRADE_ACTION_SLTP, "position": pos.ticket, "sl": pos.price_open, "tp": pos.tp}) is None
    assert runner.close_all("test") == 0
    assert mt5.order_calls == calls and len(mt5.positions_get(symbol="GOLD")) == 1
    assert any("Dry run: would" in e["message"] for e in runner.sink.events)


def test_management_orders_refused_after_lease_deadline():
    import time as _t
    runner, mt5, managed = _runner_with_position(dry_run=False, lease_until=_t.time() - 1)
    pos = mt5.positions_get(symbol="GOLD")[0]
    calls = mt5.order_calls
    assert managed.order_send({"action": mt5.TRADE_ACTION_SLTP, "position": pos.ticket, "sl": pos.price_open, "tp": pos.tp}) is None
    assert runner.close_all("test") == 0 and mt5.order_calls == calls
    runner.lease_until = _t.time() + 30  # a successful sync extends the lease: management works again
    assert managed.order_send({"action": mt5.TRADE_ACTION_SLTP, "position": pos.ticket, "sl": pos.price_open, "tp": pos.tp}) is not None
    assert mt5.order_calls == calls + 1


def test_redelivered_command_runs_once():
    runner, mt5, _ = _runner_with_position(dry_run=False)
    runner._command({"id": 7, "type": "close_all"})
    assert len(mt5.positions_get(symbol="GOLD") or ()) == 0
    calls = mt5.order_calls
    runner._command({"id": 7, "type": "close_all"})  # same id delivered again before the ack reached the server
    assert mt5.order_calls == calls and runner.acked == [7, 7]


def test_supervisor_self_fences_when_platform_unreachable(tmp_path):
    """A cut-off worker must stop its bots before the server can hand their leases to another worker."""
    import time as _t
    from nexfox_worker.api import ApiError
    from nexfox_worker.config import WorkerConfig
    from nexfox_worker.supervisor import Child, Supervisor

    class DownApi:
        def sync(self, body):
            raise ApiError(0, "connection refused")

    class Proc:
        alive, terminated = True, False

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.alive, self.terminated = False, True

        def join(self, timeout=None):
            pass

    class Q(list):
        def put(self, m):
            self.append(m)

    cfg = WorkerConfig(api_url="http://x", token="t", name="t", terminals=["fake-a"], data_dir=tmp_path, bots_dir=tmp_path)
    sup = Supervisor(cfg, api=DownApi())
    proc, control = Proc(), Q()
    sup.children[1] = Child({"instance_id": 1, "lease_epoch": 3}, "fake-a", proc, control)
    sup.tick()  # a single failed sync: keep running (transient network blip)
    assert control == [] and proc.alive
    sup.last_sync_ok = _t.time() - sup.lease_seconds * 0.6  # silent for more than half a lease
    sup.tick()
    assert control == [{"type": "stop"}] and sup.fenced
    sup.children[1].stopping_since -= 60  # the child ignores the stop request: it is killed
    sup.tick()
    assert proc.terminated


def test_journal_replay_keeps_unsent_results_when_lease_lost(tmp_path):
    import json
    from nexfox_worker.api import LeaseLost
    from nexfox_worker.sinks import PlatformSink

    class Api:
        def __init__(self):
            self.sent = []

        def intent_result(self, iid, coid, payload):
            if len(self.sent) == 1:
                raise LeaseLost("lease_lost")
            self.sent.append(coid)

    api = Api()
    sink = PlatformSink(api, 5, 1, None, tmp_path)
    sink.journal.write_text("".join(json.dumps({"client_order_id": c, "body": {"status": "filled"}}) + "\n" for c in ("a1", "a2", "a3")),
                            encoding="utf-8")
    try:
        sink._replay_journal()
        raise AssertionError("LeaseLost expected")
    except LeaseLost:
        pass
    left = [json.loads(x)["client_order_id"] for x in sink.journal.read_text(encoding="utf-8").splitlines()]
    assert api.sent == ["a1"] and left == ["a2", "a3"]
