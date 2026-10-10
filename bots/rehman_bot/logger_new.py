"""
logger_new.py  Simple Logging System for Liquidity + Engulfing Bot
"""

import os
from datetime import datetime, timezone
from config_new import LOG_DIR, LOG_FILE


def log(msg: str, level: str = "INFO", print_it: bool = True):
    """Log a message to file and optionally print."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"

    if print_it:
        print(line)

    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + "\n")
    except Exception:
        pass


def log_separator(title: str = ""):
    """Print a visual separator."""
    sep = "" * 50
    if title:
        msg = f"\n{sep}\n  {title}\n{sep}"
    else:
        msg = f"\n{sep}"
    print(msg)
    log(msg, print_it=False)


def log_analysis(module: str, data: dict):
    """Log analysis data from a module."""
    log(f"[{module}] {data}", "ANALYSIS")
