"""
reaction_zones.py  1H Liquidity Detection (Origin-of-Move)
Finds major 1H structure swings where dump/bounce >= 40pts.
"""

import pandas as pd
import numpy as np
from config_new import H1_MIN_MOVE_PTS


def detect_1h_liquidity(df: pd.DataFrame, min_move_pts: float = None,
                        lookback: int = 5) -> dict:
    if min_move_pts is None:
        min_move_pts = H1_MIN_MOVE_PTS

    if df.empty or len(df) < 20:
        return {'bsl': [], 'ssl': []}

    highs = df['high'].values.astype(float)
    lows = df['low'].values.astype(float)
    times = df['time'].values

    bsl = []
    ssl = []

    for i in range(lookback, len(df) - lookback):
        if highs[i] == max(highs[i - lookback:i + lookback + 1]):
            dump = highs[i] - min(lows[i:i + lookback + 1])
            if dump >= min_move_pts:
                bsl.append({
                    'price': round(float(highs[i]), 2),
                    'move': round(float(dump), 2),
                    'time': str(times[i]),
                    'type': 'bsl_origin',
                })

        if lows[i] == min(lows[i - lookback:i + lookback + 1]):
            bounce = max(highs[i:i + lookback + 1]) - lows[i]
            if bounce >= min_move_pts:
                ssl.append({
                    'price': round(float(lows[i]), 2),
                    'move': round(float(bounce), 2),
                    'time': str(times[i]),
                    'type': 'ssl_origin',
                })

    bsl = _merge_close(bsl)
    ssl = _merge_close(ssl)
    bsl.sort(key=lambda x: x['move'], reverse=True)
    ssl.sort(key=lambda x: x['move'], reverse=True)

    return {'bsl': bsl[:4], 'ssl': ssl[:4]}


def _merge_close(levels: list, tolerance: float = 3.0) -> list:
    if not levels:
        return []
    merged = [levels[0]]
    for l in levels[1:]:
        if abs(l['price'] - merged[-1]['price']) <= tolerance:
            if l['move'] > merged[-1]['move']:
                merged[-1] = l
        else:
            merged.append(l)
    return merged
