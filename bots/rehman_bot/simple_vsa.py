"""
simple_vsa.py  Simple VSA check (Step 7) for the XAUUSD Liquidity + Engulfing Bot

Spec: When liquidity is touched, check the M5 volume.
- Compare the current candle volume with the previous 15-20 candles.
- BUY : High Volume + Long Lower Wick + Bullish Close
- SELL: High Volume + Long Upper Wick + Bearish Close
- If volume is not above average, ignore the setup.
"""

import pandas as pd
import config_new as cfg


def _vol_column(df: pd.DataFrame) -> str:
    return 'volume' if 'volume' in df.columns else 'tick_volume'


def _candle_at(df: pd.DataFrame, index: int):
    """Return OHLCV of a candle as floats."""
    row = df.iloc[index]
    return (
        float(row['open']), float(row['high']), float(row['low']),
        float(row['close']), float(row[_vol_column(df)]),
    )


def check_vsa(df_m5: pd.DataFrame, direction: str, index: int = -1) -> dict:
    """
    VSA check on a single M5 candle vs the previous candles.
    direction: 'buy' or 'sell'.
    index: candle to check (default -1 = latest).
    """
    if df_m5 is None or len(df_m5) < cfg.VSA_VOL_PERIOD + 2:
        return {'valid': False, 'reason': 'Insufficient M5 data for VSA'}

    if index == -1:
        index = len(df_m5) - 1
    if index < cfg.VSA_VOL_PERIOD or index >= len(df_m5):
        return {'valid': False, 'reason': 'VSA candle index out of range'}

    o, h, l, c, vol = _candle_at(df_m5, index)
    vols = df_m5[_vol_column(df_m5)].values.astype(float)
    avg_vol = float(vols[index - cfg.VSA_VOL_PERIOD:index].mean())

    vol_ratio = vol / avg_vol if avg_vol > 0 else 0.0
    if avg_vol <= 0:
        return {'valid': False, 'reason': 'Zero average volume'}

    # Rule: volume must be above the previous 15-20 candle average.
    if not (vol > avg_vol * cfg.VSA_VOL_MULT):
        return {
            'valid': False,
            'reason': f"Volume not above average "
                      f"({vol:.0f} <= avg {avg_vol:.0f} x {cfg.VSA_VOL_MULT})",
            'vol_ratio': round(vol_ratio, 2),
        }

    rng = h - l
    if rng <= 0:
        return {'valid': False, 'reason': 'Zero range candle', 'vol_ratio': round(vol_ratio, 2)}

    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    if direction == 'buy':
        if not (c > o):
            return {'valid': False, 'reason': 'Candle not bullish', 'vol_ratio': round(vol_ratio, 2)}
        if not (lower_wick >= rng * cfg.VSA_WICK_RATIO):
            return {'valid': False, 'reason': 'Lower wick too short', 'vol_ratio': round(vol_ratio, 2)}
    else:
        if not (c < o):
            return {'valid': False, 'reason': 'Candle not bearish', 'vol_ratio': round(vol_ratio, 2)}
        if not (upper_wick >= rng * cfg.VSA_WICK_RATIO):
            return {'valid': False, 'reason': 'Upper wick too short', 'vol_ratio': round(vol_ratio, 2)}

    return {
        'valid': True,
        'direction': direction,
        'vol_ratio': round(vol_ratio, 2),
        'avg_volume': round(avg_vol, 1),
        'candle_volume': round(float(vol), 1),
        'range': round(rng, 2),
        'upper_wick': round(upper_wick, 2),
        'lower_wick': round(lower_wick, 2),
    }
