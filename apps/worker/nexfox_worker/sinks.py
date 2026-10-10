"""Where a running bot sends events, trades and order approvals.

PlatformSink -> the NexFox Engine API (server-approved order intents, buffered events, durable result journal).
LocalSink    -> in-memory, for isolated self-tests: approves orders locally as simulated, records everything.
"""
import json
import logging
import time
from pathlib import Path

from .api import ApiError, LeaseLost, WorkerApi

log = logging.getLogger("worker.sink")


class LocalSink:
    def __init__(self):
        self.events, self.trades, self.intents, self.results = [], [], [], []

    def event(self, message, level="info", kind="log", code=None, data=None):
        self.events.append({"ts": time.time(), "level": level, "kind": kind, "code": code, "message": message, "data": data or {}})

    def trade(self, ev: dict):
        self.trades.append(ev)

    def authorize(self, body: dict) -> dict:
        self.intents.append(body)
        return {"approved": True, "client_order_id": body["client_order_id"], "dry_run": body.get("dry_run", False)}

    def result(self, client_order_id: str, body: dict):
        self.results.append({"client_order_id": client_order_id, **body})

    def runtime(self, info: dict):
        self.runtime_info = info

    def flush(self):
        return True


class PlatformSink:
    def __init__(self, api: WorkerApi, instance_id: int, lease_epoch: int, run_id: int | None, journal_dir: Path):
        self.api, self.instance_id, self.epoch, self.run_id = api, instance_id, lease_epoch, run_id
        self.events: list[dict] = []
        self.trades: list[dict] = []
        self._runtime: dict | None = None
        self.journal = journal_dir / f"instance-{instance_id}-results.jsonl"
        self.journal.parent.mkdir(parents=True, exist_ok=True)

    # ---- buffered telemetry (best effort, bounded) ----
    def event(self, message, level="info", kind="log", code=None, data=None):
        self.events.append({"ts": time.time(), "level": level, "kind": kind, "code": code, "message": str(message)[:1000], "data": data or {}})
        del self.events[:-1000]

    def trade(self, ev: dict):
        self.trades.append(ev)

    def runtime(self, info: dict):
        self._runtime = info

    def flush(self) -> bool:
        self._replay_journal()
        if not (self.events or self.trades or self._runtime):
            return True
        ev, tr = self.events[:200], self.trades[:200]
        body = {"lease_epoch": self.epoch, "run_id": self.run_id, "events": ev, "trades": tr}
        if self._runtime:
            body["runtime"] = self._runtime
        try:
            self.api.events(self.instance_id, body)
        except LeaseLost:
            raise
        except ApiError as e:
            log.warning("event flush failed (%s); keeping %d events buffered", e, len(self.events))
            return False
        del self.events[:len(ev)]
        del self.trades[:len(tr)]
        self._runtime = None
        return True

    # ---- order approval: must succeed before any order is sent ----
    def authorize(self, body: dict) -> dict:
        try:
            return self.api.intent(self.instance_id, {**body, "lease_epoch": self.epoch, "run_id": self.run_id})
        except LeaseLost:
            raise
        except ApiError as e:
            return {"approved": False, "reason": e.detail, "code": e.code}

    def result(self, client_order_id: str, body: dict):
        """Order outcomes must reach the server: if the API is down they go to a local journal and are replayed."""
        payload = {**body, "lease_epoch": self.epoch}
        try:
            self.api.intent_result(self.instance_id, client_order_id, payload)
        except LeaseLost:
            raise
        except ApiError as e:
            if e.status in (409, 404) and e.status != 0:
                log.error("order result for %s refused: %s", client_order_id, e)
                return
            with self.journal.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"client_order_id": client_order_id, "body": body}) + "\n")

    def _replay_journal(self):
        if not self.journal.exists():
            return
        lines = [ln for ln in self.journal.read_text(encoding="utf-8").splitlines() if ln.strip()]
        self.journal.unlink()
        for i, ln in enumerate(lines):
            item = json.loads(ln)
            try:
                self.result(item["client_order_id"], item["body"])  # re-journals itself if the API is still down
            except LeaseLost:
                with self.journal.open("a", encoding="utf-8") as f:  # keep what was not replayed for the next holder
                    f.write("".join(x + "\n" for x in lines[i:]))
                raise
