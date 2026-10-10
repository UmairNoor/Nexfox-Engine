"""
config_new.py  XAUUSD Liquidity + Engulfing Bot Configuration
"""
import os

# === MT5 ===
# Credentials are NEVER stored in this file. Standalone runs read them from the environment;
# on the NexFox Engine platform the worker injects them per instance from the encrypted vault.
MT5_LOGIN    = int(os.environ.get("REHMAN_MT5_LOGIN", "0") or 0)
MT5_PASSWORD = os.environ.get("REHMAN_MT5_PASSWORD", "")
MT5_SERVER   = os.environ.get("REHMAN_MT5_SERVER", "")
MT5_PATH     = os.environ.get("REHMAN_MT5_PATH", r"C:\Program Files\MetaTrader 5\terminal64.exe")
SYMBOL       = "GOLD"
MAGIC_NUMBER = 88880001

# === RISK MANAGEMENT ===
RISK_PERCENT      = 1.0
MIN_LOT           = 0.02
MAX_LOT           = 0.10
LOT_STEP          = 0.01
SL_BUFFER_PTS     = 1.0
MIN_RR            = 2.0
SPREAD_MAX        = 60.0

# === SPLIT TARGET MODEL (2 trades per setup) ===
SPLIT_TRADES_ENABLED = True  # 2 trades: TP1 trade (1R) + runner trade (2R, trailed)
MAX_OPEN_TRADES      = 2      # dono split trades ek saath open hote hain

# === TIMEFRAMES ===
TF_D1   = "D1"
TF_H4   = "H4"
TF_H1   = "H1"
TF_M15  = "M15"
TF_M5   = "M5"
TF_M1   = "M1"

# === SWING DETECTION (4H) ===
SWING_LOOKBACK        = 2
SWING_MIN_MOVE_PTS = 5.0
SWING_CONFIRM_BARS    = 2

# === EQUAL HIGHS/LOWS (15M) ===
EQUAL_LEVEL_TOLERANCE_PTS = 1.0
EQUAL_LEVEL_LOOKBACK      = 50

# === LIQUIDITY SWEEP ===
SWEEP_BUFFER_PTS     = 0.5
SWEEP_REJECTION_BODY = 0.5

# === 1H LIQUIDITY DETECTION ===
H1_MIN_MOVE_PTS = 40.0

# === ENGULFING (5M / 1M) ===
ENGULFING_MIN_BODY_RATIO = 0.60
ENGULFING_WICK_RATIO     = 0.30

# === M3 REFINED ENTRY ===
# M5: sweep + engulfing + VSA confirmation. M3 engulfing = refined entry
# (better price -> tighter SL). VSA always on M5 only.
ENGULF_ENTRY_TF = "M3"

# === SIMPLE VSA (M5 volume vs previous 15-20 candles) ===
VSA_VOL_PERIOD  = 20      # previous candles used for average volume
VSA_VOL_MULT    = 1.2     # current volume must exceed avg * mult
VSA_WICK_RATIO  = 0.35    # "long" wick = wick length >= range * this

# === LIQUIDITY LEVEL / SETUP TRACKING ===
MAX_ENTRIES_PER_LEVEL  = 2    # max entries on the same liquidity level
MAX_LEVELS_PER_SIDE    = 2    # nearest N liquidity levels shown per side (above/below)
SWEEP_LOOKBACK_CANDLES = 8    # how many M5 candles back to look for a sweep
SL_SWEEP_BUFFER_PTS    = 1.0  # SL offset beyond sweep extreme
ENGULF_CHAIN_LOOKBACK  = 12   # engulf->sweep->engulf chain search window (per TF)

# === SESSIONS (UTC) ===
SESSION_LONDON_START = 7
SESSION_LONDON_END   = 16
SESSION_NY_START     = 12
SESSION_NY_END       = 21

# === TRADE RULES ===
MAX_DAILY_TRADES  = 8
MAX_DAILY_LOSS_USD = 20.0
SCAN_INTERVAL_SEC = 3

# === BREAKEVEN (RR-Based  Bot folder style) ===
BE_ENABLED             = True
BREAKEVEN_AT_RR        = 1.5     # Price 1.5x SL distance pe ho toh BE lagao
BE_BUFFER_PCT          = 0.4     # BE SL = entry + sl_dist * 0.4 (locks profit)

# === TRAILING STOP (ATR-Based  Bot folder style) ===
TRAIL_ENABLED          = True
TRAIL_STEP             = 4.0     # Default trail step (ATR override hota hai)
TRAIL_ATR_ENABLED      = True    # ATR se dynamic trail step
TRAIL_ATR_MULTIPLIER   = 0.3     # Trail = ATR * 0.3
TRAIL_ATR_TIMEFRAME    = "M15"   # ATR kis TF se calculate ho
TRAIL_ATR_PERIOD       = 14      # ATR period

# === TAKE PROFIT ===
TP1_RR                 = 0.5     # 0.5R pe TP1 close + runner lock (backtest: 88.9% WR)
TP1_LOCK_RR            = 0.5     # runner SL +0.5R pe lock (BE nahi - wapasi pe bhi profit)
TP1_CLOSE_PCT          = 0.0     # split model: TP1 separate trade hai, runner partial close OFF (backtest match)
TP_MAX_RR              = 2.0     # minimum RR gate (TP1 trade se farq nahi)
RUNNER_TP_MAX_RR       = 10.0    # runner safety TP (trail se exit hota hai, ye sirf backup)

# === LOGS ===
LOG_DIR     = os.environ.get("REHMAN_LOG_DIR", "logs_new")
LOG_FILE    = os.path.join(LOG_DIR, "bot.log")
TRADE_LOG   = os.path.join(LOG_DIR, "trades.csv")
os.makedirs(LOG_DIR, exist_ok=True)

# === TELEGRAM ===
TELEGRAM_ENABLED = os.environ.get("REHMAN_TELEGRAM_ENABLED", "false").lower() == "true"
TELEGRAM_TOKEN   = os.environ.get("REHMAN_TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("REHMAN_TELEGRAM_CHAT_ID", "")

# === STANDALONE SAFETY ===
# Standalone (outside the platform) runs refuse REAL accounts unless this is set explicitly.
ALLOW_LIVE_STANDALONE = os.environ.get("REHMAN_ALLOW_LIVE", "false").lower() == "true"
