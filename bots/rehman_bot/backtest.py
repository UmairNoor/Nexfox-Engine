"""
backtest.py  Historical backtest of the XAUUSD Liquidity + Engulfing strategy
"""
import sys
import time
from datetime import datetime, timezone

import MetaTrader5 as mt5
import numpy as np
import pandas as pd

import config_new as cfg
import daily_liquidity as dl
from swing_detector import detect_swing_points
from h1_liquidity import detect_h1_internal_external
from engulfing_detector import scan_for_engulfing, detect_engulf_chain
from simple_vsa import check_vsa
from level_tracker import detect_sweep_reclaim

# ---- VSA test parameters (overrides cfg) ----
import sys
ENTRY_MODE = sys.argv[1] if len(sys.argv) > 1 else 'live'  # 'live'/'full'/'m5'/'auto'/'sweep'
VSA_ON = sys.argv[2] if len(sys.argv) > 2 else 'off'   # 'sweep'/'engulf'/'off'
VSA_VOL_MULT = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0
VSA_WICK = float(sys.argv[4]) if len(sys.argv) > 4 else 0.20
SESSION_FILTER = sys.argv[5] if len(sys.argv) > 5 else 'all'   # 'all'/'london'/'ny'
STRENGTH_FILTER = sys.argv[6] if len(sys.argv) > 6 else 'all'  # 'all'/'sm'
TP_MAX_RR = float(sys.argv[7]) if len(sys.argv) > 7 else 0.0   # 0 = no cap
SPLIT = sys.argv[8] if len(sys.argv) > 8 else 'off'            # 'on' = TP1+trail runner
TP1_RR = float(sys.argv[9]) if len(sys.argv) > 9 else 0.0     # 0 = use cfg
COST_PTS = float(sys.argv[10]) if len(sys.argv) > 10 else 30.0  # net frictional cost (spread+slippage round-trip) in points
if TP1_RR > 0:
    cfg.TP1_RR = TP1_RR
if VSA_ON == 'off':
    VSA_VOL_MULT = 0.0
    VSA_WICK = 0.0
cfg.VSA_VOL_MULT = VSA_VOL_MULT
cfg.VSA_WICK_RATIO = VSA_WICK

SYMBOL = cfg.SYMBOL

if not mt5.initialize(path=cfg.MT5_PATH):
    raise RuntimeError(f'MT5 init failed: {mt5.last_error()}')
if not mt5.login(login=cfg.MT5_LOGIN, password=cfg.MT5_PASSWORD, server=cfg.MT5_SERVER):
    raise RuntimeError(f'MT5 login failed: {mt5.last_error()}')

# --------------------------------------------------------------------------
# Data loading
# --------------------------------------------------------------------------
from datetime import datetime as _dt


def fetch(tf, count, start=None):
    rates = None
    for attempt in range(5):
        if start is not None:
            rates = mt5.copy_rates_range(SYMBOL, tf, start, datetime.now(timezone.utc))
        else:
            rates = mt5.copy_rates_from_pos(SYMBOL, tf, 0, count)
        if rates is not None and len(rates) > 0:
            break
        time.sleep(1)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f'No data for TF {tf}: {mt5.last_error()}')
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    return df


START = datetime(2026, 6, 1, tzinfo=timezone.utc)
d1 = fetch(mt5.TIMEFRAME_D1, 150, start=datetime(2026, 3, 1, tzinfo=timezone.utc))
h4 = fetch(mt5.TIMEFRAME_H4, 600, start=datetime(2026, 4, 1, tzinfo=timezone.utc))
h1 = fetch(mt5.TIMEFRAME_H1, 800, start=datetime(2026, 4, 1, tzinfo=timezone.utc))
m5 = fetch(mt5.TIMEFRAME_M5, 5000, start=START)
m3 = fetch(mt5.TIMEFRAME_M3, 5000, start=START)
m15 = fetch(mt5.TIMEFRAME_M15, 3000, start=datetime(2026, 4, 1, tzinfo=timezone.utc))
try:
    m1 = fetch(mt5.TIMEFRAME_M1, 5000, start=START)
except Exception as e:
    print(f'M1 unavailable, will skip M1 refinement: {e}')
    m1 = pd.DataFrame()

m5t = m5['time'].values.astype('datetime64[s]')
m3t = m3['time'].values.astype('datetime64[s]')
m15t = m15['time'].values.astype('datetime64[s]')
m1t = m1['time'].values.astype('datetime64[s]') if len(m1) else np.array([])
d1t = d1['time'].values.astype('datetime64[s]')
h4t = h4['time'].values.astype('datetime64[s]')
h1t = h1['time'].values.astype('datetime64[s]')

start_i = int(np.searchsorted(m5t, m3t[0], side='left'))
print(f'Backtest window: {m5t[start_i]} -> {m5t[-1]}  '
      f'({len(m5) - start_i} M5 candles, {(len(m5) - start_i) * 5 / 60 / 24:.1f} days)')

# --------------------------------------------------------------------------
# Levels at a given backtest time (D1 + H4 + H1, dedupe, nearest 2/side)
# --------------------------------------------------------------------------
def _slice(df, tarr, t):
    return df.iloc[:int(np.searchsorted(tarr, t, side='right'))]


def _aware(t):
    if isinstance(t, pd.Timestamp):
        if t.tzinfo is not None:
            return t
        return t.tz_localize('UTC')
    return pd.Timestamp(t, unit='s', tz='UTC')


def levels_at(t, price):
    d1s = _slice(d1, d1t, t)
    if len(d1s) < 2:
        return []
    now = _aware(t).to_pydatetime()
    dres = dl.detect_daily_liquidity(d1s, now=now)
    bsl_ssl = dl._categorize_bsl_ssl(dres, price, d1s)

    h4s = _slice(h4, h4t, t)
    h1s = _slice(h1, h1t, t)

    all_levels = []
    for lvl in bsl_ssl.get('bsl', []):
        all_levels.append({'price': lvl['price'], 'name': lvl.get('name', 'D1'),
                           'source': 'D1', 'side': 'above'})
    for lvl in bsl_ssl.get('ssl', []):
        all_levels.append({'price': lvl['price'], 'name': lvl.get('name', 'D1'),
                           'source': 'D1', 'side': 'below'})

    if len(h4s) > 5:
        sw = detect_swing_points(h4s)
        h4hi = h4s['high'].values.astype(float)
        h4lo = h4s['low'].values.astype(float)
        for sh in sw.get('swing_highs', []):
            if (sh['price'] > price
                    and not any(x > sh['price'] for x in h4hi[sh['index'] + 1:])):
                all_levels.append({'price': sh['price'], 'name': 'H4-SW-HIGH',
                                   'source': 'H4', 'side': 'above'})
        for sl in sw.get('swing_lows', []):
            if (sl['price'] < price
                    and not any(x < sl['price'] for x in h4lo[sl['index'] + 1:])):
                all_levels.append({'price': sl['price'], 'name': 'H4-SW-LOW',
                                   'source': 'H4', 'side': 'below'})

    if len(h1s) > 5 and len(h4s) > 5:
        zones = detect_h1_internal_external(h1s, h4s)
        for b in zones.get('bsl', []):
            if b['price'] > price:
                all_levels.append({'price': b['price'], 'name': 'H1-BSL',
                                   'source': 'H1', 'side': 'above'})
        for s in zones.get('ssl', []):
            if s['price'] < price:
                all_levels.append({'price': s['price'], 'name': 'H1-SSL',
                                   'source': 'H1', 'side': 'below'})

    seen, uniq = set(), []
    for l in all_levels:
        p = round(float(l['price']), 2)
        if p in seen:
            continue
        seen.add(p)
        uniq.append(l)

    bsl = sorted([l for l in uniq if l['side'] == 'above'], key=lambda l: l['price'])
    ssl = sorted([l for l in uniq if l['side'] == 'below'],
                 key=lambda l: l['price'], reverse=True)
    return bsl[:cfg.MAX_LEVELS_PER_SIDE] + ssl[:cfg.MAX_LEVELS_PER_SIDE]


# cache levels by 30-min bucket
_level_cache = {}


def get_levels(i):
    bucket = i // 12
    if bucket not in _level_cache:
        price = float(m5['close'].values[i])
        _level_cache[bucket] = levels_at(m5t[i], price)
    return _level_cache[bucket]


# --------------------------------------------------------------------------
# ATR trail step (M15, same as live TradeManager)
# --------------------------------------------------------------------------
_atr_cache = {}


def _atr14(df):
    if df is None or len(df) < cfg.TRAIL_ATR_PERIOD + 1:
        return 0.0
    high = df['high'].values.astype(float)
    low = df['low'].values.astype(float)
    close = df['close'].values.astype(float)
    trs = []
    for k in range(1, len(df)):
        trs.append(max(high[k] - low[k],
                       abs(high[k] - close[k - 1]),
                       abs(low[k] - close[k - 1])))
    if len(trs) < cfg.TRAIL_ATR_PERIOD:
        return 0.0
    return float(np.mean(trs[-cfg.TRAIL_ATR_PERIOD:]))


def trail_step_at(i):
    idx = int(np.searchsorted(m15t, m5t[i], side='right')) - 1
    if idx not in _atr_cache:
        _atr_cache[idx] = _atr14(m15.iloc[:idx + 1])
    a = _atr_cache[idx]
    if a > 0:
        return max(1.0, round(a * cfg.TRAIL_ATR_MULTIPLIER, 1))
    return cfg.TRAIL_STEP


# --------------------------------------------------------------------------
# Backtest simulation
# --------------------------------------------------------------------------
m5_high = m5['high'].values.astype(float)
m5_low = m5['low'].values.astype(float)
m5_close = m5['close'].values.astype(float)

entries_per_level = {}
active_setups = []
open_trade = None
trades = []
daily_trades = {}
MIN_STOP = 0.10
RESOLVE_BUCKET = None
DBG = {'sweeps': 0, 'ri_eq_i': 0, 'setups': 0, 'engulf_ok': 0, 'vsa_ok': 0,
       'm3_ok': 0, 'm1_refine': 0, 'entries': 0, 'vsa_reasons': {}}

t0 = time.time()
i = start_i
while i < len(m5):
    # ---- resolve open trade first ----
    if open_trade is not None:
        hi, lo = m5_high[i], m5_low[i]

        # --- SPLIT model: TP1 half (1R) + runner half (trail to max RR) ---
        if open_trade.get('split'):
            d = open_trade['direction']
            if open_trade['tp1_result'] is None:
                hit_tp1 = (hi >= open_trade['tp1']) if d == 'buy' else (lo <= open_trade['tp1'])
                hit_sl = (lo <= open_trade['sl']) if d == 'buy' else (hi >= open_trade['sl'])
                if hit_tp1:
                    open_trade['tp1_result'] = 'hit'
                    open_trade['tp1_exit'] = open_trade['tp1']
                    lock = (open_trade['entry'] + open_trade['sl_dist'] * cfg.TP1_LOCK_RR
                            if d == 'buy' else
                            open_trade['entry'] - open_trade['sl_dist'] * cfg.TP1_LOCK_RR)
                    open_trade['runner_sl'] = lock
                    open_trade['locked'] = True
                    open_trade['trail_step'] = trail_step_at(i)
                elif hit_sl:
                    open_trade['tp1_result'] = 'miss'
                    open_trade['runner_result'] = 'sl'
                    open_trade['runner_exit'] = open_trade['sl']
            if open_trade.get('tp1_result') == 'hit' and open_trade['runner_result'] is None:
                ts = open_trade['trail_step']
                hit_tp10 = (hi >= open_trade['tp10']) if d == 'buy' else (lo <= open_trade['tp10'])
                if d == 'buy':
                    nt = hi - ts
                    if nt > open_trade['runner_sl'] + ts * 0.4:
                        open_trade['runner_sl'] = nt
                    runner_hit = lo <= open_trade['runner_sl']
                else:
                    nt = lo + ts
                    if nt < open_trade['runner_sl'] - ts * 0.4:
                        open_trade['runner_sl'] = nt
                    runner_hit = hi >= open_trade['runner_sl']
                if hit_tp10:
                    open_trade['runner_result'] = 'tp10'
                    open_trade['runner_exit'] = open_trade['tp10']
                elif runner_hit:
                    open_trade['runner_result'] = 'trail'
                    open_trade['runner_exit'] = open_trade['runner_sl']
            if open_trade['tp1_result'] is not None and open_trade['runner_result'] is not None:
                e, sd = open_trade['entry'], open_trade['sl_dist']
                tp1_r = 0.5 if open_trade['tp1_result'] == 'hit' else -0.5
                rx = open_trade['runner_exit']
                runner_r = ((rx - e) / sd if d == 'buy' else (e - rx) / sd) * 0.5
                open_trade['runner_r'] = round(runner_r, 3)
                open_trade['r_mult'] = round(tp1_r + runner_r, 3)
                open_trade['result'] = 'closed'
                open_trade['exit'] = rx
                open_trade['exit_idx'] = i
                trades.append(open_trade)
                open_trade = None
                i += 1
                continue
            i += 1
            continue

        if open_trade['direction'] == 'buy':
            if hi >= open_trade['tp']:
                open_trade['result'] = 'tp'; open_trade['exit'] = open_trade['tp']
            elif lo <= open_trade['sl']:
                open_trade['result'] = 'sl'; open_trade['exit'] = open_trade['sl']
        else:
            if lo <= open_trade['tp']:
                open_trade['result'] = 'tp'; open_trade['exit'] = open_trade['tp']
            elif hi >= open_trade['sl']:
                open_trade['result'] = 'sl'; open_trade['exit'] = open_trade['sl']
        if 'result' in open_trade:
            open_trade['exit_idx'] = i
            trades.append(open_trade)
            open_trade = None
            i += 1
            continue

    price_now = m5_close[i]

    # ---- expire / zone-exit setups ----
    kept = []
    for s in active_setups:
        if s['direction'] == 'buy' and price_now < s['sweep_low']:
            continue
        if s['direction'] == 'sell' and price_now > s['sweep_high']:
            continue
        kept.append(s)
    active_setups = kept

    # ---- advance active setups (only when no open trade - max 1 at a time) ----
    for s in active_setups:
        if open_trade is not None:
            break
        if s.get('done'):
            continue
        direction = s['direction']

        # M5 engulfing (not required in 'sweep'/'live' mode)
        if ENTRY_MODE not in ('sweep', 'live') and not s.get('engulf_abs'):
            e5 = scan_for_engulfing(m5.iloc[:i + 1], direction, lookback=2)
            if e5.get('found') and e5['latest']['candle_index'] >= s['sweep_idx']:
                s['engulf_abs'] = e5['latest']['candle_index']
                s['engulf_strength'] = e5['latest'].get('strength', '')
                DBG['engulf_ok'] += 1
            else:
                continue

        if not s.get('vsa_done'):
            s['vsa_done'] = True
            if VSA_ON == 'off':
                s['vsa'] = {'valid': True, 'reason': 'VSA disabled'}
                DBG['vsa_ok'] += 1
            else:
                v_idx = s['sweep_idx'] if VSA_ON == 'sweep' else s['engulf_abs']
                vsa = check_vsa(m5.iloc[:i + 1], direction, index=v_idx)
                if not vsa['valid']:
                    s['vsa_fail'] = vsa['reason']
                    s['done'] = True
                    reason = vsa['reason']
                    key_r = reason.split('(')[0].strip()
                    DBG['vsa_reasons'][key_r] = DBG['vsa_reasons'].get(key_r, 0) + 1
                    continue
                s['vsa'] = vsa
                DBG['vsa_ok'] += 1

        # ---- session + strength filters ----
        hour = pd.Timestamp(m5t[i]).hour
        if SESSION_FILTER == 'london' and not (7 <= hour < 16):
            continue
        if SESSION_FILTER == 'ny' and not (12 <= hour < 21):
            continue
        if STRENGTH_FILTER == 'sm' and s.get('engulf_strength', '') == 'weak':
            continue

        # ---- entry price depends on the entry mode ----
        if ENTRY_MODE == 'full':
            m3idx = int(np.searchsorted(m3t, m5t[i], side='right')) - 1
            if m3idx < 3:
                continue
            e3 = scan_for_engulfing(m3.iloc[:m3idx + 1], direction, lookback=2)
            if not e3.get('found'):
                continue
            e3c = e3['latest']
            if m3t[e3c['candle_index']] < m5t[s['engulf_abs']]:
                continue
            DBG['m3_ok'] += 1
            entry_price = float(e3c['entry_price'])
            entry_tf = 'M3'
        elif ENTRY_MODE == 'auto':
            entry_price = m5_close[i]
            entry_tf = 'M5'
            if len(m1t):
                m1idx = int(np.searchsorted(m1t, m5t[i], side='right')) - 1
                if m1idx >= 2:
                    e1 = scan_for_engulfing(m1.iloc[:m1idx + 1], direction, lookback=2)
                    if e1.get('found'):
                        e1c = e1['latest']
                        if m1t[e1c['candle_index']] >= m5t[s['engulf_abs']]:
                            entry_price = float(e1c['entry_price'])
                            entry_tf = 'M1'
                            DBG['m1_refine'] += 1
            if entry_tf == 'M5':
                m3idx = int(np.searchsorted(m3t, m5t[i], side='right')) - 1
                if m3idx >= 2:
                    e3 = scan_for_engulfing(m3.iloc[:m3idx + 1], direction, lookback=2)
                    if e3.get('found'):
                        e3c = e3['latest']
                        if m3t[e3c['candle_index']] >= m5t[s['engulf_abs']]:
                            entry_price = float(e3c['entry_price'])
                            entry_tf = 'M3'
                            DBG['m3_ok'] += 1
            s['entry_tf'] = entry_tf
        elif ENTRY_MODE == 'm5':
            entry_price = m5_close[i]
            entry_tf = 'M5'
        else:  # sweep / live (no engulfing requirement)
            entry_price = float(s.get('reclaim_price', m5_close[i]))
            entry_tf = 'M5'

        # ---- ENTRY ----
        key = s['level_key']
        if direction == 'buy':
            sl = round(s['sweep_low'] - cfg.SL_SWEEP_BUFFER_PTS, 2)
        else:
            sl = round(s['sweep_high'] + cfg.SL_SWEEP_BUFFER_PTS, 2)
        sl_dist = abs(entry_price - sl)
        if sl_dist < MIN_STOP:
            sl = (entry_price - MIN_STOP if direction == 'buy'
                  else entry_price + MIN_STOP)
            sl_dist = abs(entry_price - sl)

        if SPLIT == 'on':
            open_trade = {
                'split': True,
                'level_key': key,
                'level': s['level']['name'],
                'level_price': s['level']['price'],
                'direction': direction,
                'entry': round(entry_price, 2),
                'sl': sl,
                'tp1': round(entry_price + sl_dist * cfg.TP1_RR if direction == 'buy'
                             else entry_price - sl_dist * cfg.TP1_RR, 2),
                'tp10': round(entry_price + sl_dist * cfg.RUNNER_TP_MAX_RR if direction == 'buy'
                              else entry_price - sl_dist * cfg.RUNNER_TP_MAX_RR, 2),
                'sl_dist': round(sl_dist, 2),
                'entry_time': str(m5t[i]),
                'sweep_time': s['sweep_time'],
                'tf': entry_tf,
                'runner_sl': sl,
                'locked': False,
                'trail_step': cfg.TRAIL_STEP,
                'tp1_result': None,
                'tp1_exit': None,
                'runner_result': None,
                'runner_exit': None,
                'runner_r': None,
            }
        else:
            lvls = get_levels(i)
            if direction == 'buy':
                cands = sorted(p for p in [l['price'] for l in lvls if l['price'] > entry_price])
            else:
                cands = sorted((p for p in [l['price'] for l in lvls if l['price'] < entry_price]),
                               reverse=True)
            tp, rr = None, 0.0
            for c in cands:
                r = abs(c - entry_price) / sl_dist
                if r >= cfg.MIN_RR:
                    tp, rr = c, round(r, 2)
                    break
            if tp is None:
                tp = (entry_price + sl_dist * cfg.MIN_RR if direction == 'buy'
                      else entry_price - sl_dist * cfg.MIN_RR)
                rr = cfg.MIN_RR
            if TP_MAX_RR > 0 and rr > TP_MAX_RR:
                tp = (entry_price + sl_dist * TP_MAX_RR if direction == 'buy'
                      else entry_price - sl_dist * TP_MAX_RR)
                rr = TP_MAX_RR

            open_trade = {
                'level_key': key,
                'level': s['level']['name'],
                'level_price': s['level']['price'],
                'direction': direction,
                'entry': round(entry_price, 2),
                'sl': sl,
                'tp': round(tp, 2),
                'rr': rr,
                'entry_time': str(m5t[i]),
                'sweep_time': s['sweep_time'],
                'sl_dist': round(sl_dist, 2),
                'tf': entry_tf,
            }
        s['done'] = True
        s['entered'] = True
        DBG['entries'] += 1

        entries_per_level[key] = entries_per_level.get(key, 0) + 1
        day = str(m5t[i])[:10]
        daily_trades[day] = daily_trades.get(day, 0) + 1
        break  # one trade at a time

    # ---- scan for new setups ----
    if open_trade is None:
        day = str(m5t[i])[:10]
        if daily_trades.get(day, 0) < cfg.MAX_DAILY_TRADES:
            lvls = get_levels(i)
            for l in lvls:
                key = round(float(l['price']), 2)
                if entries_per_level.get(key, 0) >= cfg.MAX_ENTRIES_PER_LEVEL:
                    continue
                if any(s['level_key'] == key and not s.get('done') for s in active_setups):
                    continue
                sweep = detect_sweep_reclaim(m5.iloc[:i + 1], l['price'], l['side'])
                if sweep and sweep.get('swept'):
                    DBG['sweeps'] += 1
                    si = sweep['sweep_index']
                    ri = sweep.get('reclaim_index', -1)
                    if ri == i:
                        DBG['ri_eq_i'] += 1
                        DBG['setups'] += 1
                        active_setups.append({
                            'level_key': key,
                            'level': l,
                            'direction': sweep['direction'],
                            'sweep_idx': si,
                            'reclaim_idx': ri,
                            'sweep_low': float(m5_low[si]),
                            'sweep_high': float(m5_high[si]),
                            'reclaim_price': float(m5_close[i]),
                            'sweep_time': sweep['sweep_time'],
                            'created_idx': i,
                        })

            # ---- engulfing chain setups (live mode): engulf -> sweep of that
            #      engulfing candle -> engulf again, on M5 / M3 / M1 at the level ----
            if ENTRY_MODE == 'live':
                for l in lvls:
                    key = round(float(l['price']), 2)
                    if entries_per_level.get(key, 0) >= cfg.MAX_ENTRIES_PER_LEVEL:
                        continue
                    if any(s['level_key'] == key and not s.get('done') for s in active_setups):
                        continue
                    direction = 'buy' if l['side'] == 'below' else 'sell'
                    for tf_name, df, tarr in (('M5', m5, m5t), ('M3', m3, m3t), ('M1', m1, m1t)):
                        if len(df) == 0:
                            continue
                        j = int(np.searchsorted(tarr, m5t[i], side='right'))
                        if j < cfg.ENGULF_CHAIN_LOOKBACK:
                            continue
                        w = df.iloc[j - cfg.ENGULF_CHAIN_LOOKBACK:j]
                        if direction == 'buy':
                            reached = float(w['low'].min()) <= l['price']
                        else:
                            reached = float(w['high'].max()) >= l['price']
                        if not reached:
                            continue
                        chain = detect_engulf_chain(w, direction,
                                                    cfg.ENGULF_CHAIN_LOOKBACK)
                        if not chain.get('detected'):
                            continue
                        DBG['setups'] += 1
                        setup = {
                            'level_key': key,
                            'level': l,
                            'direction': direction,
                            'sweep_idx': i,
                            'sweep_time': chain['sweep_time'],
                            'created_idx': i,
                            'trigger': f'engulf_{tf_name}',
                        }
                        if direction == 'buy':
                            setup['sweep_low'] = chain['sweep_extreme']
                        else:
                            setup['sweep_high'] = chain['sweep_extreme']
                        active_setups.append(setup)
                        break

    i += 1

# close any still-open trade
if open_trade is not None:
    open_trade['result'] = 'open'
    open_trade['exit'] = m5_close[-1]
    open_trade['exit_idx'] = len(m5) - 1
    if open_trade.get('split'):
        d = open_trade['direction']
        e, sd = open_trade['entry'], open_trade['sl_dist']
        if open_trade['tp1_result'] is None:
            open_trade['tp1_result'] = 'open'
        tp1_r = 0.5 if open_trade['tp1_result'] == 'hit' \
            else ((m5_close[-1] - e) / sd if d == 'buy' else (e - m5_close[-1]) / sd) * 0.5
        if open_trade['runner_result'] is None:
            open_trade['runner_result'] = 'open'
            open_trade['runner_exit'] = m5_close[-1]
        rx = open_trade['runner_exit']
        runner_r = ((rx - e) / sd if d == 'buy' else (e - rx) / sd) * 0.5
        open_trade['r_mult'] = round(tp1_r + runner_r, 3)
    trades.append(open_trade)

elapsed = time.time() - t0
print('CONFIG: ENTRY=%s VSA_ON=%s VOL_MULT=%.2f WICK=%.2f SESS=%s STR=%s TPMAX=%.1f SPLIT=%s | DBG: %s'
      % (ENTRY_MODE, VSA_ON, VSA_VOL_MULT, VSA_WICK, SESSION_FILTER, STRENGTH_FILTER,
         TP_MAX_RR, SPLIT, DBG))

# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------
print(f'Simulation time: {elapsed:.1f}s\n')
print(f'{"#":>2} {"tf":<4} {"dir":<4} {"level":<10} {"entry_time":<17} {"entry":>7} {"SL":>7} '
      f'{"TP":>7} {"R":>4} {"result":<7} {"exit":>7} {"R-mult":>6}')
total_r = 0.0
total_r_net = 0.0
wins = losses = 0
for k, t in enumerate(trades):
    if t.get('split'):
        r_mult = t['r_mult']
        if t['result'] == 'closed' and r_mult > 0:
            wins += 1
        elif t['result'] == 'closed':
            losses += 1
        tp_show = t['tp1']
        res_show = f"{t['runner_result']}" + ('' if t['tp1_result'] == 'hit' else ' noTP1')
    elif t['result'] == 'tp':
        r_mult = t['rr']
        wins += 1
        tp_show = t['tp']
        res_show = 'tp'
    elif t['result'] == 'sl':
        r_mult = -1.0
        losses += 1
        tp_show = t['tp']
        res_show = 'sl'
    else:
        r_mult = (t['exit'] - t['entry']) / t['sl_dist'] if t['direction'] == 'buy' \
            else (t['entry'] - t['exit']) / t['sl_dist']
        tp_show = t.get('tp', t.get('tp1', 0))
        res_show = 'open'
    sd = t.get('sl_dist', 0)
    cost_eff = (COST_PTS * 0.01) / sd if sd and sd > 0 else 0.0
    r_net = r_mult - cost_eff
    total_r += r_mult
    total_r_net += r_net
    print(f'{k + 1:>2} {t.get("tf", "-"):<4} {t["direction"][:3]:<4} {t["level"]:<10} {t["entry_time"]:<17} '
          f'{t["entry"]:>7} {t["sl"]:>7} {tp_show:>7} {"-":>4} '
          f'{res_show:<7} {t["exit"]:>7} {r_mult:>+6.2f} {r_net:>+6.2f}')

closed = [t for t in trades if t['result'] in ('tp', 'sl', 'closed')]
if trades:
    wr = wins / len(closed) * 100 if closed else 0
    print(f'\nTrades: {len(trades)}  Closed: {len(closed)}  Open: {len(trades) - len(closed)}')
    print(f'Wins: {wins}  Losses: {losses}  Win rate (closed): {wr:.1f}%')
    print(f'Total R: {total_r:+.2f} (gross) | {total_r_net:+.2f} (net, {COST_PTS:.0f}pt cost)')
    print(f'Avg R/trade: {total_r / len(trades):+.2f} (gross) | {total_r_net / len(trades):+.2f} (net)')
    buy = [t for t in trades if t['direction'] == 'buy']
    sell = [t for t in trades if t['direction'] == 'sell']
    print(f'BUY: {len(buy)}  SELL: {len(sell)}')
    if SPLIT == 'on':
        tp1_hits = sum(1 for t in trades if t.get('split') and t['tp1_result'] == 'hit')
        runner_win = sum(1 for t in trades if t.get('split') and t['result'] == 'closed'
                         and t['runner_r'] and t['runner_r'] > 0)
        tr = [t for t in trades if t.get('split') and t['runner_result'] == 'trail']
        print(f'TP1 hit rate ({cfg.TP1_RR}R close): {tp1_hits}/{len(trades)} = {tp1_hits / len(trades) * 100:.1f}%')
        print(f'Runner profit (trail exit): {len(tr)}/{len(trades)} '
              f'avg runner R: {np.mean([t["runner_r"] for t in tr]) if tr else 0:+.3f}')
        print(f'Runner closed in profit: {runner_win}/{len(trades)}')

mt5.shutdown()
