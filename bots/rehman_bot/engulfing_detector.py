"""
engulfing_detector.py  5M/1M engulfing candle detection + sweep confirmation
- BSL swept  SELL, look for bearish engulfing
- SSL swept  BUY, look for bullish engulfing
"""

import pandas as pd
import numpy as np
from config_new import ENGULFING_MIN_BODY_RATIO


def detect_bullish_engulfing(df: pd.DataFrame, index: int = -1) -> dict:
    if df.empty or len(df) < 3:
        return {'detected': False}
    if index == -1:
        index = len(df) - 1
    if index < 1 or index >= len(df):
        return {'detected': False}

    curr = df.iloc[index]
    prev = df.iloc[index - 1]

    curr_o, curr_c, curr_h, curr_l = float(curr['open']), float(curr['close']), float(curr['high']), float(curr['low'])
    prev_o, prev_c = float(prev['open']), float(prev['close'])
    curr_body = abs(curr_c - curr_o)
    prev_body = abs(prev_c - prev_o)
    curr_range = curr_h - curr_l

    if curr_range == 0:
        return {'detected': False}
    if not (curr_c > curr_o and prev_c < prev_o):
        return {'detected': False}
    if not (curr_body > prev_body):
        return {'detected': False}

    body_ratio_check = curr_body / curr_range
    if body_ratio_check < ENGULFING_MIN_BODY_RATIO:
        return {'detected': False}

    upper_wick = curr_h - max(curr_o, curr_c)
    lower_wick = min(curr_o, curr_c) - curr_l
    wick_ratio = max(upper_wick, lower_wick) / curr_range if curr_range > 0 else 0

    return {
        'detected': True,
        'type': 'bullish_engulfing',
        'direction': 'buy',
        'body_size': round(float(curr_body), 2),
        'body_ratio': round(float(curr_body / prev_body if prev_body > 0 else 0), 2),
        'body_to_range': round(float(body_ratio_check), 2),
        'wick_ratio': round(float(wick_ratio), 2),
        'candle_index': index,
        'candle_time': str(curr['time']),
        'entry_price': round(float(curr_c), 2),
        'candle_high': round(float(curr_h), 2),
        'candle_low': round(float(curr_l), 2),
        'strength': 'strong' if curr_body >= 2 * prev_body else 'moderate' if curr_body >= 1.5 * prev_body else 'weak',
    }


def detect_bearish_engulfing(df: pd.DataFrame, index: int = -1) -> dict:
    if df.empty or len(df) < 3:
        return {'detected': False}
    if index == -1:
        index = len(df) - 1
    if index < 1 or index >= len(df):
        return {'detected': False}

    curr = df.iloc[index]
    prev = df.iloc[index - 1]

    curr_o, curr_c, curr_h, curr_l = float(curr['open']), float(curr['close']), float(curr['high']), float(curr['low'])
    prev_o, prev_c = float(prev['open']), float(prev['close'])
    curr_body = abs(curr_c - curr_o)
    prev_body = abs(prev_c - prev_o)
    curr_range = curr_h - curr_l

    if curr_range == 0:
        return {'detected': False}
    if not (curr_c < curr_o and prev_c > prev_o):
        return {'detected': False}
    if not (curr_body > prev_body):
        return {'detected': False}

    body_ratio_check = curr_body / curr_range
    if body_ratio_check < ENGULFING_MIN_BODY_RATIO:
        return {'detected': False}

    upper_wick = curr_h - max(curr_o, curr_c)
    lower_wick = min(curr_o, curr_c) - curr_l
    wick_ratio = max(upper_wick, lower_wick) / curr_range if curr_range > 0 else 0

    return {
        'detected': True,
        'type': 'bearish_engulfing',
        'direction': 'sell',
        'body_size': round(float(curr_body), 2),
        'body_ratio': round(float(curr_body / prev_body if prev_body > 0 else 0), 2),
        'body_to_range': round(float(body_ratio_check), 2),
        'wick_ratio': round(float(wick_ratio), 2),
        'candle_index': index,
        'candle_time': str(curr['time']),
        'entry_price': round(float(curr_c), 2),
        'candle_high': round(float(curr_h), 2),
        'candle_low': round(float(curr_l), 2),
        'strength': 'strong' if curr_body >= 2 * prev_body else 'moderate' if curr_body >= 1.5 * prev_body else 'weak',
    }


def _check_last_candles(df: pd.DataFrame, direction: str, n_candles: int = 2) -> dict:
    for i in range(n_candles - 1, -1, -1):
        idx = len(df) - 1 - i
        if idx < 1:
            continue
        if direction == 'buy':
            result = detect_bullish_engulfing(df, idx)
        else:
            result = detect_bearish_engulfing(df, idx)
        if result['detected']:
            return result
    return {'detected': False}


def confirm_5m_1m_engulfing(df_5m: pd.DataFrame, df_1m: pd.DataFrame,
                             expected_direction: str) -> dict:
    """
    Check 5M + 1M engulfing in expected direction.
    Both must confirm in the same direction for entry.
    """
    result = {'confirmed': False, 'engulf_5m': None, 'engulf_1m': None, 'reason': ''}

    engulf_5m = _check_last_candles(df_5m, expected_direction, n_candles=2)
    if not engulf_5m['detected']:
        result['reason'] = f'No 5M {expected_direction} engulfing in last 2 candles'
        return result

    result['engulf_5m'] = engulf_5m

    engulf_1m = _check_last_candles(df_1m, expected_direction, n_candles=2)
    if not engulf_1m['detected']:
        result['reason'] = f'No 1M {expected_direction} engulfing in last 2 candles'
        return result

    result['engulf_1m'] = engulf_1m
    result['confirmed'] = True
    result['reason'] = f'5M + 1M {expected_direction} engulfing confirmed'

    strength = 0
    for p in [engulf_5m, engulf_1m]:
        s = p.get('strength', '')
        if s == 'strong':
            strength += 40
        elif s == 'moderate':
            strength += 25
        else:
            strength += 10
    result['confirmation_strength'] = min(100, strength)

    return result


def confirm_sweep_engulfing(sweep_result: dict, df_5m: pd.DataFrame,
                             df_1m: pd.DataFrame) -> dict:
    """
    Combined sweep + engulfing confirmation.
    Uses sweep direction to determine expected engulfing direction.
    """
    result = {
        'sweep_confirmed': False,
        'engulfing_confirmed': False,
        'trade_ready': False,
        'direction': None,
        'sweep': sweep_result,
        'engulfing': None,
        'reason': '',
    }

    if not sweep_result.get('swept'):
        result['reason'] = 'No sweep detected'
        return result

    result['sweep_confirmed'] = True
    trade_dir = sweep_result['direction']

    engulf = confirm_5m_1m_engulfing(df_5m, df_1m, trade_dir)
    result['engulfing'] = engulf

    if engulf['confirmed']:
        result['engulfing_confirmed'] = True
        result['trade_ready'] = True
        result['direction'] = trade_dir
        result['reason'] = f'Sweep + engulfing confirmed: {trade_dir}'
    else:
        result['reason'] = f'Swept but no engulfing: {engulf["reason"]}'

    return result


def scan_for_engulfing(df: pd.DataFrame, direction: str = None,
                       lookback: int = 10) -> dict:
    """Scan recent candles for engulfing patterns."""
    if df.empty or len(df) < 3:
        return {'found': False, 'patterns': []}

    patterns = []
    start = max(2, len(df) - lookback)

    for i in range(start, len(df)):
        if direction is None or direction == 'buy':
            bull = detect_bullish_engulfing(df, i)
            if bull['detected']:
                patterns.append(bull)
        if direction is None or direction == 'sell':
            bear = detect_bearish_engulfing(df, i)
            if bear['detected']:
                patterns.append(bear)

    if patterns:
        strongest = max(patterns, key=lambda x: x.get('body_ratio', 0))
        return {'found': True, 'latest': strongest, 'patterns': patterns, 'count': len(patterns)}

    return {'found': False, 'patterns': []}


def detect_engulf_chain(df: pd.DataFrame, direction: str, lookback: int = 12) -> dict:
    """
    Engulfing -> that engulfing candle swept -> engulfing again -> entry.
    buy : bullish engulfing, its low swept, bullish engulfing again.
    sell: bearish engulfing, its high swept, bearish engulfing again.
    Second engulfing must be on the last 2 candles (fresh confirmation).
    """
    if df is None or df.empty or len(df) < 5:
        return {'detected': False}

    n = len(df)
    start = max(2, n - lookback)
    highs = df['high'].values.astype(float)
    lows = df['low'].values.astype(float)
    times = df['time'].values
    detect = detect_bullish_engulfing if direction == 'buy' else detect_bearish_engulfing

    for i in range(start, n - 2):
        first = detect(df, i)
        if not first['detected']:
            continue
        for j in range(i + 1, n):
            if direction == 'buy':
                swept = lows[j] < lows[i]
            else:
                swept = highs[j] > highs[i]
            if not swept:
                continue
            for k in range(max(j + 1, n - 2), n):
                second = detect(df, k)
                if not second['detected']:
                    continue
                if direction == 'buy':
                    extreme = float(min(lows[i:k + 1]))
                else:
                    extreme = float(max(highs[i:k + 1]))
                return {
                    'detected': True,
                    'direction': direction,
                    'engulf1_time': str(times[i]),
                    'sweep_time': str(times[j]),
                    'engulf2_time': str(times[k]),
                    'sweep_extreme': round(extreme, 2),
                    'strength': second.get('strength', ''),
                }
    return {'detected': False}
