"""Worker settings from environment variables (see apps/worker/.env.example)."""
import json
import os
import socket
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"'))


@dataclass
class WorkerConfig:
    api_url: str
    token: str
    name: str
    terminals: list[str]
    data_dir: Path
    bots_dir: Path
    mt5_backend: str = "real"            # real | fake (fake = simulated MT5 for tests / self-tests, never trades)
    fake_config: dict = field(default_factory=dict)
    sync_interval: float = 5.0
    heartbeat_interval: float = 5.0
    request_timeout: float = 10.0
    log_json: bool = True
    fast: bool = False                   # tests only: no sleeping between scans (simulated backend)

    @property
    def slots(self) -> int:
        return len(self.terminals)


def load(env_file: str | None = None) -> WorkerConfig:
    _load_env_file(Path(env_file) if env_file else Path(__file__).resolve().parents[1] / ".env")
    terminals = [t.strip() for t in os.environ.get("MT5_TERMINALS", "").split(";") if t.strip()]
    backend = os.environ.get("MT5_BACKEND", "real")
    if backend == "fake" and not terminals:
        terminals = [f"fake-terminal-{i}" for i in range(int(os.environ.get("FAKE_SLOTS", "2")))]
    fake = {}
    if os.environ.get("FAKE_MT5_CONFIG"):
        fake = json.loads(Path(os.environ["FAKE_MT5_CONFIG"]).read_text(encoding="utf-8-sig"))  # tolerates a BOM (Notepad / PowerShell)
    cfg = WorkerConfig(
        api_url=os.environ.get("NEXFOX_API_URL", "http://127.0.0.1:8000").rstrip("/"),
        token=os.environ.get("WORKER_TOKEN", ""),
        name=os.environ.get("WORKER_NAME", socket.gethostname()),
        terminals=terminals,
        data_dir=Path(os.environ.get("WORKER_DATA_DIR", str(Path(__file__).resolve().parents[1] / "data"))),
        bots_dir=Path(os.environ.get("BOTS_DIR", str(REPO_ROOT / "bots"))),
        mt5_backend=backend, fake_config=fake,
        sync_interval=float(os.environ.get("SYNC_INTERVAL", "5")),
        heartbeat_interval=float(os.environ.get("HEARTBEAT_INTERVAL", "5")),
        log_json=os.environ.get("LOG_JSON", "true").lower() == "true",
        fast=os.environ.get("WORKER_FAST", "false").lower() == "true" and backend == "fake",
    )
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    return cfg
