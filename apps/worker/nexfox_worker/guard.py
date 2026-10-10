"""Execution guard installed into the bot's order path (bots/rehman_bot/trade_engine.EXECUTION_GUARD).

before_order: local checks (paused, halted, entitlement, unresolved orders, symbol, lot cap, stop loss), then a
              server-approved intent. Fail-safe: if the platform cannot be reached, the order is NOT sent.
after_order : reports filled / rejected / uncertain; an uncertain outcome is reconciled against MT5 positions and
              deal history using the client order id embedded in the order comment.
"""
import logging
import time
import uuid

from .api import LeaseLost

log = logging.getLogger("worker.guard")
RETCODE_DONE = {10008, 10009, 10010}  # placed, done, done partial


def tag_comment(comment: str, coid: str) -> str:
    """MT5 comments are max 31 chars. Keep the bot's prefix (the strategy reads it, e.g. 'TP1_') + '#' + id."""
    base = (comment or "")[: 31 - len(coid) - 1]
    return f"{base}#{coid}"


def coid_from_comment(comment: str | None) -> str | None:
    if comment and "#" in comment:
        c = comment.rsplit("#", 1)[1]
        return c if c.isalnum() else None
    return None


class ExecutionGuard:
    def __init__(self, mt5, sink, state: dict, lease_valid=None):
        """state is shared with the runner: desired_state, trading flags, config. lease_valid: local lease check."""
        self.mt5, self.sink, self.state = mt5, sink, state
        self.lease_valid = lease_valid or (lambda: True)
        self.unresolved: dict[str, dict] = {}
        self.stats = {"approved": 0, "refused": 0, "filled": 0, "rejected": 0, "uncertain": 0, "simulated": 0}

    # -------------------------------------------------------------- before
    def _local_refusal(self, request: dict) -> str | None:
        s, cfg = self.state, self.state["config"]
        if not self.lease_valid():
            return "lease could not be confirmed with the platform"
        if s["desired_state"] != "running":
            return f"bot is {s['desired_state']}: no new entries"
        t = s.get("trading", {})
        if t.get("halted"):
            return "trading halted platform-wide"
        if t.get("entitled") is False:
            return f"subscription not active ({t.get('entitlement_reason')})"
        if self.unresolved:
            return f"previous order {next(iter(self.unresolved))} outcome unknown; reconciling"
        if request.get("symbol") != cfg.get("symbol"):
            return "wrong symbol"
        cap = min(float(cfg.get("max_lot", 0.01)), float(t.get("max_lot_hard", 5.0)))
        if float(request.get("volume", 0)) > cap + 1e-9:
            return f"volume above max lot {cap}"
        if not request.get("sl"):
            return "order has no stop loss"
        return None

    def before_order(self, request: dict, direction: str) -> dict:
        why = self._local_refusal(request)
        if why:
            self.stats["refused"] += 1
            self.sink.event(f"Entry blocked: {why}", level="info", kind="risk", code="RISK_LIMIT")
            return {"allowed": False, "reason": why}
        coid = uuid.uuid4().hex[:12]
        dry = bool(self.state["config"].get("dry_run"))
        body = {"client_order_id": coid, "symbol": request["symbol"], "side": "BUY" if direction == "buy" else "SELL",
                "volume": float(request["volume"]), "price": float(request.get("price") or 0) or None,
                "sl": float(request.get("sl") or 0) or None, "tp": float(request.get("tp") or 0) or None,
                "comment": request.get("comment"), "dry_run": dry}
        res = self.sink.authorize(body)  # LeaseLost propagates: the runner stops immediately
        if not res.get("approved"):
            self.stats["refused"] += 1
            self.sink.event(f"Entry refused by platform: {res.get('reason')}", level="warn", kind="risk", code=res.get("code"))
            return {"allowed": False, "reason": res.get("reason") or "refused"}
        self.stats["approved"] += 1
        req = dict(request)
        req["comment"] = tag_comment(request.get("comment", ""), coid)
        return {"allowed": True, "request": req, "client_order_id": coid, "dry_run": dry, "body": body, "sent_at": time.time()}

    # -------------------------------------------------------------- after
    def after_order(self, decision: dict, result) -> None:
        coid = decision["client_order_id"]
        if decision.get("dry_run"):
            self.stats["simulated"] += 1
            self.sink.result(coid, {"status": "simulated"})
            self.sink.event(f"Dry run: would send {decision['body']['side']} {decision['body']['volume']}", kind="order")
            return
        if result is None:
            self.stats["uncertain"] += 1
            err = self.mt5.last_error()
            self.unresolved[coid] = {"client_order_id": coid, **decision["body"], "sent_at": decision["sent_at"]}
            self.sink.result(coid, {"status": "uncertain", "error": f"order_send returned None: {err}"})
            self.sink.event(f"Order {coid}: outcome unknown ({err}). Not retrying; reconciling.", level="error", kind="order",
                            code="ORDER_UNCERTAIN")
            self.reconcile()
            return
        if result.retcode in RETCODE_DONE:
            self.stats["filled"] += 1
            self.sink.result(coid, {"status": "filled", "broker_order": str(result.order), "broker_deal": str(result.deal),
                                    "fill_price": float(result.price or 0) or None, "retcode": int(result.retcode)})
        else:
            self.stats["rejected"] += 1
            self.sink.result(coid, {"status": "rejected", "retcode": int(result.retcode), "error": str(result.comment)[:300]})
            self.sink.event(f"Order {coid} rejected by broker: {result.retcode} {result.comment}", level="warn", kind="order",
                            code="ORDER_REJECTED")

    # -------------------------------------------------------------- reconciliation
    def load_unresolved(self, items: list[dict]) -> None:
        """Orders a previous run left open. 'approved' with no result = the old process died around order_send:
        mark it uncertain first (outcome unknown), then reconcile like any uncertain order."""
        for it in items:
            if it.get("status") == "approved":
                self.sink.result(it["client_order_id"], {"status": "uncertain", "error": "previous run ended before reporting"})
            # sent_at = now gives the terminal the same 30 s grace as a fresh order (history can lag after a reconnect)
            self.unresolved[it["client_order_id"]] = {**it, "sent_at": time.time()}

    def reconcile(self) -> None:
        """Look for each unresolved order in open positions and deal history (by the id in the comment)."""
        if not self.unresolved:
            return
        symbol = self.state["config"].get("symbol")
        positions = self.mt5.positions_get(symbol=symbol)
        if positions is None:
            return  # terminal not reachable: try again later, keep entries blocked
        found = {coid_from_comment(p.comment): p for p in positions}
        from datetime import datetime, timedelta, timezone
        deals = self.mt5.history_deals_get(datetime.now(timezone.utc) - timedelta(days=3), datetime.now(timezone.utc) + timedelta(days=1)) or ()
        for d in deals:
            c = coid_from_comment(getattr(d, "comment", None))
            if c and c not in found and getattr(d, "entry", 0) == 0:
                found[c] = d
        for coid in list(self.unresolved):
            hit = found.get(coid)
            try:
                if hit is not None:
                    self.sink.result(coid, {"status": "reconciled_filled", "broker_order": str(getattr(hit, "ticket", "")),
                                            "fill_price": float(getattr(hit, "price_open", getattr(hit, "price", 0)) or 0) or None})
                    self.sink.event(f"Order {coid} reconciled: it WAS filled (ticket {getattr(hit, 'ticket', '?')})", level="warn", kind="order")
                    self.unresolved.pop(coid)
                elif time.time() - float(self.unresolved[coid].get("sent_at") or 0) > 30 or "sent_at" not in self.unresolved[coid]:
                    # absent from positions and deals 30 s after sending: it was not executed
                    self.sink.result(coid, {"status": "reconciled_absent"})
                    self.sink.event(f"Order {coid} reconciled: not executed", kind="order")
                    self.unresolved.pop(coid)
            except LeaseLost:
                raise
