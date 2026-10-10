"""
h1_liquidity.py  H1 Major Swing Liquidity (Step 4)

Uses the SAME swing detection as H4 (fractal confirmation + origin-of-move),
then applies the same "not swept since formed" filter.
Classifies each swing as:
  - external: outside the current H4 range (major liquidity beyond H4 swings)
  - internal: inside the current H4 range
"""

import numpy as np

from swing_detector import detect_swing_points


def detect_h1_internal_external(df_h1, df_h4) -> dict:
    """Return {'bsl': [...], 'ssl': [...]} with an added 'category' field.
    Only unswept-since-formed swings are returned."""
    swings = detect_swing_points(df_h1)

    h1_highs = df_h1['high'].values.astype(float)
    h1_lows = df_h1['low'].values.astype(float)

    h4_high = None
    h4_low = None
    if df_h4 is not None and not df_h4.empty:
        h4_high = float(np.max(df_h4['high'].values))
        h4_low = float(np.min(df_h4['low'].values))

    out = {'bsl': [], 'ssl': []}

    for s in swings.get('swing_highs', []):
        # valid only if not swept since it was formed
        if any(h > s['price'] for h in h1_highs[s['index'] + 1:]):
            continue
        b = {'price': s['price'], 'move': s['move_size']}
        if h4_high is not None and b['price'] > h4_high:
            b['category'] = 'external'
        else:
            b['category'] = 'internal'
        out['bsl'].append(b)

    for s in swings.get('swing_lows', []):
        # valid only if not swept since it was formed
        if any(l < s['price'] for l in h1_lows[s['index'] + 1:]):
            continue
        b = {'price': s['price'], 'move': s['move_size']}
        if h4_low is not None and b['price'] < h4_low:
            b['category'] = 'external'
        else:
            b['category'] = 'internal'
        out['ssl'].append(b)

    return out
