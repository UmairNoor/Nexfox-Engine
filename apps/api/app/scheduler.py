"""Periodic jobs. Production: run as its own service (`python -m app.scheduler`, deploy/linux/systemd/nexfox-scheduler.service).
Dev: also runs inside the API process when RUN_SCHEDULER_IN_API=true. A DB leader lease guarantees one active runner.
"""
import asyncio
import logging
import os
import signal
import socket
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from .config import get_settings
from .db import SessionLocal
from .models import (AiTask, BotEvent, BotInstance, BrokerAccount, Heartbeat, Notification, Subscription, WorkerJob)
from .observability import log_event, setup_logging
from .services import locks, notify
from .services.billing import aware, enforce_grace, expire_trials
from .services.bus import bus
from .services.entitlements import entitlement
from .services.instances import event, lifecycle
from .services.trading_guard import expire_stale_intents

settings = get_settings()
log = logging.getLogger("scheduler")
HOLDER = f"{socket.gethostname()}:{os.getpid()}"
TICK_SECONDS = 20


def now() -> datetime:
    return datetime.now(timezone.utc)


def ea_watchdog(db) -> int:
    """Marks silent EAs offline (and alerts)."""
    cut, n = now() - timedelta(seconds=60), 0
    for a in db.scalars(select(BrokerAccount).where(BrokerAccount.status.in_(["running", "paused"]))):
        hb = aware(a.last_heartbeat)
        if hb and hb < cut:
            a.status = "offline"
            notify.send(db, a.tenant_id, "disconnected", f"EA on {a.login} has been silent for over 60 s. No new trades until it reconnects.")
            bus.publish(a.tenant_id, "offline", {"account_id": a.id})
            n += 1
    db.commit()
    return n


def instance_watchdog(db) -> int:
    """Worker-run instances whose worker stopped reporting: lease expires -> 'offline'; another worker may claim it."""
    n = 0
    cut = now() - timedelta(seconds=settings.instance_heartbeat_timeout_seconds)
    for inst in db.scalars(select(BotInstance).where(BotInstance.archived_at.is_(None), BotInstance.worker_id.is_not(None))):
        exp = aware(inst.lease_expires_at)
        if exp and exp < now() and inst.state not in ("offline", "stopped"):
            inst.state, inst.worker_id = ("stopped" if inst.desired_state == "stopped" else "offline"), None
            if inst.state == "offline":
                event(db, inst, "Worker stopped reporting (lease expired). Waiting for a healthy worker to take over.",
                      level="error", kind="lifecycle", code="WORKER_CRASH")
                notify.send(db, inst.tenant_id, "disconnected", f"{inst.bot.name} on {inst.account.login} lost its worker. It restarts automatically when a worker is available.")
            n += 1
    for inst in db.scalars(select(BotInstance).where(BotInstance.archived_at.is_(None), BotInstance.state.in_(("running", "paused")))):
        hb = aware(inst.last_heartbeat)
        if inst.bot.execution_model == "mt5_ea" and hb and hb < cut:
            inst.state = "offline"
            n += 1
    db.commit()
    return n


def trials_and_lapse(db) -> None:
    for tid in expire_trials(db):
        notify.send(db, tid, "billing", "Your free trial has ended. Bots stop opening new trades until you choose a plan.")
    # trial ending soon: one notice per tenant at <= 2 days left
    for sub in db.scalars(select(Subscription).where(Subscription.status == "trialing")):
        end = aware(sub.trial_end)
        if end and timedelta(0) < end - now() <= timedelta(days=2):
            sent = db.scalar(select(Notification).where(Notification.tenant_id == sub.tenant_id, Notification.event == "billing",
                                                        Notification.text.like("Your trial ends%")))
            if not sent:
                notify.send(db, sub.tenant_id, "billing", f"Your trial ends on {end:%Y-%m-%d %H:%M} UTC. Choose a plan to keep your bots trading.")
    db.commit()
    if settings.entitlement_lapse_policy == "pause":
        for inst in db.scalars(select(BotInstance).where(BotInstance.archived_at.is_(None), BotInstance.desired_state == "running")):
            if not entitlement(db, inst.tenant_id)["valid"]:
                lifecycle(db, None, inst, "pause", reason="subscription not active")
        db.commit()
    # block_new_entries: enforced at order time (EA license check, worker intent authorization) — nothing to do here.


def stale_jobs(db) -> int:
    n = 0
    for j in db.scalars(select(WorkerJob).where(WorkerJob.status == "running", WorkerJob.started_at < now() - timedelta(minutes=10))):
        j.status, j.result, j.finished_at = "failed", {"error": "worker did not report a result within 10 minutes"}, now()
        if j.kind == "verify_account" and j.account_id:
            a = db.get(BrokerAccount, j.account_id)
            if a and a.connection_status == "checking":
                a.connection_status = "unverified"
        n += 1
    db.commit()
    return n


def retention(db) -> dict:
    out = {}
    for model, col, days in ((Heartbeat, Heartbeat.ts, settings.retention_heartbeats_days),
                             (BotEvent, BotEvent.ts, settings.retention_bot_events_days),
                             (AiTask, AiTask.created_at, settings.retention_ai_tasks_days),
                             (Notification, Notification.created_at, settings.retention_notifications_days)):
        res = db.execute(delete(model).where(col < now() - timedelta(days=days)))
        out[model.__tablename__] = res.rowcount
    db.commit()
    return out


_last_retention = {"at": None}


def run_once() -> bool:
    """One scheduler tick. Returns False when another process holds the leader lease."""
    with SessionLocal() as db:
        if not locks.acquire(db, "scheduler", HOLDER, ttl_seconds=TICK_SECONDS * 3):
            return False
        for job in (ea_watchdog, instance_watchdog, expire_stale_intents, trials_and_lapse, enforce_grace, stale_jobs):
            try:
                job(db)
            except Exception:
                db.rollback()
                log.exception("scheduler job %s failed", job.__name__)
        if _last_retention["at"] is None or now() - _last_retention["at"] > timedelta(hours=1):
            try:
                log_event(log, logging.INFO, "scheduler.retention", "retention purge", deleted=retention(db))
            except Exception:
                db.rollback()
                log.exception("retention failed")
            _last_retention["at"] = now()
    return True


async def loop(stop: asyncio.Event | None = None) -> None:
    while not (stop and stop.is_set()):
        try:
            await asyncio.to_thread(run_once)
        except Exception:
            log.exception("scheduler tick failed")
        try:
            await asyncio.wait_for(stop.wait(), TICK_SECONDS) if stop else await asyncio.sleep(TICK_SECONDS)
        except asyncio.TimeoutError:
            pass


def main() -> None:
    setup_logging("scheduler")
    from .db import current_revision, head_revision
    if current_revision() != head_revision():
        raise SystemExit("Database is not at the latest migration. Run: python -m app.cli migrate")
    stop = asyncio.Event()

    async def runner():
        loop_ = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop_.add_signal_handler(sig, stop.set)
            except NotImplementedError:  # Windows
                signal.signal(sig, lambda *_: stop.set())
        log.info("scheduler started as %s", HOLDER)
        await loop(stop)
        with SessionLocal() as db:
            locks.release(db, "scheduler", HOLDER)
        log.info("scheduler stopped")

    asyncio.run(runner())


if __name__ == "__main__":
    main()
