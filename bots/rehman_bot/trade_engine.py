"""
trade_engine.py  Step 9, 10, 11: Entry Rules, Stop Loss, Take Profit
Handles trade execution via MetaTrader 5.
"""

import MetaTrader5 as mt5
import math
from datetime import datetime, timezone
from config_new import (
    MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH,
    SYMBOL, MAGIC_NUMBER, RISK_PERCENT,
    MIN_LOT, MAX_LOT, LOT_STEP, SL_BUFFER_PTS, MIN_RR,
    SPREAD_MAX, MAX_OPEN_TRADES, MAX_DAILY_TRADES,
    MAX_DAILY_LOSS_USD, ALLOW_LIVE_STANDALONE
)

# Execution guard (NexFox Engine worker). None = standalone mode.
# A guard exposes before_order(request, direction) -> {"allowed", "reason", "request", "dry_run", ...}
# and after_order(decision, result). It is the only place orders are authorised; strategy logic is unchanged.
EXECUTION_GUARD = None


def set_execution_guard(guard):
    global EXECUTION_GUARD
    EXECUTION_GUARD = guard


def connect():
    """Connect to MetaTrader 5 (standalone mode; the platform worker connects itself)."""
    if not mt5.initialize(path=MT5_PATH):
        print(f"MT5 init failed: {mt5.last_error()}")
        return False

    authorized = mt5.login(
        login=MT5_LOGIN,
        password=MT5_PASSWORD,
        server=MT5_SERVER,
    )
    if not authorized:
        print(f"MT5 login failed: {mt5.last_error()}")
        return False

    info = mt5.account_info()
    if info.trade_mode == mt5.ACCOUNT_TRADE_MODE_REAL and not ALLOW_LIVE_STANDALONE:
        print("Refusing to trade a REAL account in standalone mode. Set REHMAN_ALLOW_LIVE=true to override.")
        mt5.shutdown()
        return False

    print(f"MT5 connected: {info.login}")
    return True


def disconnect():
    """Disconnect from MT5."""
    mt5.shutdown()


def get_symbol_info():
    """Get current symbol info."""
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        mt5.symbol_select(SYMBOL, True)
        info = mt5.symbol_info(SYMBOL)
    return info


def get_current_price() -> dict:
    """Get current bid/ask/mid price."""
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        return {'error': 'No tick data'}

    info = get_symbol_info()
    point = info.point if info else 0.01

    return {
        'bid': tick.bid,
        'ask': tick.ask,
        'mid': round((tick.bid + tick.ask) / 2, 5),
        'spread': round((tick.ask - tick.bid) / point, 1) if point > 0 else 0,
        'point': point,
    }


def calculate_lot_size(sl_distance_pts: float, price: float) -> float:
    """Calculate lot size based on risk % and SL distance."""
    account = mt5.account_info()
    if account is None:
        return MIN_LOT

    balance = account.balance
    # Dynamic risk: balance ka RISK_PERCENT% - koi fixed USD cap nahi.
    # SL tight/calculated hota hai (sweep extreme + buffer), toh dollar
    # risk seedha lot size se nikalta hai.
    risk_amount = balance * (RISK_PERCENT / 100)

    info = get_symbol_info()
    if info is None:
        return MIN_LOT

    tick_value = info.trade_tick_value
    tick_size = info.trade_tick_size

    if tick_value == 0 or tick_size == 0:
        return MIN_LOT

    sl_distance_price = sl_distance_pts * info.point
    risk_per_lot = (sl_distance_price / tick_size) * tick_value

    if risk_per_lot <= 0:
        return MIN_LOT

    lots = risk_amount / risk_per_lot
    lots = max(MIN_LOT, min(MAX_LOT, lots))
    # floor (round down) so actual lot-based risk budget se upar na jaye
    lots = math.floor(lots / LOT_STEP) * LOT_STEP
    lots = round(lots, 2)

    return lots


def has_open_positions() -> bool:
    """Check if we already have open positions for this symbol."""
    positions = mt5.positions_get(symbol=SYMBOL)
    if positions:
        our_positions = [p for p in positions if p.magic == MAGIC_NUMBER]
        return len(our_positions) >= MAX_OPEN_TRADES
    return False


def get_open_positions() -> list:
    """Get our open positions."""
    positions = mt5.positions_get(symbol=SYMBOL)
    if positions is None:
        return []
    return [p for p in positions if p.magic == MAGIC_NUMBER]


def get_daily_trade_count() -> int:
    """Count setups opened today (one per split setup - TP1 entry deal)."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    deals = mt5.history_deals_get(today_start, now, group=f"*{SYMBOL}*")
    if deals is None:
        return 0

    our_deals = [d for d in deals if d.magic == MAGIC_NUMBER and (d.type == mt5.DEAL_TYPE_BUY or d.type == mt5.DEAL_TYPE_SELL)]
    entry_deals = [d for d in our_deals
                   if d.entry == mt5.DEAL_ENTRY_IN
                   and d.comment and d.comment.startswith('TP1_')]
    return len(entry_deals)


def get_daily_pnl() -> float:
    """Calculate daily PnL."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    deals = mt5.history_deals_get(today_start, now, group=f"*{SYMBOL}*")
    if deals is None:
        return 0.0

    total = 0.0
    for d in deals:
        if d.magic == MAGIC_NUMBER:
            total += d.profit + d.swap + d.commission
    return round(total, 2)


def validate_setup(direction: str, sl: float, tp: float, entry_price: float,
                   min_rr: float = None) -> dict:
    """Validate trade setup before execution."""
    if min_rr is None:
        min_rr = MIN_RR
    info = get_symbol_info()
    point = info.point if info else 0.01
    tick = mt5.symbol_info_tick(SYMBOL)

    if tick is None:
        return {'valid': False, 'reason': 'No tick data'}

    spread = (tick.ask - tick.bid) / point
    if spread > SPREAD_MAX:
        return {'valid': False, 'reason': f'Spread too high: {spread:.1f}'}

    if has_open_positions():
        return {'valid': False, 'reason': 'Already at max open positions'}

    if get_daily_trade_count() >= MAX_DAILY_TRADES:
        return {'valid': False, 'reason': 'Max daily trades reached'}

    daily_pnl = get_daily_pnl()
    if daily_pnl <= -MAX_DAILY_LOSS_USD:
        return {'valid': False, 'reason': f'Daily loss limit: ${daily_pnl}'}

    sl_dist = abs(entry_price - sl)
    tp_dist = abs(tp - entry_price)

    min_stop_price = max(info.trade_stops_level * point, point * 10) if info else point * 10
    if sl_dist < min_stop_price:
        return {'valid': False, 'reason': f'SL too tight: {sl_dist:.2f} < min {min_stop_price:.2f}'}

    rr_ratio = tp_dist / sl_dist if sl_dist > 0 else 0
    if rr_ratio < min_rr:
        return {'valid': False, 'reason': f'RR too low: 1:{rr_ratio:.1f}'}

    return {'valid': True, 'reason': 'OK', 'rr_ratio': round(rr_ratio, 2)}


def open_trade(direction: str, entry_price: float, sl: float,
               tp: float, lot_size: float = None, comment: str = '',
               min_rr: float = None) -> dict:
    """
    Open a market order.
    direction: 'buy' or 'sell'
    """
    info = get_symbol_info()
    if info is None:
        return {'success': False, 'error': 'No symbol info'}

    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        return {'success': False, 'error': 'No tick data'}

    sl_dist = abs(entry_price - sl)
    if lot_size is None:
        lot_size = calculate_lot_size(sl_dist / info.point, entry_price)

    validation = validate_setup(direction, sl, tp, entry_price, min_rr=min_rr)
    if not validation['valid']:
        return {'success': False, 'error': validation['reason']}

    if direction == 'buy':
        order_type = mt5.ORDER_TYPE_BUY
        exec_price = tick.ask
    else:
        order_type = mt5.ORDER_TYPE_SELL
        exec_price = tick.bid

    sl_pts = round(sl_dist / info.point, 0)
    tp_pts = round(abs(tp - entry_price) / info.point, 0)

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": lot_size,
        "type": order_type,
        "price": exec_price,
        "sl": sl,
        "tp": tp,
        "deviation": 30,
        "magic": MAGIC_NUMBER,
        "comment": comment or f"LE_{direction[:3].upper()}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    decision = None
    if EXECUTION_GUARD is not None:
        decision = EXECUTION_GUARD.before_order(request, direction)
        if not decision.get('allowed'):
            return {'success': False, 'error': decision.get('reason', 'order refused by platform')}
        request = decision.get('request', request)
        if decision.get('dry_run'):
            EXECUTION_GUARD.after_order(decision, None)
            return {'success': True, 'dry_run': True, 'ticket': 0, 'direction': direction, 'entry': exec_price,
                    'sl': sl, 'tp': tp, 'lots': lot_size, 'sl_pts': sl_pts, 'tp_pts': tp_pts,
                    'rr_ratio': round(tp_pts / sl_pts, 2) if sl_pts > 0 else 0, 'risk_usd': 0, 'balance': 0,
                    'symbol': SYMBOL}

    result = mt5.order_send(request)
    if EXECUTION_GUARD is not None:
        EXECUTION_GUARD.after_order(decision, result)

    if result is None:
        return {'success': False, 'error': str(mt5.last_error())}

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        return {'success': False, 'error': f'Order failed: {result.comment}'}

    account = mt5.account_info()
    risk_usd = lot_size * sl_dist * info.trade_tick_value / info.trade_tick_size if info.trade_tick_size > 0 else 0

    return {
        'success': True,
        'ticket': result.order,
        'direction': direction,
        'entry': exec_price,
        'sl': sl,
        'tp': tp,
        'lots': lot_size,
        'sl_pts': sl_pts,
        'tp_pts': tp_pts,
        'rr_ratio': round(tp_pts / sl_pts, 2) if sl_pts > 0 else 0,
        'risk_usd': round(risk_usd, 2),
        'balance': round(account.balance, 2) if account else 0,
        'symbol': SYMBOL,
    }
