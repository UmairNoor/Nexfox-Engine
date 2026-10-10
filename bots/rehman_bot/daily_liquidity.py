"""
daily_liquidity.py  D1 Liquidity Levels
PDH/PDL + D1 Equal Highs/Lows only.
Equal highs/lows = 2+ consecutive swing highs/lows within tolerance
forming a liquidity pool (BSL above / SSL below).
"""

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from datetime import datetime, timezone

SWING_MIN_MOVE_PTS = 15.0
EQUAL_TOLERANCE_PTS = 4.0
RECENT_BARS = 45
LOOKBACK_BARS = 90


def get_daily_data(symbol: str, bars_back: int = LOOKBACK_BARS) -> pd.DataFrame:
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, bars_back)
    if rates is None or len(rates) == 0:
        return pd.DataFrame()
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    return df


def _swing_highs(df: pd.DataFrame) -> list:
    """Structural swing highs (move-confirmed) on D1."""
    hi = df['high'].values.astype(float)
    lo = df['low'].values.astype(float)
    vals = []
    lookahead = 6
    for i in range(3, len(df) - 2):
        left = hi[i - 3:i + 1]
        if hi[i] != max(left):
            continue
        subsequent_low = min(lo[i:min(i + lookahead + 1, len(lo))])
        move = hi[i] - subsequent_low
        if move >= SWING_MIN_MOVE_PTS:
            vals.append({
                'price': round(float(hi[i]), 2),
                'index': i,
                'time': str(df['time'].values[i])[:10],
                'move': round(float(move), 2),
            })
    return vals


def _swing_lows(df: pd.DataFrame) -> list:
    """Structural swing lows (move-confirmed) on D1."""
    hi = df['high'].values.astype(float)
    lo = df['low'].values.astype(float)
    vals = []
    lookahead = 6
    for i in range(3, len(df) - 2):
        right = lo[i:i + 3]
        if lo[i] != min(right):
            continue
        subsequent_high = max(hi[i:min(i + lookahead + 1, len(hi))])
        move = subsequent_high - lo[i]
        if move >= SWING_MIN_MOVE_PTS:
            vals.append({
                'price': round(float(lo[i]), 2),
                'index': i,
                'time': str(df['time'].values[i])[:10],
                'move': round(float(move), 2),
            })
    return vals


def _cluster_levels(vals: list, tol: float = EQUAL_TOLERANCE_PTS,
                    recent_bars: int = RECENT_BARS, max_index: int = 0) -> list:
    """Cluster swing points within tolerance into liquidity pools.
    Non-consecutive swings allowed. Clusters must contain a recent swing."""
    out = []
    for v in vals:
        placed = False
        for c in out:
            if abs(c['level'] - v['price']) <= tol:
                c['prices'].append(v['price'])
                c['count'] += 1
                c['level'] = round(sum(c['prices']) / len(c['prices']), 2)
                c['time'] = v['time']
                c['index'] = v['index']  # latest member = latest formation
                placed = True
                break
        if not placed:
            out.append({'level': v['price'], 'count': 1, 'prices': [v['price']],
                        'time': v['time'], 'index': v['index']})

    return [
        c for c in out
        if c['count'] >= 2 and c['index'] >= max_index - recent_bars
    ]


def _equal_highs(df: pd.DataFrame) -> list:
    """Equal highs: clustered swing highs within tolerance (BSL pool)."""
    sw = _swing_highs(df)
    return [
        {
            'level': c['level'],
            'count': c['count'],
            'prices': c['prices'],
            'time': c['time'],
            'type': 'EQUAL_HIGHS',
        }
        for c in _cluster_levels(sw, max_index=len(df))
    ]


def _equal_lows(df: pd.DataFrame) -> list:
    """Equal lows: clustered swing lows within tolerance (SSL pool)."""
    sw = _swing_lows(df)
    return [
        {
            'level': c['level'],
            'count': c['count'],
            'prices': c['prices'],
            'time': c['time'],
            'type': 'EQUAL_LOWS',
        }
        for c in _cluster_levels(sw, max_index=len(df))
    ]


def detect_daily_liquidity(df: pd.DataFrame, now=None) -> dict:
    if df.empty or len(df) < 2:
        return {'error': 'Insufficient daily data'}

    if now is None:
        now = datetime.now(timezone.utc)

    # Most recent COMPLETED daily bar.
    # A D1 bar closes at its timestamp + 1 day. If that has passed, it is
    # complete. During weekends the last bar is complete -> use it as prev_day.
    prev_idx = -1
    for i in range(len(df) - 1, -1, -1):
        bar_start = pd.to_datetime(df['time'].values[i], unit='s', utc=True)
        if bar_start + pd.Timedelta(days=1) <= now:
            prev_idx = i
            break
    if prev_idx < 0:
        prev_idx = len(df) - 2

    prev_day = df.iloc[prev_idx]

    result = {
        'pdh': round(float(prev_day['high']), 2),
        'pdl': round(float(prev_day['low']), 2),
        'prev_day_date': prev_day['time'].date(),
        'prev_idx': prev_idx,
        'swing_highs': _swing_highs(df),
        'swing_lows': _swing_lows(df),
        'equal_highs': [],
        'equal_lows': [],
    }

    result['equal_highs'] = _equal_highs(df)
    result['equal_lows'] = _equal_lows(df)

    return result


def _categorize_bsl_ssl(levels: dict, current_price: float,
                        df: pd.DataFrame = None) -> dict:
    """
    PDH/PDL + Equal highs/lows -> BSL (above) / SSL (below).

    A level is marked ONLY if it has NOT been swept since it was formed:
      - BSL (above)  valid only if no daily bar AFTER the level's bar
                      traded ABOVE its tip
      - SSL (below)  valid only if no daily bar AFTER the level's bar
                      traded BELOW its tip
    Old history before the level was formed is ignored (that liquidity was
    for an older setup, not the current one).
    """
    bsl = []
    ssl = []
    seen = set()

    if df is not None and not df.empty:
        highs = df['high'].values.astype(float)
        lows = df['low'].values.astype(float)
    else:
        highs = []
        lows = []

    def swept_above(tip: float, formed_idx: int) -> bool:
        """Liquidity above `tip` consumed if any later high exceeds it."""
        return any(h > tip for h in highs[formed_idx + 1:])

    def swept_below(tip: float, formed_idx: int) -> bool:
        """Liquidity below `tip` consumed if any later low goes under it."""
        return any(l < tip for l in lows[formed_idx + 1:])

    prev_idx = levels.get('prev_idx', len(highs) - 2)

    pdh = levels.get('pdh')
    if (pdh is not None and pdh > current_price and pdh not in seen
            and not swept_above(pdh, prev_idx)):
        seen.add(pdh)
        bsl.append({'price': pdh, 'name': 'PDH', 'source': 'D1'})

    pdl = levels.get('pdl')
    if (pdl is not None and pdl < current_price and pdl not in seen
            and not swept_below(pdl, prev_idx)):
        seen.add(pdl)
        ssl.append({'price': pdl, 'name': 'PDL', 'source': 'D1'})

    # ALL daily swing highs not yet swept (above price) -> BSL
    for s in levels.get('swing_highs', []):
        p = s['price']
        if (p > current_price and p not in seen
                and not swept_above(p, s.get('index', prev_idx))):
            seen.add(p)
            bsl.append({'price': p, 'name': 'D1_SW_HI', 'source': 'D1',
                        'move': s.get('move')})

    # ALL daily swing lows not yet swept (below price) -> SSL
    for s in levels.get('swing_lows', []):
        p = s['price']
        if (p < current_price and p not in seen
                and not swept_below(p, s.get('index', prev_idx))):
            seen.add(p)
            ssl.append({'price': p, 'name': 'D1_SW_LO', 'source': 'D1',
                        'move': s.get('move')})

    for e in levels.get('equal_highs', []):
        p = e['level']
        tip = max(e['prices'])  # highest member = real resistance tip
        if (p > current_price and p not in seen
                and not swept_above(tip, e.get('index', prev_idx))):
            seen.add(p)
            bsl.append({'price': p, 'name': 'D1_EQH', 'source': 'D1',
                        'count': e['count'], 'prices': e['prices']})

    for e in levels.get('equal_lows', []):
        p = e['level']
        tip = min(e['prices'])  # lowest member = real support tip
        if (p < current_price and p not in seen
                and not swept_below(tip, e.get('index', prev_idx))):
            seen.add(p)
            ssl.append({'price': p, 'name': 'D1_EQL', 'source': 'D1',
                        'count': e['count'], 'prices': e['prices']})

    bsl.sort(key=lambda x: x['price'])
    ssl.sort(key=lambda x: x['price'], reverse=True)
    return {'bsl': bsl, 'ssl': ssl}


def analyze(symbol: str, current_price: float) -> dict:
    df = get_daily_data(symbol)
    levels = detect_daily_liquidity(df)
    if 'error' in levels:
        return levels
    bsl_ssl = _categorize_bsl_ssl(levels, current_price, df)
    return {**levels, **bsl_ssl}
