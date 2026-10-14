"""Paths and tunable settings. Override any setting in config.json at the project root."""
import copy
import datetime as dt
import json
import os
from pathlib import Path

ROOT = Path(os.environ.get("SCREENER_HOME", Path(__file__).resolve().parent.parent))
DATA = ROOT / "data"
CACHE = DATA / "cache"
REPORTS = DATA / "reports"
LOGS = DATA / "logs"
DB_PATH = DATA / "screener.sqlite"
SHORTLIST_PATH = DATA / "shortlist.json"
CONFIG_PATH = ROOT / "config.json"

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
MARKET_CLOSE = dt.time(15, 30)   # news after this maps to the next session
EOD_READY_HOUR = 16              # today's daily candle is trusted only after 16:00 IST

DEFAULTS = {
    "benchmark": "^NSEI",
    "benchmark_fallback": "NIFTYBEES.NS",
    "history_range": "2y",
    "workers": 4,
    "news_lookback_days": 4,
    "monitor": {"enabled": True, "poll_seconds": 60, "headline_max_age_minutes": 60},
    "news_alerts": {
        "mode": "actionable",           # actionable | signals_only | all
        "correlation_hours": 72,         # retain overnight/weekend news as possible context
        "quote_max_age_minutes": 20,     # indicative quotes can be delayed
        "confirmations": 2,              # distinct quote timestamps, not repeated cached prices
    },
    "risk": {"max_stock_weight": 0, "max_sector_weight": 0},  # 0 = no user-defined limit
    "event": {
        "estimation_sessions": 120,      # market-model fit window before the event
        "min_estimation_sessions": 60,
        "gap_sessions": 5,               # gap so pre-event leakage doesn't contaminate the fit
        "window_sessions": 2,            # event day + next session
        "min_abnormal_drop": 0.03,       # abnormal drop must be at least 3% ...
        "sigma_multiple": 2.0,           # ... and at least 2 sigma of the stock's residual noise
        "volume_ratio_flag": 1.5,
        "expire_after_sessions": 60,
    },
    "levelling": {
        "sessions": 5,                   # look-back window for 'has it levelled out'
        "min_sessions_after_event": 5,
        "atr_ratio_max": 1.25,           # recent ATR% <= 1.25x pre-event ATR%
        "drift_sigma_max": 1.0,          # |abnormal drift| over window <= 1 sigma*sqrt(n)
        "no_new_low_sessions": 3,        # trough must be older than this many sessions
    },
    "notify": {
        "windows_toast": True,
        "telegram_bot_token": "",
        "telegram_chat_id": "",
        "ntfy_topic": "",                # phone push via the free ntfy app (https://ntfy.sh)
        "ntfy_server": "https://ntfy.sh",
        "discord_webhook": "",           # phone push via a Discord channel webhook
    },
}


def _merge(base, override):
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path=None):
    path = Path(path) if path else CONFIG_PATH
    user = {}
    if path.exists():
        user = json.loads(path.read_text(encoding="utf-8"))
    cfg = _merge(DEFAULTS, user)
    from . import telegram
    local = telegram.load()
    # Secrets may also come from environment variables.
    cfg["notify"]["telegram_bot_token"] = os.environ.get("SCREENER_TELEGRAM_TOKEN", cfg["notify"]["telegram_bot_token"])
    cfg["notify"]["telegram_chat_id"] = os.environ.get("SCREENER_TELEGRAM_CHAT", cfg["notify"]["telegram_chat_id"])
    # Explicit local pairing controls this worker; environment values remain a headless fallback.
    if local.get('token'):
        cfg['notify']['telegram_bot_token'] = local['token']
        cfg['notify']['telegram_chat_id'] = local.get('notify_chat_id') or local.get('chat_id', '')
    if local.get('disabled'):
        cfg['notify']['telegram_bot_token'] = ''
        cfg['notify']['telegram_chat_id'] = ''
    cfg["notify"]["ntfy_topic"] = os.environ.get("SCREENER_NTFY_TOPIC", cfg["notify"]["ntfy_topic"])
    cfg["notify"]["discord_webhook"] = os.environ.get("SCREENER_DISCORD_WEBHOOK", cfg["notify"]["discord_webhook"])
    return cfg


def ensure_dirs():
    for d in (DATA, CACHE, REPORTS, LOGS):
        d.mkdir(parents=True, exist_ok=True)


def now_ist():
    return dt.datetime.now(IST)
