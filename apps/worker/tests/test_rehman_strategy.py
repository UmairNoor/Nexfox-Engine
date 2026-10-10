"""Characterization tests for the unchanged Rehman Bot strategy modules (bots/rehman_bot). They pin current behaviour
so a future change to the strategy is a visible, reviewed decision — not an accident."""
import importlib
import sys

import numpy as np
import pandas as pd
from conftest import BOTS

sys.path.insert(0, str(BOTS / "rehman_bot"))


def _mod(name):
    if "MetaTrader5" not in sys.modules:
        from nexfox_worker.fake_mt5 import FakeMT5
        sys.modules["MetaTrader5"] = FakeMT5({})
    return importlib.import_module(name)


def _df(o, h, lo, c):
    n = len(o)
    return pd.DataFrame({"time": pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC"), "open": o, "high": h, "low": lo,
                         "close": c, "volume": np.full(n, 100)})


def test_bullish_and_bearish_engulfing():
    ed = _mod("engulfing_detector")
    df = _df([10, 10.5, 9.4], [10.6, 10.6, 11.2], [9.9, 9.5, 9.3], [10.5, 9.6, 11.1])
    assert ed.detect_bullish_engulfing(df, 2)["detected"]
    assert not ed.detect_bearish_engulfing(df, 2)["detected"]
    df2 = _df([10, 9.5, 10.6], [10.6, 10.6, 10.7], [9.4, 9.4, 8.8], [9.6, 10.5, 8.9])
    assert ed.detect_bearish_engulfing(df2, 2)["detected"]


def test_sweep_and_reclaim_below_level_gives_buy():
    lt = _mod("level_tracker")
    lows = [2405, 2404, 2403, 2398.5, 2401, 2402]
    df = _df([2406, 2405, 2404, 2403, 2399, 2401.5], [2407, 2406, 2405, 2404, 2402, 2403], lows, [2405, 2404, 2403.5, 2399, 2401.5, 2402.5])
    s = lt.detect_sweep_reclaim(df, 2400.0, "below")
    assert s and s["direction"] == "buy" and s["sweep_low"] == 2398.5
    assert lt.detect_sweep_reclaim(df, 2390.0, "below") is None


def test_level_tracker_caps_entries_per_level():
    lt = _mod("level_tracker")
    cfg = _mod("config_new")
    t = lt.LevelTracker()
    lvl = {"price": 2400.0, "name": "PDL", "source": "D1", "type": "PDL", "side": "below"}
    t.refresh_levels([lvl], 2410)
    for _ in range(cfg.MAX_ENTRIES_PER_LEVEL):
        t.on_trade_entered({"level_key": 2400.0})
    assert 2400.0 not in t._levels


def test_swing_detector_finds_major_swing():
    sd = _mod("swing_detector")
    highs = [10, 11, 12, 20, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 2]
    lows = [h - 1 for h in highs]
    df = _df(highs, highs, lows, highs)
    out = sd.detect_swing_points(df, min_move_pts=5.0)
    assert any(s["price"] == 20 for s in out["swing_highs"])


def test_default_platform_config_matches_original_bot():
    """The platform's packaged defaults for Rehman Bot 1.0.0 must equal bots/rehman_bot/config_new.py."""
    cfg = _mod("config_new")
    sys.path.insert(0, str(BOTS.parent / "apps" / "api"))
    from app.bots.rehman import RehmanBot  # noqa: E402  (pure dataclass, no DB access)
    for k, v in RehmanBot().default_config().items():
        if k in ("schema_version", "dry_run"):
            continue
        assert getattr(cfg, k.upper()) == v, k
