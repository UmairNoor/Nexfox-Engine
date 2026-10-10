"""Worker supervisor: syncs with the platform and runs one OS process per bot instance.

Loop (every SYNC_INTERVAL seconds):
  1. collect status from child processes (state, heartbeat, connection, acked commands, crashes)
  2. POST /worker/v1/sync -> renewals, stop list, newly claimed instances (with lease_epoch fencing tokens), jobs
  3. stop children the server no longer wants us to run (lease lost / stop requested)
  4. start children for new instances, each on its own terminal from the account-to-terminal pool
  5. restart a child whose config/version changed (the strategy reads config at start-up)
Graceful shutdown (Ctrl+C / service stop): children are asked to stop, positions are left with their broker SL/TP.
"""
import logging
import multiprocessing as mp
import platform
import signal
import threading
import time
from pathlib import Path

from . import __version__
from .api import ApiError, WorkerApi
from .config import WorkerConfig
from .runners import RUNNERS, capabilities
from .terminals import TerminalPool

log = logging.getLogger("worker.supervisor")
ctx = mp.get_context("spawn")


def _child_main(spec: dict, terminal: str, cfg: dict, status_q, control_q) -> None:
    """Entry point of an instance process."""
    from .logging_setup import setup
    from .sinks import PlatformSink
    setup(cfg["log_json"], f"instance-{spec['instance_id']}")
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)  # the supervisor coordinates shutdown via the control queue
    except (ValueError, OSError):
        pass
    api = WorkerApi(cfg["api_url"], cfg["token"], cfg["request_timeout"])
    sink = PlatformSink(api, spec["instance_id"], spec["lease_epoch"], spec.get("run_id"), Path(cfg["data_dir"]) / "journal")
    runner = RUNNERS[spec["bot_code"]]["runner"](spec, terminal, sink, Path(cfg["bots_dir"]), Path(cfg["data_dir"]) / f"instance-{spec['instance_id']}",
                                                 cfg["mt5_backend"], cfg["fake_config"], control=control_q, status=status_q,
                                                 heartbeat_interval=cfg["heartbeat_interval"], fast=cfg.get("fast", False))
    runner.run()


def _job_main(job: dict, terminal: str | None, cfg: dict, out_q) -> None:
    from . import jobs
    from .logging_setup import setup
    setup(cfg["log_json"], f"job-{job['id']}")
    try:
        if job["kind"] == "verify_account":
            res = jobs.verify_account(job["payload"], terminal, cfg["mt5_backend"], cfg["fake_config"])
        else:
            res = jobs.self_test(job["payload"], Path(cfg["bots_dir"]), cfg.get("self_test_fake"))
    except Exception as e:
        res = {"status": "failed", "result": {"error": f"{type(e).__name__}: {str(e)[:300]}"}}
    out_q.put({"job_id": job["id"], **res})


class Child:
    def __init__(self, spec: dict, terminal: str, proc, control):
        self.spec, self.terminal, self.proc, self.control = spec, terminal, proc, control
        self.last: dict = {"instance_id": spec["instance_id"], "lease_epoch": spec["lease_epoch"], "run_id": spec.get("run_id"),
                           "state": "starting"}
        self.acked: list[int] = []
        self.stopping_since: float | None = None


class Supervisor:
    def __init__(self, cfg: WorkerConfig, api: WorkerApi | None = None):
        self.cfg = cfg
        self.api = api or WorkerApi(cfg.api_url, cfg.token, cfg.request_timeout)
        self.pool = TerminalPool(cfg.terminals, cfg.data_dir / "terminals.json")
        self.children: dict[int, Child] = {}
        self.status_q = ctx.Queue()
        self.jobs_q = ctx.Queue()
        self.job_procs: dict[int, tuple] = {}
        self.stop_event = threading.Event()
        self.lease_seconds = 45.0
        self.last_sync_ok = time.time()
        self.fenced = False
        self.child_cfg = {"api_url": cfg.api_url, "token": cfg.token, "request_timeout": cfg.request_timeout, "bots_dir": str(cfg.bots_dir),
                          "data_dir": str(cfg.data_dir), "mt5_backend": cfg.mt5_backend, "fake_config": cfg.fake_config,
                          "heartbeat_interval": cfg.heartbeat_interval, "log_json": cfg.log_json, "fast": cfg.fast}

    # ------------------------------------------------------------------ status
    def _drain_status(self) -> None:
        while True:
            try:
                msg = self.status_q.get_nowait()
            except Exception:
                break
            ch = self.children.get(msg["instance_id"])
            if ch and msg.get("lease_epoch") == ch.spec["lease_epoch"]:
                ch.acked += msg.pop("acked_commands", []) or []
                ch.last = {**ch.last, **{k: v for k, v in msg.items() if v is not None or k in ("error", "error_code")}}
        for iid, ch in list(self.children.items()):
            if not ch.proc.is_alive():
                ch.proc.join(timeout=1)
                if ch.last.get("state") not in ("stopped", "failed"):
                    ch.last.update(state="failed", error=f"process exited unexpectedly (code {ch.proc.exitcode})", error_code="WORKER_CRASH")
                ch.last["_exited"] = True

    def _reports(self) -> list[dict]:
        out = []
        for ch in self.children.values():
            r = {k: ch.last.get(k) for k in ("instance_id", "lease_epoch", "run_id", "state", "error", "error_code",
                                             "heartbeat", "connection", "applied_config_version")}
            r["id"] = r.pop("instance_id")
            r["acked_commands"] = ch.acked[:]
            out.append({k: v for k, v in r.items() if v is not None})
        return out

    # ------------------------------------------------------------------ process control
    def _start(self, spec: dict) -> None:
        iid = spec["instance_id"]
        if spec["bot_code"] not in RUNNERS:
            return
        terminal = self.pool.acquire(spec["account"]["id"], iid)
        if terminal is None:
            log.warning("no free terminal for instance %s", iid)
            return
        control = ctx.Queue()
        p = ctx.Process(target=_child_main, args=(spec, terminal, self.child_cfg, self.status_q, control), name=f"instance-{iid}", daemon=False)
        p.start()
        self.children[iid] = Child(spec, terminal, p, control)
        log.info("started instance %s (%s %s) lease %s on %s pid %s", iid, spec["bot_code"], spec["bot_version"], spec["lease_epoch"], terminal, p.pid)

    def _request_stop(self, iid: int, reason: str) -> None:
        ch = self.children.get(iid)
        if not ch or ch.stopping_since:
            return
        log.info("stopping instance %s (%s)", iid, reason)
        ch.stopping_since = time.time()
        ch.control.put({"type": "stop"})

    def _reap(self, force_after: float = 30.0) -> list[int]:
        """Remove exited children (after their final status was reported); kill ones that ignore a stop request."""
        gone = []
        for iid, ch in list(self.children.items()):
            if ch.stopping_since and ch.proc.is_alive() and time.time() - ch.stopping_since > force_after:
                log.error("instance %s did not stop in %ss: terminating process", iid, force_after)
                ch.proc.terminate()
                ch.proc.join(timeout=5)
                ch.last.update(state="stopped", error="terminated after stop timeout")
            if not ch.proc.is_alive() and ch.last.get("_reported"):
                self.pool.release(ch.terminal)
                del self.children[iid]
                gone.append(iid)
        return gone

    # ------------------------------------------------------------------ jobs
    def _start_job(self, job: dict) -> None:
        terminal = None
        if job["kind"] == "verify_account":
            terminal = self.pool.acquire(job["payload"]["account"]["id"], -job["id"])
            if terminal is None:
                self.api.job_result(job["id"], {"status": "failed", "result": {"error": "no free terminal on the worker"}})
                return
        p = ctx.Process(target=_job_main, args=(job, terminal, {**self.child_cfg, "self_test_fake": None}, self.jobs_q), name=f"job-{job['id']}")
        p.start()
        self.job_procs[job["id"]] = (p, terminal, time.time())

    def _collect_jobs(self) -> None:
        while True:
            try:
                res = self.jobs_q.get_nowait()
            except Exception:
                break
            jid = res.pop("job_id")
            p, terminal, _ = self.job_procs.pop(jid, (None, None, None))
            if terminal:
                self.pool.release(terminal)
            try:
                self.api.job_result(jid, res)
            except ApiError as e:
                log.error("could not report job %s: %s", jid, e)
        for jid, (p, terminal, started) in list(self.job_procs.items()):
            if not p.is_alive() and time.time() - started > 5:
                self.job_procs.pop(jid)
                if terminal:
                    self.pool.release(terminal)
                try:
                    self.api.job_result(jid, {"status": "failed", "result": {"error": f"job process exited (code {p.exitcode})"}})
                except ApiError:
                    pass

    # ------------------------------------------------------------------ sync
    def tick(self) -> dict | None:
        self._drain_status()
        self._collect_jobs()
        reports = self._reports()
        body = {"version": __version__, "host": f"{platform.node()} ({platform.system()} {platform.release()})", "bots": capabilities(),
                "free_slots": max(0, self.pool.free() - len(self.job_procs)), "instances": reports,
                "accept_jobs": len(self.job_procs) < 2 and not self.stop_event.is_set()}
        if self.stop_event.is_set():
            body["free_slots"] = 0
        sent_at = time.time()
        try:
            resp = self.api.sync(body)
        except ApiError as e:
            self._self_fence(e)
            return None
        self.last_sync_ok, self.fenced = sent_at, False
        self.lease_seconds = float(resp.get("lease_seconds") or self.lease_seconds)
        # children stop trading by themselves at this deadline unless a later sync extends it (see RehmanRunner)
        lease_until = sent_at + self.lease_seconds * 0.8
        for r in reports:
            ch = self.children.get(r["id"])
            if ch:
                ch.acked = [a for a in ch.acked if a not in r.get("acked_commands", [])]
                if ch.last.get("_exited"):
                    ch.last["_reported"] = True
        for s in resp.get("stop", []):
            self._request_stop(s["instance_id"], s.get("reason", "server"))
        for spec in resp.get("instances", []):
            iid = spec["instance_id"]
            ch = self.children.get(iid)
            if spec.get("fresh"):
                if ch:  # we lost and regained the lease: stale process must go first
                    self._request_stop(iid, "re-claimed with a new lease")
                    continue
                self._start({**spec, "lease_until": lease_until})
            elif ch and not ch.stopping_since:
                if spec["config_version"] != ch.spec["config_version"] or spec["bot_version"] != ch.spec["bot_version"]:
                    self._request_stop(iid, f"config v{spec['config_version']} needs a restart")
                else:
                    ch.control.put({"type": "spec", "spec": spec, "lease_until": lease_until})
        for job in resp.get("jobs", []):
            self._start_job(job)
        self._reap()
        return resp

    def _self_fence(self, err: Exception) -> None:
        """Platform unreachable. The server hands our leases to another worker once they expire, so every instance is
        stopped well before that: two processes must never manage the same account. Stop != close: positions keep
        their broker-side SL/TP."""
        silent = time.time() - self.last_sync_ok
        if silent <= self.lease_seconds * 0.5 or not self.children:
            log.warning("sync failed: %s (retrying; instances stop if the platform stays unreachable)", err)
            return
        if not self.fenced:
            log.error("platform unreachable for %.0fs: stopping all %d instance(s) before their leases expire", silent, len(self.children))
            self.fenced = True
        for iid in list(self.children):
            self._request_stop(iid, "lease self-fence: platform unreachable")
        self._reap(force_after=max(3.0, self.lease_seconds * 0.2))

    def run_forever(self) -> None:
        log.info("NexFox worker %s: %d terminal slot(s), backend=%s, bots=%s", __version__, self.pool.free(), self.cfg.mt5_backend, capabilities())
        while not self.stop_event.is_set():
            self.tick()
            self.stop_event.wait(self.cfg.sync_interval)
        self.shutdown()

    def shutdown(self, timeout: float = 30.0) -> None:
        """Stop every instance cleanly; positions stay open with their broker SL/TP, leases are released via a final sync."""
        log.info("shutting down %d instance(s)", len(self.children))
        for iid in list(self.children):
            self._request_stop(iid, "worker shutdown")
        deadline = time.time() + timeout
        while self.children and time.time() < deadline:
            self.stop_event.set()
            self.tick()
            time.sleep(1)
        for ch in self.children.values():
            if ch.proc.is_alive():
                ch.proc.terminate()
        log.info("worker stopped")
