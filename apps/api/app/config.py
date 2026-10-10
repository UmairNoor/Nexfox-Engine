"""Runtime settings, read from environment variables (see .env.example). `validate_for_production` refuses unsafe prod config."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_APP_SECRET = "dev-only-change-me-32-bytes-minimum!!"
DEV_EA_SECRET = "dev-only-ea-signing-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "NexFox Engine API"
    env: str = "dev"  # dev | test | staging | prod
    service_name: str = "api"
    # PostgreSQL is the production database (postgresql+psycopg://...). SQLite is only for local tests.
    database_url: str = "sqlite:///./dev.db"
    db_pool_size: int = 10
    redis_url: str | None = None  # optional: fan-out websocket events across API replicas

    # Secrets: CHANGE IN PRODUCTION
    app_secret: str = DEV_APP_SECRET
    ea_signing_secret: str = DEV_EA_SECRET
    # Fernet key (base64, 32 bytes) for MT5 passwords at rest. Separate from APP_SECRET so it can be rotated/held apart.
    mt5_credentials_key: str | None = None

    access_token_minutes: int = 15
    refresh_token_days: int = 30
    handshake_ttl_seconds: int = 600          # signed EA config expires after 10 min
    license_grace_hours: int = 72             # past_due grace period
    offline_trade_block_hours: int = 24       # EA never opens trades without a license check in 24 h

    web_url: str = "http://localhost:3000"
    cors_origins: str = "http://localhost:3000"

    # Billing provider: stripe (production), manual (admins grant plans, no online payment), mock (dev/test only)
    billing_provider: str = "mock"
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_price_starter: str | None = None
    stripe_price_pro: str | None = None
    stripe_price_elite: str | None = None
    trial_days: int = 7
    trial_enabled: bool = True
    # What happens to running bots when entitlement lapses (trial expired, suspended, canceled):
    #   block_new_entries  -> bots stop opening trades, keep managing open positions (default, safest)
    #   pause              -> bots are paused (no new trades, positions keep broker SL/TP)
    entitlement_lapse_policy: str = "block_new_entries"

    # Trading safety
    allow_live_trading: bool = False  # platform-wide gate; per-instance owner authorization is ALSO required
    max_lot_hard: float = 5.0         # no order above this volume, whatever a bot config says
    max_orders_per_instance_day: int = 50
    worker_lease_seconds: int = 45
    instance_heartbeat_timeout_seconds: int = 90

    # Alerts / email
    telegram_bot_token: str | None = None
    telegram_bot_username: str = ""  # your bot's @username from BotFather; empty = Telegram linking not configured
    telegram_callback_secret: str | None = None  # shared secret for the Telegram worker calling /alerts/telegram/callback
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "NexFox Engine <no-reply@localhost>"
    smtp_starttls: bool = True

    ea_download_dir: str = "./downloads"  # put the compiled AurumScalp.ex5 here

    # Processes
    auto_migrate: bool = True            # dev/test: run Alembic on startup. prod: run `python -m app.cli migrate` in deploy
    run_scheduler_in_api: bool = True    # prod: false, run `python -m app.scheduler` as its own service
    log_json: bool = False               # prod: true (one JSON object per line for journald / log shipping)
    log_level: str = "INFO"

    # Retention (days)
    retention_heartbeats_days: int = 14
    retention_bot_events_days: int = 90
    retention_ai_tasks_days: int = 180
    retention_notifications_days: int = 90

    # Rate limits (requests per minute per client IP)
    rate_limit_auth_per_min: int = 20
    rate_limit_gateway_per_min: int = 240

    # Dev helpers
    dev_show_email_codes: bool = True  # print verification codes in the API log instead of emailing

    @property
    def is_prod(self) -> bool:
        # fail closed: anything that is not an explicit dev/test value (e.g. "PROD", "live") gets production checks
        return self.env.strip().lower() not in ("dev", "development", "test", "local")

    def validate_for_production(self) -> list[str]:
        """Problems that make this configuration unsafe to serve real users. Empty list = OK."""
        if not self.is_prod:
            return []
        p = []
        if self.app_secret == DEV_APP_SECRET or len(self.app_secret) < 32:
            p.append("APP_SECRET must be a unique random value of 32+ characters")
        if self.ea_signing_secret == DEV_EA_SECRET or len(self.ea_signing_secret) < 32:
            p.append("EA_SIGNING_SECRET must be a unique random value of 32+ characters")
        if self.app_secret == self.ea_signing_secret:
            p.append("APP_SECRET and EA_SIGNING_SECRET must differ (the EA secret ships inside the .ex5)")
        if not self.mt5_credentials_key:
            p.append("MT5_CREDENTIALS_KEY must be set (generate with: python -m app.cli gen-key)")
        else:
            try:
                from cryptography.fernet import Fernet
                Fernet(self.mt5_credentials_key)
            except Exception:
                p.append("MT5_CREDENTIALS_KEY is not a valid Fernet key (generate with: python -m app.cli gen-key)")
        if not self.database_url.startswith("postgresql"):
            p.append("DATABASE_URL must point to PostgreSQL in production")
        if self.billing_provider == "mock":
            p.append("BILLING_PROVIDER=mock grants free subscriptions; use stripe or manual in production")
        if self.billing_provider == "stripe" and not (self.stripe_secret_key and self.stripe_webhook_secret):
            p.append("STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET are required with BILLING_PROVIDER=stripe")
        if self.dev_show_email_codes:
            p.append("DEV_SHOW_EMAIL_CODES must be false in production")
        if "*" in self.cors_origins:
            p.append("CORS_ORIGINS must list explicit origins")
        if self.entitlement_lapse_policy not in ("block_new_entries", "pause"):
            p.append("ENTITLEMENT_LAPSE_POLICY must be block_new_entries or pause")
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()
