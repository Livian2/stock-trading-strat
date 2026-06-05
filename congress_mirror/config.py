"""Load configuration from environment / .env file."""
import os
from pathlib import Path

_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            # Strip inline comments (everything after unquoted #)
            val = val.split("#")[0].strip()
            os.environ.setdefault(key.strip(), val)


def _req(key: str) -> str:
    val = os.environ.get(key, "")
    if not val:
        raise RuntimeError(f"Required env var {key!r} is not set. Check your .env file.")
    return val


def _opt(key: str, default: str) -> str:
    return os.environ.get(key, default)


ALPACA_API_KEY    = _req("ALPACA_API_KEY")
ALPACA_SECRET_KEY = _req("ALPACA_SECRET_KEY")
ALPACA_BASE_URL   = _opt("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

EMAIL_SENDER       = _opt("EMAIL_SENDER",       "")
EMAIL_RECIPIENT    = _opt("EMAIL_RECIPIENT",     "")
EMAIL_SMTP_SERVER  = _opt("EMAIL_SMTP_SERVER",   "smtp.gmail.com")
EMAIL_SMTP_PORT    = int(_opt("EMAIL_SMTP_PORT", "587"))
EMAIL_SMTP_PASSWORD = _opt("EMAIL_SMTP_PASSWORD", "")

MIN_TRADES          = int(_opt("MIN_TRADES_FOR_RANKING", "5"))
LOOKBACK_DAYS       = int(_opt("LOOKBACK_DAYS", "365"))
PORTFOLIO_FRACTION  = float(_opt("PORTFOLIO_FRACTION", "0.90"))
MAX_POSITIONS       = int(_opt("MAX_POSITIONS", "15"))
