"""
simple_bot.py  XAUUSD Liquidity + Sweep Bot (Simple)

Only the rules below. No Order Blocks / FVG / Premium-Discount / SMT /
Kill Zones / Daily Bias / Engulfing / VSA.

Timeframes: D1, H4, H1 (levels), M5 / M3 / M1 (entries).

  Step 1  D1 : mark Previous Day High / Previous Day Low
  Step 2  D1 : mark Equal Highs / Equal Lows (not yet swept)
  Step 3  H4 : mark Major Swing High / Major Swing Low (major liquidity)
  Step 4  H1 : mark Internal / External Liquidity (important swings)
  Step 5      do nothing until price reaches a marked level
  Step 6      wait for liquidity sweep (BUY: below then back above /
               SELL: above then back below)
  Step 6b      when price reaches a level: engulfing chain entry on
               M5 / M3 / M1 - engulfing -> that engulfing candle swept
               -> engulfing again -> entry
  Step 7      entry at current price once swept + reclaimed (or chain)
  Step 8      SL below/above the liquidity sweep extreme
  Step 9      TP = SL distance * TP_MAX_RR (fixed 1:2)

Rules:
  - Trade only at a marked liquidity level.
  - Max 2 entries on the same liquidity level.
  - If price leaves the zone without confirmation, cancel the setup.
  - No setup timeout (setup stays until zone exit / level used up).
  - Continuously monitors throughout the day.
"""

import signal
import sys
import time

import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timezone

import config_new as cfg
from daily_liquidity import analyze as daily_analysis
from swing_detector import detect_swing_points
from h1_liquidity import detect_h1_internal_external
from level_tracker import LevelTracker

from trade_engine import (
    connect, disconnect, get_current_price, open_trade,
    get_open_positions, get_daily_trade_count, get_symbol_info,
    calculate_lot_size,
)
from trade_manager import TradeManager
import notifier_new as notifier
from logger_new import log

_running = True
_print_counter = 0
_tracker = LevelTracker()
_manager = TradeManager()


def _shutdown(sig, frame):
    global _running
    print("\nShutdown signal received. Stopping...")
    _running = False


signal.signal(signal.SIGINT, _shutdown)
signal.signal(signal.SIGTERM, _shutdown)


def fetch_data(symbol: str) -> dict:
    """Fetch H4 / H1 / M5 / M1 OHLCV data."""
    timeframes = {
        'H4': (mt5.TIMEFRAME_H4, 300),
        'H1': (mt5.TIMEFRAME_H1, 300),
        'M5': (mt5.TIMEFRAME_M5, 300),
        'M3': (mt5.TIMEFRAME_M3, 300),
        'M1': (mt5.TIMEFRAME_M1, 200),
    }

    data = {}
    for name, (tf, count) in timeframes.items():
        if tf is None:
            data[name] = pd.DataFrame()
            continue
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
        if rates is not None and len(rates) > 0:
            df = pd.DataFrame(rates)
            if 'volume' not in df.columns and 'tick_volume' in df.columns:
                df = df.rename(columns={'tick_volume': 'volume'})
            df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
            data[name] = df
        else:
            data[name] = pd.DataFrame()

    return data


def collect_levels(current_price: float) -> list:
    """
    Collect all marked liquidity levels (Steps 1-4).
    Returns a flat list of {price, name, source, type, side}.
    side: 'above' = BSL (sweep -> SELL), 'below' = SSL (sweep -> BUY).
    """
    all_levels = []

    daily = daily_analysis(cfg.SYMBOL, current_price)
    if 'error' not in daily:
        for lvl in daily.get('bsl', []):
            all_levels.append({
                'price': lvl['price'],
                'name': lvl.get('name', 'D1-BSL'),
                'source': 'D1',
                'type': lvl.get('name', 'd1'),
                'side': 'above',
            })
        for lvl in daily.get('ssl', []):
            all_levels.append({
                'price': lvl['price'],
                'name': lvl.get('name', 'D1-SSL'),
                'source': 'D1',
                'type': lvl.get('name', 'd1'),
                'side': 'below',
            })

    h4 = _DATA.get('H4')
    if h4 is not None and not h4.empty:
        swings = detect_swing_points(h4)
        h4_highs = h4['high'].values.astype(float)
        h4_lows = h4['low'].values.astype(float)
        for sh in swings.get('swing_highs', []):
            # valid only if price has NOT swept it since it was formed
            if (sh['price'] > current_price
                    and not any(h > sh['price'] for h in h4_highs[sh['index'] + 1:])):
                all_levels.append({
                    'price': sh['price'], 'name': 'H4-SW-HIGH',
                    'source': 'H4', 'type': 'swing_high', 'side': 'above',
                })
        for sl in swings.get('swing_lows', []):
            if (sl['price'] < current_price
                    and not any(l < sl['price'] for l in h4_lows[sl['index'] + 1:])):
                all_levels.append({
                    'price': sl['price'], 'name': 'H4-SW-LOW',
                    'source': 'H4', 'type': 'swing_low', 'side': 'below',
                })

    h1 = _DATA.get('H1')
    if h1 is not None and not h1.empty:
        zones = detect_h1_internal_external(h1, h4)
        for b in zones.get('bsl', []):
            if b['price'] > current_price:
                all_levels.append({
                    'price': b['price'],
                    'name': f"H1-{b['category'].upper()}",
                    'source': 'H1',
                    'type': f"h1_{b['category']}",
                    'side': 'above',
                })
        for s in zones.get('ssl', []):
            if s['price'] < current_price:
                all_levels.append({
                    'price': s['price'],
                    'name': f"H1-{s['category'].upper()}",
                    'source': 'H1',
                    'type': f"h1_{s['category']}",
                    'side': 'below',
                })

    # Deduplicate by price: same level can come from D1 + H4 + H1.
    seen_prices = set()
    uniq = []
    for l in all_levels:
        p = round(float(l['price']), 2)
        if p in seen_prices:
            continue
        seen_prices.add(p)
        uniq.append(l)

    # Keep only the nearest N levels per side. When a level gets swept /
    # finished, the next nearest one automatically takes its place (ladder).
    bsl = [l for l in uniq if l['side'] == 'above']
    ssl = [l for l in uniq if l['side'] == 'below']
    bsl.sort(key=lambda l: l['price'])
    ssl.sort(key=lambda l: l['price'], reverse=True)
    all_levels = bsl[:cfg.MAX_LEVELS_PER_SIDE] + ssl[:cfg.MAX_LEVELS_PER_SIDE]
    return all_levels


def print_levels(all_levels: list, current_price: float) -> None:
    """Print all marked liquidity levels (D1 / H4 / H1)."""
    bsl = [l for l in all_levels if l['side'] == 'above']
    ssl = [l for l in all_levels if l['side'] == 'below']
    bsl.sort(key=lambda l: l['price'])
    ssl.sort(key=lambda l: l['price'], reverse=True)

    log("-- LIQUIDITY LEVELS --", "INFO")
    log(f"  Price: {current_price:.2f} | total BSL: {len(bsl)} | SSL: {len(ssl)}", "INFO")
    log("  BUY SIDE (BSL - SELL target):", "INFO")
    if bsl:
        for l in bsl:
            log(f"    {l['name']:<12} @ {l['price']:<9} "
                f"dist {l['price'] - current_price:>+8.1f}  [{l['source']}]", "INFO")
    else:
        log("    (none above)", "INFO")
    log("  SELL SIDE (SSL - BUY target):", "INFO")
    if ssl:
        for l in ssl:
            log(f"    {l['name']:<12} @ {l['price']:<9} "
                f"dist {l['price'] - current_price:>+8.1f}  [{l['source']}]", "INFO")
    else:
        log("    (none below)", "INFO")
    log("-- END LIQUIDITY --", "INFO")


def _reclaim_index(df_m5, reclaim_time: str) -> int:
    """Locate the reclaim candle in the current M5 data by its timestamp."""
    if df_m5 is None or df_m5.empty:
        return -1
    times = [str(t) for t in df_m5['time'].values]
    for i in range(len(times) - 1, -1, -1):
        if times[i] == reclaim_time:
            return i
    return -1


def process_setups(current_price: float) -> None:
    """
    Entry on liquidity sweep + reclaim (M5) or engulfing chain
    (engulf -> sweep of engulfing candle -> engulf, on M5/M3/M1).
    TP = fixed SL-distance * TP_MAX_RR.
    """
    for setup in _tracker.active_setups():
        _execute_entry(setup, current_price)


def _execute_entry(setup: dict, current_price: float) -> None:
    """Two trades: TP1 trade (1R, broker TP) + runner (trail -> max RR)."""
    direction = setup['direction']
    lvl = setup['level']
    tag = f"{lvl['name']}@{lvl['price']}"

    info = get_symbol_info()
    point = info.point if info else 0.01
    min_stop = max(info.trade_stops_level * point, point * 10) if info else point * 10

    if direction == 'buy':
        sl = round(setup['sweep_low'] - cfg.SL_SWEEP_BUFFER_PTS, 2)
    else:
        sl = round(setup['sweep_high'] + cfg.SL_SWEEP_BUFFER_PTS, 2)

    sl_dist = abs(current_price - sl)
    if sl_dist < min_stop:
        sl = round(current_price - min_stop, 2) if direction == 'buy' \
            else round(current_price + min_stop, 2)
        sl_dist = abs(current_price - sl)

    tp1 = (current_price + sl_dist * cfg.TP1_RR) if direction == 'buy' \
        else (current_price - sl_dist * cfg.TP1_RR)
    runner_tp = (current_price + sl_dist * cfg.RUNNER_TP_MAX_RR) if direction == 'buy' \
        else (current_price - sl_dist * cfg.RUNNER_TP_MAX_RR)
    tp1, runner_tp = round(tp1, 2), round(runner_tp, 2)

    total_lot = calculate_lot_size(sl_dist / point, current_price)
    lot_a = round(total_lot / 2, 2)
    lot_b = round(total_lot - lot_a, 2)
    if lot_a < 0.01:
        lot_a = 0.01
    if lot_b < 0.01:
        lot_b = 0.01

    log(f"SETUP READY:", "SIGNAL")
    log(f"  Level: {tag} ({lvl['source']}) | Direction: {direction.upper()}", "SIGNAL")
    log(f"  Sweep: {setup['sweep_time']} | Market: {current_price:.2f}", "SIGNAL")
    log(f"  SL: {sl:.2f} ({sl_dist:.1f} pts) | TP1: {tp1:.2f} | Runner trail -> max RR", "SIGNAL")
    log(f"  Lots: {total_lot:.2f} -> {lot_a:.2f} @TP1 + {lot_b:.2f} runner", "SIGNAL")

    r1 = open_trade(direction, current_price, sl, tp1,
                    lot_size=lot_a, comment=f"TP1_LQ_{lvl['source']}_{direction}",
                    min_rr=cfg.TP1_RR)
    r2 = open_trade(direction, current_price, sl, runner_tp,
                    lot_size=lot_b, comment=f"TP2_LQ_{lvl['source']}_{direction}",
                    min_rr=cfg.TP_MAX_RR)

    entered = False
    if r1['success']:
        entered = True
        log(f"TP1 TRADE #{r1['ticket']} {direction.upper()} "
            f"@ {r1['entry']} | SL {r1['sl']} | TP {r1['tp']} "
            f"| Lots {r1['lots']} | RR 1:{r1['rr_ratio']}", "TRADE")
        notifier.alert_trade_opened(r1)
    else:
        log(f"TP1 trade failed: {r1.get('error', 'Unknown')}", "ERROR")

    if r2['success']:
        entered = True
        log(f"TP2 RUNNER #{r2['ticket']} {direction.upper()} "
            f"@ {r2['entry']} | SL {r2['sl']} | TP {r2['tp']} "
            f"| Lots {r2['lots']} | RR 1:{r2['rr_ratio']}", "TRADE")
        notifier.alert_trade_opened(r2)
    else:
        log(f"TP2 runner failed: {r2.get('error', 'Unknown')}", "ERROR")

    if entered:
        _tracker.on_trade_entered(setup)


def scan_once() -> None:
    """One complete scan cycle. Trades 24h except weekends (Sat/Sun UTC off)."""
    global _print_counter

    if datetime.now(timezone.utc).weekday() >= 5:
        return

    _print_counter += 1
    log("--- Starting Scan ---", "SCAN")

    positions = get_open_positions()
    if positions:
        _manager.manage_positions(log)
        return

    daily_trades = get_daily_trade_count()
    if daily_trades >= cfg.MAX_DAILY_TRADES:
        log(f"Daily trade limit reached: {daily_trades}/{cfg.MAX_DAILY_TRADES}", "WAIT")
        return

    price_data = get_current_price()
    if 'error' in price_data:
        log(f"Price fetch error: {price_data['error']}", "ERROR")
        return

    current_price = price_data['mid']
    spread = price_data['spread']
    log(f"XAUUSD @ {current_price:.2f} | Spread: {spread:.1f}", "INFO")

    if spread > cfg.SPREAD_MAX:
        log(f"Spread too high: {spread:.1f} > {cfg.SPREAD_MAX}", "WAIT")
        return

    global _DATA
    _DATA = fetch_data(cfg.SYMBOL)
    if _DATA.get('M5') is None or _DATA['M5'].empty:
        log("Incomplete data - skipping", "ERROR")
        return

    all_levels = collect_levels(current_price)
    _tracker.refresh_levels(all_levels, current_price)
    log(f"Marked liquidity levels: {len(all_levels)} "
        f"({_tracker_active_count()} tracked)", "INFO")

    print_levels(all_levels, current_price)

    cancelled = _tracker.check_zone_exit(current_price)
    for s in cancelled:
        log(f"SETUP CANCELLED {s['level']['name']}@{s['level']['price']} "
            f"- price left the zone without confirmation", "WAIT")

    new_setups = _tracker.scan_setups(_DATA['M5'], _DATA.get('M3'),
                                      _DATA.get('M1'))
    for s in new_setups:
        log(f"SETUP: {s['level']['name']}@{s['level']['price']} "
            f"{s['direction'].upper()} [{s.get('trigger', 'sweep_reclaim')}] "
            f"({s['sweep_time']})", "SIGNAL")

    if _tracker.active_setups():
        process_setups(current_price)
    else:
        log("No active setup - waiting for price to reach a marked level", "WAIT")


def _tracker_active_count() -> int:
    return len(_tracker._levels)


def run_bot() -> None:
    """Main loop - monitors continuously throughout the day."""
    print("=" * 60)
    print("  XAUUSD Liquidity + Sweep Bot (Simple)")
    print("  D1 / H4 / H1 | TP = SL * TP_MAX_RR")
    print("=" * 60)
    print()

    if not connect():
        print("Failed to connect to MT5. Exiting.")
        sys.exit(1)

    log("Bot started - connected to MT5", "INFO")
    log(f"Symbol: {cfg.SYMBOL} | Magic: {cfg.MAGIC_NUMBER}", "INFO")
    log(f"Risk: {cfg.RISK_PERCENT}% (dynamic by lot size)", "INFO")
    log(f"Max entries per level: {cfg.MAX_ENTRIES_PER_LEVEL} | "
        f"Min RR: 1:{cfg.MIN_RR} | Scan: every {cfg.SCAN_INTERVAL_SEC}s", "INFO")
    log("Hours: 24h (Sat/Sun off)", "INFO")
    print()

    try:
        while _running:
            try:
                scan_once()
            except Exception as e:
                log(f"Scan error: {e}", "ERROR")
            for _ in range(int(cfg.SCAN_INTERVAL_SEC * 10)):
                if not _running:
                    break
                time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        log("Bot shutting down...", "INFO")
        disconnect()
        print("Bot stopped.")


_DATA = {}


if __name__ == '__main__':
    run_bot()
