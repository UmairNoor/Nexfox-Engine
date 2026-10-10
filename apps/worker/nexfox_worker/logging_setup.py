"""Structured worker logs (same field names as the API: ts, level, service, module, message...). Never logs secrets."""
import json
import logging
import os
from datetime import datetime, timezone

SECRET_KEYS = {"password", "token", "authorization", "mt5_password"}


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, r: logging.LogRecord) -> str:
        out = {"ts": datetime.fromtimestamp(r.created, timezone.utc).isoformat(timespec="milliseconds"), "level": r.levelname,
               "env": os.environ.get("ENV", "prod"), "service": self.service, "module": r.name, "message": r.getMessage()}
        if r.exc_info:
            out["exc"] = self.formatException(r.exc_info)[-2000:]
        return json.dumps(out, default=str)


def setup(json_logs: bool, service: str) -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    h = logging.StreamHandler()
    h.setFormatter(JsonFormatter(f"worker:{service}") if json_logs else logging.Formatter(f"%(asctime)s %(levelname)s [{service}] %(name)s: %(message)s"))
    root.addHandler(h)
    root.setLevel(os.environ.get("LOG_LEVEL", "INFO"))
    logging.getLogger("httpx").setLevel(logging.WARNING)
