"""
swing_detector.py  4H Major Swing Detection (Origin-of-Move)
Only marks major structural swings where dump/bounce >= 50pts.
A swing high = high with `lookback` lower highs on the LEFT and
`confirm_bars` lower highs on the RIGHT -> price dumps from here.
A swing low  = low  with `lookback` higher lows on the LEFT and
`confirm_bars` higher lows on the RIGHT -> price bounces from here.
"""

import pandas as pd
import numpy as np
from config_new import SWING_MIN_MOVE_PTS


def detect_swing_points(df: pd.DataFrame, lookback: int = 2,
                        min_move_pts: float = None,
                        confirm_bars: int = 2,
                        move_lookahead: int = 12) -> dict:
    if min_move_pts is None:
        min_move_pts = SWING_MIN_MOVE_PTS

    if df.empty or len(df) < lookback + confirm_bars + 1:
        return {'swing_highs': [], 'swing_lows': []}

    highs = df['high'].values.astype(float)
    lows = df['low'].values.astype(float)
    times = df['time'].values

    swing_highs = []
    swing_lows = []

    for i in range(lookback, len(df) - confirm_bars):
        left_h = highs[i - lookback:i]
        right_h = highs[i + 1:i + confirm_bars + 1]
        left_l = lows[i - lookback:i]
        right_l = lows[i + 1:i + confirm_bars + 1]

        # Swing high: current high above all left & right highs
        if (len(right_h) == confirm_bars
                and highs[i] > left_h.max() and highs[i] > right_h.max()):
            # how far price dumped AFTER this high (measured over lookahead)
            ahead = lows[i + 1:i + 1 + move_lookahead]
            dump = highs[i] - min(ahead) if len(ahead) else 0.0
            if dump >= min_move_pts:
                swing_highs.append({
                    'price': round(float(highs[i]), 2),
                    'time': str(times[i]),
                    'move_size': round(float(dump), 2),
                    'index': i,
                })

        # Swing low: current low below all left & right lows
        if (len(right_l) == confirm_bars
                and lows[i] < left_l.min() and lows[i] < right_l.min()):
            # how far price bounced AFTER this low (measured over lookahead)
            ahead = highs[i + 1:i + 1 + move_lookahead]
            bounce = max(ahead) - lows[i] if len(ahead) else 0.0
            if bounce >= min_move_pts:
                swing_lows.append({
                    'price': round(float(lows[i]), 2),
                    'time': str(times[i]),
                    'move_size': round(float(bounce), 2),
                    'index': i,
                })

    # Merge nearby, keep strongest
    swing_highs = _merge_close(swing_highs)
    swing_lows = _merge_close(swing_lows)

    return {
        'swing_highs': swing_highs,
        'swing_lows': swing_lows,
    }


def _merge_close(swings: list, tolerance: float = 5.0) -> list:
    if not swings:
        return []
    swings = sorted(swings, key=lambda x: x['index'])
    merged = [swings[0]]
    for s in swings[1:]:
        if abs(s['price'] - merged[-1]['price']) <= tolerance:
            # keep the one with the bigger move, but remember the later index
            if s['move_size'] > merged[-1]['move_size']:
                merged[-1] = s
        else:
            merged.append(s)
    return merged
