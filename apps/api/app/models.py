"""SQLAlchemy models. Every customer-owned row carries tenant_id (Postgres RLS policies in infra/sql)."""
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer, Numeric, String, Text,
                        UniqueConstraint, Index, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# JSON documents: JSONB on PostgreSQL (indexable, validated before storage), plain JSON on SQLite (tests).
JSONType = JSON().with_variant(JSONB(), "postgresql")
Money = Numeric(14, 2, asdecimal=False)
Price = Numeric(12, 3, asdecimal=False)


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    country: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="active")
    provider_customer_id: Mapped[str | None] = mapped_column(String(120))
    trial_consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # one free trial per workspace
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    users: Mapped[list["User"]] = relationship(back_populates="tenant")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="owner")  # owner | member | viewer | admin | support
    status: Mapped[str] = mapped_column(String(20), default="active", server_default="active")  # active | suspended
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # bump to revoke every token
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    totp_secret_enc: Mapped[str | None] = mapped_column(Text)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    risk_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    tenant: Mapped[Tenant] = relationship(back_populates="users")


class EmailCode(Base):
    __tablename__ = "email_codes"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(30))  # verify | reset
    code_hash: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # wrong guesses; code dies at 5


class Plan(Base):
    __tablename__ = "plans"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)  # starter | pro | elite
    name: Mapped[str] = mapped_column(String(60))
    price_cents: Mapped[int] = mapped_column(Integer)
    interval: Mapped[str] = mapped_column(String(10), default="month")
    max_accounts: Mapped[int] = mapped_column(Integer)
    hosted: Mapped[bool] = mapped_column(Boolean, default=False)
    features: Mapped[dict] = mapped_column(JSONType, default=dict)


class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"))
    status: Mapped[str] = mapped_column(String(20))  # trialing | active | past_due | suspended | canceled
    provider_sub_id: Mapped[str | None] = mapped_column(String(120), index=True)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trial_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    past_due_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    plan: Mapped[Plan] = relationship()


class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    provider_invoice_id: Mapped[str] = mapped_column(String(120), unique=True)
    number: Mapped[str] = mapped_column(String(60))
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8), default="usd")
    status: Mapped[str] = mapped_column(String(20))
    pdf_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BillingEvent(Base):
    __tablename__ = "billing_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider_event_id: Mapped[str] = mapped_column(String(120), unique=True)  # makes webhooks idempotent
    type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSONType)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class License(Base):
    __tablename__ = "licenses"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id"))
    key_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    key_enc: Mapped[str] = mapped_column(Text)  # encrypted so the owner can view it again
    key_prefix: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | suspended | revoked
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    subscription: Mapped[Subscription] = relationship()


class BrokerAccount(Base):
    """An MT5 account bound to a license (self-hosted) or hosted in our farm."""
    __tablename__ = "broker_accounts"
    __table_args__ = (UniqueConstraint("server", "login", name="uq_account_server_login"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    license_id: Mapped[int] = mapped_column(ForeignKey("licenses.id"))
    login: Mapped[str] = mapped_column(String(20))
    server: Mapped[str] = mapped_column(String(120))
    account_type: Mapped[str] = mapped_column(String(10), default="demo")  # demo | live
    mode: Mapped[str] = mapped_column(String(10), default="self")  # self | hosted
    status: Mapped[str] = mapped_column(String(20), default="waiting")  # waiting | running | paused | stopped | offline
    symbol: Mapped[str] = mapped_column(String(30), default="XAUUSD")
    ea_version: Mapped[str | None] = mapped_column(String(20))
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    balance: Mapped[float | None] = mapped_column(Money)
    equity: Mapped[float | None] = mapped_column(Money)
    peak_equity: Mapped[float | None] = mapped_column(Money)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_handshake: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_ip: Mapped[str | None] = mapped_column(String(64))
    vault_ref: Mapped[str | None] = mapped_column(String(200))  # hosted only: pointer to encrypted password
    # Hosted (worker-run) accounts: MT5 password encrypted with MT5_CREDENTIALS_KEY. Never returned by any API.
    credential_enc: Mapped[str | None] = mapped_column(Text)
    credential_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Connection health as last verified by a worker against the real terminal (not by the existence of this row).
    connection_status: Mapped[str] = mapped_column(String(30), default="unverified", server_default="unverified")
    connection_error: Mapped[str | None] = mapped_column(String(300))
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reported_trade_mode: Mapped[str | None] = mapped_column(String(10))  # demo | contest | real (from the terminal)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    config: Mapped["AccountConfig"] = relationship(back_populates="account", uselist=False, cascade="all, delete-orphan")


class Preset(Base):
    __tablename__ = "presets"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(60))
    version: Mapped[int] = mapped_column(Integer, default=1)
    description: Mapped[str] = mapped_column(String(200), default="")
    params: Mapped[dict] = mapped_column(JSONType)
    min_plan: Mapped[str] = mapped_column(String(20), default="starter")


class AccountConfig(Base):
    __tablename__ = "account_configs"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("broker_accounts.id"), unique=True)
    preset_id: Mapped[int] = mapped_column(ForeignKey("presets.id"))
    overrides: Mapped[dict] = mapped_column(JSONType, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)          # bumps on every change
    applied_version: Mapped[int] = mapped_column(Integer, default=0)  # reported back by the EA
    trading_enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # pause/resume
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    account: Mapped[BrokerAccount] = relationship(back_populates="config")
    preset: Mapped[Preset] = relationship()


class Command(Base):
    __tablename__ = "commands"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("broker_accounts.id"), index=True)
    instance_id: Mapped[int | None] = mapped_column(ForeignKey("bot_instances.id"), index=True)
    type: Mapped[str] = mapped_column(String(20))  # pause | resume | close_all | kill
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | sent | acked
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (UniqueConstraint("account_id", "ticket", name="uq_position_ticket"),
                      Index("ix_positions_tenant_closed", "tenant_id", "closed_at"))
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    account_id: Mapped[int] = mapped_column(ForeignKey("broker_accounts.id"), index=True)
    instance_id: Mapped[int | None] = mapped_column(ForeignKey("bot_instances.id"), index=True)
    client_order_id: Mapped[str | None] = mapped_column(String(40))
    ticket: Mapped[str] = mapped_column(String(40))
    symbol: Mapped[str] = mapped_column(String(30), default="XAUUSD")
    side: Mapped[str] = mapped_column(String(4))  # BUY | SELL
    volume: Mapped[float] = mapped_column(Float)
    open_price: Mapped[float] = mapped_column(Price)
    requested_price: Mapped[float | None] = mapped_column(Price)
    sl: Mapped[float | None] = mapped_column(Price)
    tp: Mapped[float | None] = mapped_column(Price)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_price: Mapped[float | None] = mapped_column(Price)
    profit: Mapped[float | None] = mapped_column(Money)
    commission: Mapped[float] = mapped_column(Money, default=0)
    swap: Mapped[float] = mapped_column(Money, default=0)
    slippage_pts: Mapped[float | None] = mapped_column(Float)
    spread_at_entry: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    module: Mapped[str | None] = mapped_column(String(40))
    exit_reason: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(10), default="open")  # open | closed


class Heartbeat(Base):
    """Time-series (Timescale hypertable in Postgres)."""
    __tablename__ = "ea_heartbeats"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("broker_accounts.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    balance: Mapped[float | None] = mapped_column(Money)
    equity: Mapped[float | None] = mapped_column(Money)
    bid: Mapped[float | None] = mapped_column(Price)
    ask: Mapped[float | None] = mapped_column(Price)
    spread: Mapped[int | None] = mapped_column(Integer)
    ping_ms: Mapped[int | None] = mapped_column(Integer)
    open_positions: Mapped[int | None] = mapped_column(Integer)
    state: Mapped[str | None] = mapped_column(String(20))


class Candle(Base):
    """M1 candles built from EA heartbeats (hypertable in Postgres)."""
    __tablename__ = "candles"
    __table_args__ = (UniqueConstraint("symbol", "timeframe", "ts", name="uq_candle"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(30), default="XAUUSD")
    timeframe: Mapped[str] = mapped_column(String(5), default="M1")
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    o: Mapped[float] = mapped_column(Price)
    h: Mapped[float] = mapped_column(Price)
    l: Mapped[float] = mapped_column(Price)
    c: Mapped[float] = mapped_column(Price)


class AlertPref(Base):
    __tablename__ = "alert_prefs"
    __table_args__ = (UniqueConstraint("tenant_id", "event", name="uq_alert_pref"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    event: Mapped[str] = mapped_column(String(40))
    telegram: Mapped[bool] = mapped_column(Boolean, default=True)
    email: Mapped[bool] = mapped_column(Boolean, default=True)
    push: Mapped[bool] = mapped_column(Boolean, default=False)


class TelegramLink(Base):
    __tablename__ = "telegram_links"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), unique=True)
    link_token: Mapped[str] = mapped_column(String(64), unique=True)
    chat_id: Mapped[str | None] = mapped_column(String(64))
    username: Mapped[str | None] = mapped_column(String(64))


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    channel: Mapped[str] = mapped_column(String(20))
    event: Mapped[str] = mapped_column(String(40))
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(80))
    detail: Mapped[dict] = mapped_column(JSONType, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuthSession(Base):
    """One row per refresh token. Refresh rotates the row; logout / password reset / suspension revoke it."""
    __tablename__ = "auth_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(200))


# ---------------------------------------------------------------- multi-bot platform
class Bot(Base):
    """Bot registry. `enabled` only controls NEW assignments/starts; it never touches running instances or positions."""
    __tablename__ = "bots"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)  # stable identifier, e.g. rehman_liquidity
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text, default="")
    execution_model: Mapped[str] = mapped_column(String(20))  # mt5_ea | mt5_python
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    min_plan: Mapped[str] = mapped_column(String(20), default="starter")
    active_version_id: Mapped[int | None] = mapped_column(ForeignKey("bot_versions.id", use_alter=True, name="fk_bots_active_version"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BotVersion(Base):
    __tablename__ = "bot_versions"
    __table_args__ = (UniqueConstraint("bot_id", "version", name="uq_bot_version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id"), index=True)
    version: Mapped[str] = mapped_column(String(20))
    config_schema: Mapped[dict] = mapped_column(JSONType, default=dict)
    default_config: Mapped[dict] = mapped_column(JSONType, default=dict)
    # Integration contract (see docs/BOT_INTEGRATION.md): runtime, account types, instruments, capabilities, risk controls...
    manifest: Mapped[dict] = mapped_column(JSONType, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")
    # draft -> approved -> (halted | retired). Only approved versions can be assigned or rolled out.
    status: Mapped[str] = mapped_column(String(20), default="approved")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BotInstance(Base):
    """A bot assigned to one MT5 account. desired_state is what people asked for; state is what the runtime reports."""
    __tablename__ = "bot_instances"
    __table_args__ = (Index("uq_instance_account_active", "account_id", unique=True,
                            sqlite_where=text("archived_at IS NULL"), postgresql_where=text("archived_at IS NULL")),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("broker_accounts.id"), index=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id"), index=True)
    bot_version_id: Mapped[int] = mapped_column(ForeignKey("bot_versions.id"))
    config: Mapped[dict] = mapped_column(JSONType, default=dict)
    config_version: Mapped[int] = mapped_column(Integer, default=1)
    applied_config_version: Mapped[int] = mapped_column(Integer, default=0)
    desired_state: Mapped[str] = mapped_column(String(20), default="stopped")  # running | paused | stopped
    state: Mapped[str] = mapped_column(String(20), default="stopped")  # stopped|pending|starting|running|paused|stopping|failed|offline
    live_authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    live_authorized_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    worker_id: Mapped[int | None] = mapped_column(ForeignKey("workers.id"), index=True)
    lease_epoch: Mapped[int] = mapped_column(Integer, default=0)  # fencing token: bumps on every claim
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bot: Mapped[Bot] = relationship()
    bot_version: Mapped[BotVersion] = relationship()
    account: Mapped[BrokerAccount] = relationship()


class BotConfigHistory(Base):
    __tablename__ = "bot_config_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("bot_instances.id"), index=True)
    config_version: Mapped[int] = mapped_column(Integer)
    bot_version_id: Mapped[int] = mapped_column(ForeignKey("bot_versions.id"))
    config: Mapped[dict] = mapped_column(JSONType)
    changed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BotRun(Base):
    """One execution session of an instance on a worker: exact bot version, config snapshot and runtime versions.
    Every event and order intent points at its run, so a failure can be reproduced with the same inputs."""
    __tablename__ = "bot_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("bot_instances.id"), index=True)
    worker_id: Mapped[int | None] = mapped_column(ForeignKey("workers.id"))
    lease_epoch: Mapped[int] = mapped_column(Integer)
    bot_version_id: Mapped[int] = mapped_column(ForeignKey("bot_versions.id"))
    config_version: Mapped[int] = mapped_column(Integer)
    config_snapshot: Mapped[dict] = mapped_column(JSONType, default=dict)
    runtime: Mapped[dict] = mapped_column(JSONType, default=dict)  # python, MetaTrader5 pkg, terminal build, worker version
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_reason: Mapped[str | None] = mapped_column(String(200))


class BotEvent(Base):
    __tablename__ = "bot_events"
    __table_args__ = (Index("ix_bot_events_instance_ts", "instance_id", "ts"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("bot_instances.id"))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("bot_runs.id"), index=True)
    code: Mapped[str | None] = mapped_column(String(40))  # error category, e.g. MT5_CONNECTION, ORDER_REJECTED
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    level: Mapped[str] = mapped_column(String(10), default="info")  # info | warn | error
    kind: Mapped[str] = mapped_column(String(20), default="log")  # lifecycle | signal | order | risk | connection | log
    message: Mapped[str] = mapped_column(String(1000))
    data: Mapped[dict] = mapped_column(JSONType, default=dict)


class Worker(Base):
    """A Windows host running MT5 terminals. Authenticates with a token whose hash is stored here."""
    __tablename__ = "workers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | disabled
    host: Mapped[str | None] = mapped_column(String(120))
    version: Mapped[str | None] = mapped_column(String(40))
    capabilities: Mapped[dict] = mapped_column(JSONType, default=dict)  # {"bots": {"rehman_liquidity": ["1.0.0"]}, "slots": 4}
    max_instances: Mapped[int] = mapped_column(Integer, default=4)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkerJob(Base):
    """One-shot work for a worker: verify_account (real terminal login check) or self_test (bot vs simulated market)."""
    __tablename__ = "worker_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # verify_account | self_test
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), index=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("broker_accounts.id"))
    bot_id: Mapped[int | None] = mapped_column(ForeignKey("bots.id"))
    bot_version_id: Mapped[int | None] = mapped_column(ForeignKey("bot_versions.id"))
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued | running | passed | failed
    worker_id: Mapped[int | None] = mapped_column(ForeignKey("workers.id"))
    result: Mapped[dict] = mapped_column(JSONType, default=dict)
    requested_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OrderIntent(Base):
    """Server-approved order request. No worker sends an order without one; an 'uncertain' one blocks new entries."""
    __tablename__ = "order_intents"
    __table_args__ = (Index("ix_order_intents_instance_status", "instance_id", "status"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("bot_instances.id"))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("bot_runs.id"))
    client_order_id: Mapped[str] = mapped_column(String(40), unique=True)
    lease_epoch: Mapped[int] = mapped_column(Integer)
    symbol: Mapped[str] = mapped_column(String(30))
    side: Mapped[str] = mapped_column(String(4))  # BUY | SELL
    volume: Mapped[float] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Price)
    sl: Mapped[float | None] = mapped_column(Price)
    tp: Mapped[float | None] = mapped_column(Price)
    comment: Mapped[str | None] = mapped_column(String(40))
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    # approved -> filled | rejected | uncertain -> reconciled_filled | reconciled_absent ; expired if never reported
    status: Mapped[str] = mapped_column(String(20), default="approved")
    broker_order: Mapped[str | None] = mapped_column(String(40))
    broker_deal: Mapped[str | None] = mapped_column(String(40))
    fill_price: Mapped[float | None] = mapped_column(Price)
    retcode: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AiAgent(Base):
    """AI agent registry. Registration grants nothing: an agent only gets the scopes listed on its approved version,
    and the trading/secret scopes are refused by the runtime (see services/agents.py)."""
    __tablename__ = "ai_agents"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    active_version_id: Mapped[int | None] = mapped_column(ForeignKey("ai_agent_versions.id", use_alter=True, name="fk_ai_agents_active_version"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AiAgentVersion(Base):
    __tablename__ = "ai_agent_versions"
    __table_args__ = (UniqueConstraint("agent_id", "version", name="uq_ai_agent_version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("ai_agents.id"), index=True)
    version: Mapped[str] = mapped_column(String(20))
    manifest: Mapped[dict] = mapped_column(JSONType, default=dict)  # inputs, outputs, scopes, limits, provider
    config: Mapped[dict] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="approved")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AiTask(Base):
    """One agent execution: traceable input, bounded output, and an explicit 'generated' label on the result."""
    __tablename__ = "ai_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), index=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("ai_agents.id"), index=True)
    agent_version_id: Mapped[int] = mapped_column(ForeignKey("ai_agent_versions.id"))
    requested_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    subject_type: Mapped[str | None] = mapped_column(String(30))  # e.g. bot_instance
    subject_id: Mapped[int | None] = mapped_column(Integer)
    input: Mapped[dict] = mapped_column(JSONType, default=dict)
    output: Mapped[dict] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued | running | succeeded | failed
    error_code: Mapped[str | None] = mapped_column(String(40))
    error: Mapped[str | None] = mapped_column(String(300))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlatformSetting(Base):
    __tablename__ = "platform_settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONType, default=dict)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SchedulerLock(Base):
    """Leader lease so only one scheduler process runs periodic jobs, however many API/scheduler replicas exist."""
    __tablename__ = "scheduler_locks"
    name: Mapped[str] = mapped_column(String(60), primary_key=True)
    holder: Mapped[str] = mapped_column(String(120))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NewsEvent(Base):
    """High-impact economic calendar entries pushed to the EA as blackout windows."""
    __tablename__ = "news_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    impact: Mapped[str] = mapped_column(String(10), default="high")
    title: Mapped[str] = mapped_column(String(200))
