"""
trade_manager.py  Institutional Trade Management (Bot folder style)
BE at RR-based level + ATR-based Trailing + Structure Exit

Flow:
1. Trade opens  original SL intact
2. Price hits BREAKEVEN_AT_RR  SL moves to entry + buffer (locks profit)
3. Price hits TP1_RR  partial close 50% (0.01 lot), trail remaining runner
4. After TP1  Trail runner with ATR-based step until exited
5. Structure exit if price moves 1.5x SL against us
"""

import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timezone
import config_new as cfg


class TradeManager:
    def __init__(self):
        self._be_done = set()
        self._tp1_done = set()
        self._trail_sl = {}
        self._original_sl_dist = {}

    def manage_positions(self, log_func=None):
        """Main entry point. Call every scan cycle."""
        if log_func is None:
            log_func = lambda msg, level="INFO": print(msg)

        positions = self._get_our_positions()
        if not positions:
            return

        tick = self._get_tick()
        if not tick:
            return

        price = tick['bid']
        trail_step = self._get_trail_step()

        for pos in positions:
            ticket = pos.ticket
            entry = pos.price_open
            sl = pos.sl
            direction = 'buy' if pos.type == mt5.ORDER_TYPE_BUY else 'sell'
            lots = pos.volume

            if sl == 0 or entry == 0:
                continue

            # Split-model: TP1 trade broker TP pe khud close hoti hai - manage na karein
            if pos.comment and pos.comment.startswith("TP1_"):
                continue

            sl_dist = abs(entry - sl)
            if sl_dist < 0.5:
                continue

            if ticket not in self._original_sl_dist:
                self._original_sl_dist[ticket] = sl_dist
            orig_sl_dist = self._original_sl_dist[ticket]

            #  Structure Exit 
            self._check_structure_exit(ticket, direction, price, entry, sl_dist, log_func)

            tp1_price = (entry + orig_sl_dist * cfg.TP1_RR) if direction == 'buy' \
                        else (entry - orig_sl_dist * cfg.TP1_RR)

            #  TP1: 1:1 RR pe runner SL ko +0.5R lock + trail runner
            if ticket not in self._tp1_done:
                at_tp1 = (direction == 'buy' and price >= tp1_price) or \
                         (direction == 'sell' and price <= tp1_price)

                if at_tp1:
                    self._tp1_done.add(ticket)

                    # Runner ko +0.5R pe lock karo (entry BE nahi - wapasi pe bhi profit)
                    lock_sl = (entry + orig_sl_dist * cfg.TP1_LOCK_RR) if direction == 'buy' \
                              else (entry - orig_sl_dist * cfg.TP1_LOCK_RR)
                    cur = self._trail_sl.get(ticket, sl)
                    if self._is_better_sl(cur, lock_sl, direction):
                        if self._modify_sl(ticket, lock_sl, pos.tp):
                            self._trail_sl[ticket] = lock_sl
                            log_func(
                                f"TP1 LOCK #{ticket} {direction.upper()} | "
                                f"SL: {cur:.2f}  {lock_sl:.2f} | "
                                f"Price: {price:.2f} >= TP1: {tp1_price:.2f}",
                                "TRADE"
                            )

                    # 0.01 partial close (runner ko TP1 pe close NAI karna)
                    partial = round(lots * cfg.TP1_CLOSE_PCT, 2)
                    if partial > 0 and partial < lots:
                        self._close_partial(ticket, partial)
                        log_func(
                            f"TP1 PARTIAL #{ticket}: {partial} lots closed | "
                            f"Runner: {round(lots - partial, 2)} lots",
                            "TRADE"
                        )

            #  TRAILING (after TP1 partial close) 
            if ticket in self._tp1_done:
                last = self._trail_sl.get(ticket, sl)
                if direction == 'buy':
                    nt = price - trail_step
                    if nt > last + (trail_step * 0.4) and self._modify_sl(ticket, nt, pos.tp):
                        self._trail_sl[ticket] = nt
                        log_func(
                            f"TRAIL #{ticket} BUY | SL: {last:.2f}  {nt:.2f} | "
                            f"Step: {trail_step:.1f}",
                            "TRADE"
                        )
                else:
                    nt = price + trail_step
                    if nt < last - (trail_step * 0.4) and self._modify_sl(ticket, nt, pos.tp):
                        self._trail_sl[ticket] = nt
                        log_func(
                            f"TRAIL #{ticket} SELL | SL: {last:.2f}  {nt:.2f} | "
                            f"Step: {trail_step:.1f}",
                            "TRADE"
                        )

    def _check_structure_exit(self, ticket, direction, price, entry, sl_dist, log_func):
        """Close if price moves 1.5x SL against us (structure break)."""
        if direction == 'buy':
            if price < entry - sl_dist * 1.5:
                self._close_full(ticket)
                log_func(f"STRUCTURE EXIT #{ticket} BUY | Price: {price:.2f} < {entry - sl_dist * 1.5:.2f}", "TRADE")
        else:
            if price > entry + sl_dist * 1.5:
                self._close_full(ticket)
                log_func(f"STRUCTURE EXIT #{ticket} SELL | Price: {price:.2f} > {entry + sl_dist * 1.5:.2f}", "TRADE")

    def _get_trail_step(self) -> float:
        """ATR-based dynamic trail step ya fixed config value."""
        if cfg.TRAIL_ATR_ENABLED:
            try:
                rates = mt5.copy_rates_from_pos(cfg.SYMBOL, mt5.TIMEFRAME_M15, 0, 50)
                if rates is not None and len(rates) > cfg.TRAIL_ATR_PERIOD:
                    df = pd.DataFrame(rates)
                    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
                    atr = self._calc_atr(df, cfg.TRAIL_ATR_PERIOD)
                    if atr and atr > 0:
                        return max(1.0, round(atr * cfg.TRAIL_ATR_MULTIPLIER, 1))
            except Exception:
                pass
        return cfg.TRAIL_STEP

    def _calc_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """Calculate ATR."""
        if df.empty or len(df) < period + 1:
            return 0.0

        high = df['high'].values
        low = df['low'].values
        close = df['close'].values

        tr_list = []
        for i in range(1, len(df)):
            tr = max(
                high[i] - low[i],
                abs(high[i] - close[i - 1]),
                abs(low[i] - close[i - 1])
            )
            tr_list.append(tr)

        if len(tr_list) < period:
            return 0.0

        atr = sum(tr_list[-period:]) / period
        return round(atr, 2)

    def _is_better_sl(self, current_sl: float, new_sl: float, direction: str) -> bool:
        """Check if new SL is better."""
        if current_sl == 0 or current_sl is None:
            return True
        if direction == 'buy':
            return new_sl > current_sl
        else:
            return new_sl < current_sl

    def _modify_sl(self, ticket: int, new_sl: float, current_tp: float = 0) -> bool:
        """Modify SL for an open position while preserving TP."""
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol": cfg.SYMBOL,
            "sl": new_sl,
            "tp": current_tp,
            "magic": cfg.MAGIC_NUMBER,
        }
        result = mt5.order_send(request)
        if result is None:
            return False
        return result.retcode == mt5.TRADE_RETCODE_DONE

    def _close_partial(self, ticket: int, volume: float) -> bool:
        """Close partial volume."""
        pos = self._get_position(ticket)
        if pos is None:
            return False

        if pos.type == mt5.ORDER_TYPE_BUY:
            close_type = mt5.ORDER_TYPE_SELL
            close_price = mt5.symbol_info_tick(cfg.SYMBOL).bid
        else:
            close_type = mt5.ORDER_TYPE_BUY
            close_price = mt5.symbol_info_tick(cfg.SYMBOL).ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": cfg.SYMBOL,
            "volume": volume,
            "type": close_type,
            "position": ticket,
            "price": close_price,
            "deviation": 30,
            "magic": cfg.MAGIC_NUMBER,
            "comment": "TP1_PARTIAL",
        }
        result = mt5.order_send(request)
        if result is None:
            return False
        return result.retcode == mt5.TRADE_RETCODE_DONE

    def _close_full(self, ticket: int) -> bool:
        """Close full position."""
        pos = self._get_position(ticket)
        if pos is None:
            return False

        if pos.type == mt5.ORDER_TYPE_BUY:
            close_type = mt5.ORDER_TYPE_SELL
            close_price = mt5.symbol_info_tick(cfg.SYMBOL).bid
        else:
            close_type = mt5.ORDER_TYPE_BUY
            close_price = mt5.symbol_info_tick(cfg.SYMBOL).ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": cfg.SYMBOL,
            "volume": pos.volume,
            "type": close_type,
            "position": ticket,
            "price": close_price,
            "deviation": 30,
            "magic": cfg.MAGIC_NUMBER,
            "comment": "TP2_FULL",
        }
        result = mt5.order_send(request)
        if result is None:
            return False
        return result.retcode == mt5.TRADE_RETCODE_DONE

    def _get_position(self, ticket: int):
        """Get position by ticket."""
        positions = mt5.positions_get(symbol=cfg.SYMBOL)
        if positions is None:
            return None
        for p in positions:
            if p.ticket == ticket:
                return p
        return None

    def _get_our_positions(self) -> list:
        """Get all our open positions."""
        positions = mt5.positions_get(symbol=cfg.SYMBOL)
        if positions is None:
            return []
        return [p for p in positions if p.magic == cfg.MAGIC_NUMBER]

    def _get_tick(self) -> dict:
        """Get current tick."""
        tick = mt5.symbol_info_tick(cfg.SYMBOL)
        if tick is None:
            return None
        return {'bid': tick.bid, 'ask': tick.ask}

    def get_status(self, log_func=None) -> dict:
        """Get current status."""
        positions = self._get_our_positions()
        status = {
            'open_count': len(positions),
            'be_applied': list(self._be_done),
            'tp1_done': list(self._tp1_done),
        }

        if log_func and positions:
            for pos in positions:
                direction = 'buy' if pos.type == mt5.ORDER_TYPE_BUY else 'sell'
                entry = pos.price_open
                tick = self._get_tick()
                if not tick:
                    continue

                price = tick['bid']
                profit_pts = price - entry if direction == 'buy' else entry - price

                be_s = "BE" if pos.ticket in self._be_done else "---"
                tp1_s = "TP1" if pos.ticket in self._tp1_done else "---"

                log_func(
                    f"POS #{pos.ticket} {direction.upper()} @ {entry:.2f} | "
                    f"P/L: {profit_pts:.1f} pts | SL: {pos.sl:.2f} | "
                    f"{be_s} | {tp1_s} | TRAIL",
                    "INFO"
                )

        return status

    def reset_ticket(self, ticket: int):
        """Reset tracking for a closed ticket."""
        self._be_done.discard(ticket)
        self._tp1_done.discard(ticket)
        self._trail_sl.pop(ticket, None)
        self._original_sl_dist.pop(ticket, None)

    def reset_all(self):
        """Reset all tracking."""
        self._be_done.clear()
        self._tp1_done.clear()
        self._trail_sl.clear()
        self._original_sl_dist.clear()
