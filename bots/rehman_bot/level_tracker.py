"""
level_tracker.py  Liquidity sweep + reclaim detection and setup state tracking

Implements the Level / Setup rules of the XAUUSD Liquidity + Engulfing Bot:

- Step 6  Liquidity Sweep
    BUY : price goes BELOW the liquidity level, then comes back ABOVE it.
    SELL: price goes ABOVE the liquidity level, then comes back BELOW it.
- Step 6b Engulfing chain entry (checked on M5 / M3 / M1 when price
    reaches a marked level):
    engulfing -> that engulfing candle swept -> engulfing again -> entry.
- Max 2 entries on the same liquidity level.
- If price leaves the zone without confirmation, cancel the setup.
- No setup timeout - setup stays until zone exit / level used up.
"""

import config_new as cfg
from engulfing_detector import detect_engulf_chain


def detect_sweep_reclaim(df_m5, level_price: float, side: str):
    """
    Detect a liquidity sweep with reclaim on M5 candles.

    side:
      'below' -> SSL level below price; sweep = low < level, reclaim = close back above  BUY
      'above' -> BSL level above price; sweep = high > level, reclaim = close back below  SELL

    Returns a sweep dict or None.
    """
    if df_m5 is None or len(df_m5) < 3:
        return None

    highs = df_m5['high'].values.astype(float)
    lows = df_m5['low'].values.astype(float)
    closes = df_m5['close'].values.astype(float)
    times = df_m5['time'].values
    n = len(df_m5)

    lookback = cfg.SWEEP_LOOKBACK_CANDLES

    sweep_idx = None
    start = n - 1
    end = max(0, n - lookback) - 1
    for i in range(start, end, -1):
        if side == 'below' and lows[i] < level_price:
            sweep_idx = i
            break
        if side == 'above' and highs[i] > level_price:
            sweep_idx = i
            break

    if sweep_idx is None:
        return None

    if side == 'below':
        for j in range(sweep_idx, n):
            if closes[j] > level_price:
                sweep_low = float(min(lows[sweep_idx:j + 1]))
                return {
                    'swept': True,
                    'direction': 'buy',
                    'sweep_index': sweep_idx,
                    'reclaim_index': j,
                    'sweep_low': round(sweep_low, 2),
                    'sweep_time': str(times[sweep_idx]),
                    'reclaim_time': str(times[j]),
                }
    else:
        for j in range(sweep_idx, n):
            if closes[j] < level_price:
                sweep_high = float(max(highs[sweep_idx:j + 1]))
                return {
                    'swept': True,
                    'direction': 'sell',
                    'sweep_index': sweep_idx,
                    'reclaim_index': j,
                    'sweep_high': round(sweep_high, 2),
                    'sweep_time': str(times[sweep_idx]),
                    'reclaim_time': str(times[j]),
                }

    return None


class LevelTracker:
    """Tracks liquidity levels and their active setups."""

    def __init__(self):
        self._levels = {}
        self._setups = []

    def _key(self, price: float) -> float:
        return round(float(price), 2)

    def refresh_levels(self, levels: list, current_price: float) -> dict:
        """
        Refresh the pool of marked (unswept / still valid) liquidity levels.

        levels: list of dicts {price, name, source, type, side}.
        A level is dropped once it has produced MAX_ENTRIES_PER_LEVEL entries.
        """
        fresh = {}
        for lvl in levels:
            p = self._key(lvl['price'])
            if p in fresh:
                continue
            existing = self._levels.get(p)
            if existing and existing['entries'] >= cfg.MAX_ENTRIES_PER_LEVEL:
                continue
            fresh[p] = {
                'price': lvl['price'],
                'name': lvl['name'],
                'source': lvl['source'],
                'type': lvl['type'],
                'side': lvl['side'],
                'entries': existing['entries'] if existing else 0,
                'active': True,
            }

        active_keys = set(fresh.keys())
        self._setups = [s for s in self._setups if s['level_key'] in active_keys]
        self._levels = fresh
        return self._levels

    def scan_setups(self, df_m5, df_m3=None, df_m1=None) -> list:
        """
        Look for new setups on marked levels:
        - M5 liquidity sweep + reclaim, OR
        - engulfing chain (engulf -> sweep of engulfing candle -> engulf)
          when price reaches the level - checked on M5, M3 and M1.
        """
        active_keys = {s['level_key'] for s in self._setups}
        new_setups = []

        for key, lvl in self._levels.items():
            if key in active_keys:
                continue
            if lvl['entries'] >= cfg.MAX_ENTRIES_PER_LEVEL:
                continue
            if not lvl.get('active', True):
                continue

            sweep = detect_sweep_reclaim(df_m5, lvl['price'], lvl['side'])
            if sweep and sweep.get('swept'):
                sweep['level_key'] = key
                sweep['level'] = lvl
                self._setups.append(sweep)
                new_setups.append(sweep)
                continue

            # Engulfing chain entry on M5 / M3 / M1 at the level
            direction = 'buy' if lvl['side'] == 'below' else 'sell'
            for tf_name, df in (('M5', df_m5), ('M3', df_m3), ('M1', df_m1)):
                if df is None or df.empty:
                    continue
                if not self._level_reached(lvl, df):
                    continue
                chain = detect_engulf_chain(df, direction,
                                            cfg.ENGULF_CHAIN_LOOKBACK)
                if not chain.get('detected'):
                    continue
                setup = {
                    'direction': direction,
                    'level_key': key,
                    'level': lvl,
                    'sweep_time': chain['sweep_time'],
                    'trigger': f"engulf_{tf_name}",
                    'strength': chain.get('strength', ''),
                }
                if direction == 'buy':
                    setup['sweep_low'] = chain['sweep_extreme']
                else:
                    setup['sweep_high'] = chain['sweep_extreme']
                self._setups.append(setup)
                new_setups.append(setup)
                break

        return new_setups

    def _level_reached(self, lvl: dict, df) -> bool:
        """Price touched the marked level within the chain search window."""
        if df is None or df.empty:
            return False
        recent = df.tail(cfg.ENGULF_CHAIN_LOOKBACK)
        if lvl['side'] == 'below':
            return float(recent['low'].min()) <= lvl['price']
        return float(recent['high'].max()) >= lvl['price']

    def active_setups(self) -> list:
        """Return a snapshot of currently active setups."""
        return list(self._setups)

    def cancel_setup(self, setup: dict):
        """Cancel a single setup (VSA failed / zone left)."""
        self._setups = [s for s in self._setups if s is not setup]

    def check_zone_exit(self, price: float) -> list:
        """
        Cancel setups whose price leaves the zone without confirmation.

        BUY : price breaks back below the sweep low (reclaim failed).
        SELL: price breaks back above the sweep high (reclaim failed).
        """
        cancelled = []
        kept = []
        for s in self._setups:
            if s['direction'] == 'buy':
                left_zone = price < s['sweep_low']
            else:
                left_zone = price > s['sweep_high']
            if left_zone:
                cancelled.append(s)
            else:
                kept.append(s)
        self._setups = kept
        return cancelled

    def on_trade_entered(self, setup: dict):
        """Register an executed trade against its level (max 2 per level)."""
        key = setup['level_key']
        lvl = self._levels.get(key)
        if lvl:
            lvl['entries'] += 1
            if lvl['entries'] >= cfg.MAX_ENTRIES_PER_LEVEL:
                lvl['active'] = False
                self._levels.pop(key, None)
        self._setups = [s for s in self._setups if s is not setup]
