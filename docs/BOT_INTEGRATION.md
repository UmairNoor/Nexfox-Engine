# Bot integration contract (v1.0)

A bot is added through code review and a controlled rollout. Nothing is uploaded or loaded dynamically.

## 1. Pieces of a bot

| Piece | Where | Required for |
|---|---|---|
| Strategy code | `bots/<bot_dir>/` | all |
| API adapter (`BotAdapter`) | `apps/api/app/bots/<bot>.py`, registered in `registry.py` | all |
| Worker runner | `apps/worker/nexfox_worker/runners/<bot>.py`, registered in `runners/__init__.py` | `mt5_python` |
| Tests | `apps/worker/tests/`, `apps/api/tests/` | all |

Execution models: `mt5_python` (runs in the Windows worker next to a dedicated MT5 terminal) and `mt5_ea` (runs as an
Expert Advisor in the customer's terminal, talks to `/ea/v1`). Another language/runtime gets a new execution model
and gateway; the platform core (auth, billing, instances, orders, admin) does not change.

## 2. The adapter

```python
@dataclass
class MyBot(BotAdapter):
    code: str = "my_bot"                 # stable identifier, never reused
    name: str = "My Bot"
    description: str = "..."
    execution_model: str = "mt5_python"
    version: str = "1.0.0"               # packaged version; immutable once used
    min_plan: str = "starter"
    capabilities: dict = field(default_factory=lambda: {"start": True, "pause": True, "resume": True, "stop": True,
                                                        "close_all": True, "live_reconfigure": False, "self_test": True})
    manifest_extra: dict = field(default_factory=lambda: {
        "runtime": {...}, "brokers": {...}, "instruments": [...], "timeframes": [...], "permissions": [...],
        "resources": {...}, "risk_controls": [...], "health": {...}, "logging": {...}, "tests": [...],
        "compatibility": {...}, "lifecycle": {...}, "deployment": {"rollout": "...", "rollback": "..."}})

    def fields(self) -> list[Field]:     # the configuration schema customers/admins may edit
        return [Field("risk_percent", "Risk per trade", "number", 1.0, 0.1, 3.0, 0.1, group="Risk"), ...]

    def cross_validate(self, c: dict) -> list[str]:   # rules spanning fields
        return ["Minimum lot cannot exceed maximum lot."] if c["min_lot"] > c["max_lot"] else []
```

`Field` types: `number`, `integer`, `boolean`, `string` (with `pattern`), `enum`. `editable=False` = fixed by the
version; `admin_only=True` = only super-admins change it. Unknown keys are rejected; numbers are range-checked against
the schema **and** platform hard limits (`max_lot`). Validated configs are stored as JSONB with `schema_version`.

The manifest (stored on each `bot_versions` row) is the contract: runtime, broker/account types, instruments,
capabilities, permissions, resources, health checks, lifecycle semantics, logging, tests, risk controls,
compatibility, deployment/rollback notes.

## 3. The worker runner (mt5_python)

A runner class receives the instance spec and must:

* connect with `mt5_backend.connect` (identity, trade mode, symbol) and report failures with the right code
  (`MT5_AUTH`, `MT5_CONNECTION`, `ACCOUNT_MISMATCH`);
* route **every** new order through `ExecutionGuard.before_order/after_order` (never call `order_send` for an entry
  without it); management actions that only reduce risk (SL moves, closes) may go direct;
* honour `desired_state` (paused = no entries), commands (`close_all`, `pause`, `resume`, `kill`) and stop requests;
* report heartbeats, trades (open/modify/close) and events through the sink, and the runtime/strategy fingerprint once;
* never log or persist the MT5 password.

`runners/rehman.py` is the reference implementation. Keep each instance in its own process (the MetaTrader5 package
holds one terminal connection per process).

## 4. Rollout process

1. Implement + tests; CI/locally: `apps/worker` and `apps/api` test suites green.
2. Deploy worker code that supports the new version (worker advertises `{bot: [versions]}` on every sync; the server
   never assigns a version a worker cannot run).
3. Admin console → Bots → **New version** (draft; config schema/defaults validated).
4. **Self-test**: a worker runs the version against the simulated MT5 market; result stored in `worker_jobs`.
5. **Approve** (blocked without a passing self-test for `mt5_python`), then **Set active**: new assignments get it.
6. Move existing instances explicitly (instance must be stopped): Admin → Instances → version. Watch events/diagnostics.
7. **Rollback**: set the previous approved version active; move affected instances back; **Halt** the bad version.
   Rollbacks never close positions or erase history; every step is in the audit log.

## 5. Debugging a misbehaving bot

* Instance page → **Event log** (filter errors), **Orders** (every intent with status and broker result),
  **Runs** (exact bot version, config version + snapshot, Python/MetaTrader5/terminal versions, strategy file hashes),
  **Config history** (diff and restore).
* Structured logs carry `instance_id`, `bot_code`, `tenant_id`, `correlation_id`, `event`, error `code`.
* Reproduce: take the run's `config_snapshot` + bot version and run the self-test/fake backend
  (`apps/worker/nexfox_worker/fake_mt5.py`, seeded scenarios) or the strategy's `backtest.py` against a demo terminal.
* Fix → regression test → new version → self-test → approve → move instances. Platform releases and bot releases are
  independent.
