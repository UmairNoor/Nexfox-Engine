import sys
from pathlib import Path

WORKER = Path(__file__).resolve().parents[1]
REPO = WORKER.parents[1]
sys.path.insert(0, str(WORKER))
BOTS = REPO / "bots"
DEMO = {"7100001": {"password": "demo-pass", "server": "Fake-Demo", "trade_mode": "demo", "balance": 10000},
        "7100002": {"password": "real-pass", "server": "Fake-Live", "trade_mode": "real", "balance": 10000}}
