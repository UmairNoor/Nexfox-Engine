"""
notifier_new.py  Telegram Notification System
Sends trade alerts and bot status to Telegram.
"""

import requests
from datetime import datetime, timezone
from config_new import TELEGRAM_ENABLED, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID


def send_telegram(message: str, parse_mode: str = 'HTML') -> bool:
    """Send message to Telegram."""
    if not TELEGRAM_ENABLED:
        return False

    if TELEGRAM_TOKEN == 'YOUR_BOT_TOKEN' or TELEGRAM_CHAT_ID == 'YOUR_CHAT_ID':
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': message,
        'parse_mode': parse_mode,
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        print(f"Telegram error: {e}")
        return False


def alert_trade_opened(trade: dict):
    """Alert when a trade is opened."""
    direction = trade.get('direction', '').upper()
    emoji = "" if direction == "BUY" else ""

    msg = (
        f"{emoji} <b>TRADE OPENED</b>\n"
        f"\n"
        f" Symbol: <b>{trade.get('symbol', 'GOLD')}</b>\n"
        f" Direction: <b>{direction}</b>\n"
        f" Entry: <b>{trade.get('entry', 0):.2f}</b>\n"
        f" SL: <b>{trade.get('sl', 0):.2f}</b>\n"
        f" TP: <b>{trade.get('tp', 0):.2f}</b>\n"
        f" Lots: <b>{trade.get('lots', 0)}</b>\n"
        f" RR: <b>1:{trade.get('rr_ratio', 0)}</b>\n"
        f" Risk: <b>${trade.get('risk_usd', 0)}</b>\n"
        f" Ticket: <b>#{trade.get('ticket', '')}</b>\n"
        f"\n"
        f" {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"
    )
    send_telegram(msg)


def alert_trade_closed(ticket: int, pnl: float, reason: str):
    """Alert when a trade is closed."""
    emoji = "" if pnl >= 0 else ""
    msg = (
        f"{emoji} <b>TRADE CLOSED</b>\n"
        f"\n"
        f" Ticket: <b>#{ticket}</b>\n"
        f" PnL: <b>${pnl:.2f}</b>\n"
        f" Reason: <b>{reason}</b>\n"
        f" {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"
    )
    send_telegram(msg)


def alert_bot_status(status: str, details: str = ""):
    """Alert bot status changes."""
    msg = (
        f" <b>BOT STATUS</b>\n"
        f"\n"
        f" Status: <b>{status}</b>\n"
    )
    if details:
        msg += f" {details}\n"
    msg += f" {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"
    send_telegram(msg)


def alert_daily_summary(pnl: float, trades: int):
    """Send daily summary."""
    emoji = "" if pnl >= 0 else ""
    msg = (
        f"{emoji} <b>DAILY SUMMARY</b>\n"
        f"\n"
        f" Daily PnL: <b>${pnl:.2f}</b>\n"
        f" Trades: <b>{trades}</b>\n"
        f" {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    send_telegram(msg)


def alert_sweep_detected(sweep_type: str, price: float, direction: str):
    """Alert when a liquidity sweep is detected."""
    msg = (
        f" <b>SWEEP DETECTED</b>\n"
        f"\n"
        f" Type: <b>{sweep_type}</b>\n"
        f" Price: <b>{price:.2f}</b>\n"
        f" Direction: <b>{direction.upper()}</b>\n"
        f" {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"
    )
    send_telegram(msg)
