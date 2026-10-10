"""python -m nexfox_worker [--env FILE] [--check]

--check  validates configuration and connectivity to the API (one sync with zero free slots), then exits.
"""
import argparse
import signal
import sys

from .api import ApiError
from .config import load
from .logging_setup import setup
from .supervisor import Supervisor


def main() -> int:
    ap = argparse.ArgumentParser(prog="nexfox_worker")
    ap.add_argument("--env", help="path to the worker .env file")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    cfg = load(a.env)
    setup(cfg.log_json, "supervisor")
    if not cfg.token:
        print("WORKER_TOKEN is not set. Register the worker in the admin console (Workers) or with `python -m app.cli create-worker`.")
        return 2
    if not cfg.terminals:
        print("MT5_TERMINALS is empty. List one terminal64.exe per slot, separated by ';' (see README).")
        return 2
    sup = Supervisor(cfg)
    if a.check:
        try:
            sup.api.sync({"version": "check", "bots": {}, "free_slots": 0, "instances": [], "accept_jobs": False})
        except ApiError as e:
            print("API check failed:", e)
            return 1
        print(f"OK: API reachable at {cfg.api_url}, token accepted, {cfg.slots} terminal slot(s), backend {cfg.mt5_backend}")
        return 0

    def stop(*_):
        sup.stop_event.set()
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, stop)  # Windows service / console close
    sup.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
