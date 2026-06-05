"""
Congressional trade data.

The problem
-----------
- capitoltrades.com sits behind Cloudflare and blocks scripted requests.
- The official House/Senate clerk sites only publish PDFs (not machine-readable
  transaction rows), so they cannot drive an automated strategy.

The solution
------------
Use already-parsed, structured, public datasets. We try several sources in
order and use the first that returns data. Everything is downloaded ONCE per
run and cached in memory (call ``clear_cache()`` at the start of each daily
cycle to force a refresh).

Sources (in priority order)
    1. House Stock Watcher  – public S3 JSON, no auth   (House reps)
    2. Senate Stock Watcher – public S3 JSON, no auth   (Senators)
    3. capitoltrades.com    – BFF JSON API via cloudscraper (both chambers)

Public interface
    clear_cache()
    get_all_trades(days)            -> {politician_id: [trade_dict, ...]}
    get_politicians()              -> [{id, name, party, state, chamber}, ...]
    get_politician_trades(id, days) -> [trade_dict, ...]

trade_dict schema
    {ticker, type('buy'|'sell'), amount, amount_mid, date(datetime, tz=utc),
     politician_name, politician_id, chamber}
"""
from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data source URLs
# ---------------------------------------------------------------------------
HOUSE_SW_URL  = "https://house-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json"
SENATE_SW_URL = "https://senate-stock-watcher-data.s3-us-west-2.amazonaws.com/aggregate/all_transactions.json"
CAPITOL_BFF   = "https://bff.capitoltrades.com/trades"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Midpoints of STOCK Act disclosure amount ranges
AMOUNT_MIDPOINTS: dict[str, float] = {
    "$1,001 - $15,000":           8_000,
    "$15,001 - $50,000":         32_500,
    "$50,001 - $100,000":        75_000,
    "$100,001 - $250,000":      175_000,
    "$250,001 - $500,000":      375_000,
    "$500,001 - $1,000,000":    750_000,
    "$1,000,001 - $5,000,000": 3_000_000,
    "$5,000,001 - $25,000,000":15_000_000,
    "Over $5,000,000":         5_000_000,
}

_TICKER_RE = re.compile(r"^[A-Z]{1,5}(\.[A-Z]{1,2})?$")


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_amount(text: str) -> float:
    text = (text or "").strip()
    for pattern, mid in AMOUNT_MIDPOINTS.items():
        if text and (text.lower() in pattern.lower() or pattern.lower() in text.lower()):
            return mid
    nums = re.findall(r"[\d,]+", text)
    if len(nums) >= 2:
        return (float(nums[0].replace(",", "")) + float(nums[1].replace(",", ""))) / 2
    if len(nums) == 1:
        return float(nums[0].replace(",", ""))
    return 8_000


def _parse_date(text: str) -> datetime | None:
    text = (text or "").strip()
    if not text or text in ("--", "N/A"):
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).replace(tzinfo=UTC)
        except ValueError:
            pass
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _clean_name(name: str) -> str:
    """Strip honorifics like 'Hon.', 'Rep.', 'Sen.' from a display name."""
    name = (name or "").strip()
    name = re.sub(r"^(hon\.?|rep\.?|representative|sen\.?|senator|mr\.?|mrs\.?|ms\.?|dr\.?)\s+",
                  "", name, flags=re.I).strip()
    return name


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _clean_name(name).lower()).strip("-")


def _join_name(row: dict) -> str:
    first = (row.get("first_name") or row.get("firstName") or "").strip()
    last  = (row.get("last_name")  or row.get("lastName")  or "").strip()
    return f"{first} {last}".strip()


def _norm_type(raw: str) -> str | None:
    raw = (raw or "").lower()
    if "purchase" in raw or raw == "buy" or raw.startswith("p"):
        return "buy"
    if "sale" in raw or "sell" in raw or raw.startswith("s"):
        return "sell"
    return None  # skip exchange / receive / other


def _norm_ticker(raw: str) -> str | None:
    t = (raw or "").strip().upper()
    if not t or t in ("--", "N/A", "NONE"):
        return None
    return t if _TICKER_RE.match(t) else None


# ---------------------------------------------------------------------------
# Source 1 & 2: Stock Watcher (House + Senate) public JSON datasets
# ---------------------------------------------------------------------------

def _normalize_sw_row(row: dict, chamber: str) -> dict | None:
    """Normalise a House/Senate Stock Watcher JSON record."""
    try:
        ticker = _norm_ticker(row.get("ticker"))
        if ticker is None:
            return None

        ttype = _norm_type(row.get("type"))
        if ttype is None:
            return None

        date = _parse_date(str(row.get("transaction_date") or row.get("disclosure_date") or ""))
        if date is None:
            return None

        if chamber == "house":
            name = row.get("representative") or _join_name(row)
        else:
            name = row.get("senator") or _join_name(row)
        name = _clean_name(name)
        if not name:
            return None

        amount = row.get("amount") or ""
        return {
            "ticker":          ticker,
            "type":            ttype,
            "amount":          amount,
            "amount_mid":      _parse_amount(amount),
            "date":            date,
            "politician_name": name,
            "politician_id":   _slugify(name),
            "chamber":         chamber,
        }
    except Exception:
        return None


def _load_stockwatcher(url: str, chamber: str) -> list[dict]:
    logger.info("Downloading %s Stock Watcher dataset …", chamber.title())
    r = requests.get(url, timeout=120, headers={"User-Agent": _UA})
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        # Some snapshots wrap the array under a key
        data = data.get("transactions") or data.get("data") or []
    out = [t for row in data if (t := _normalize_sw_row(row, chamber)) is not None]
    logger.info("  %s: %d usable equity trades", chamber.title(), len(out))
    return out


# ---------------------------------------------------------------------------
# Source 3: capitoltrades.com BFF JSON API (fallback) via cloudscraper
# ---------------------------------------------------------------------------

def _bff_session():
    try:
        import cloudscraper
        return cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
    except ImportError:
        s = requests.Session()
        s.headers.update({"User-Agent": _UA, "Accept": "application/json"})
        return s


def _normalize_bff_trade(raw: dict) -> dict | None:
    try:
        asset = raw.get("asset") or {}
        ticker = _norm_ticker(asset.get("issuerTicker") or asset.get("assetTicker", "").split(":")[0])
        if ticker is None:
            return None

        ttype = _norm_type(raw.get("txType"))
        if ttype is None:
            return None

        date = _parse_date(str(raw.get("txDate") or raw.get("pubDate") or ""))
        if date is None:
            return None

        pol = raw.get("politician") or {}
        name = _clean_name(
            f"{pol.get('firstName','')} {pol.get('lastName','')}".strip()
            or raw.get("politicianName", "")
        )
        if not name:
            return None

        # capitoltrades gives a numeric `value` (upper bound of range) and `size`
        value = raw.get("value") or raw.get("size") or 0
        try:
            amount_mid = float(value)
        except (TypeError, ValueError):
            amount_mid = _parse_amount(str(value))

        chamber = (pol.get("chamber") or "").lower() or "unknown"
        return {
            "ticker":          ticker,
            "type":            ttype,
            "amount":          str(value),
            "amount_mid":      amount_mid or 8_000,
            "date":            date,
            "politician_name": name,
            "politician_id":   _slugify(name),
            "chamber":         chamber,
        }
    except Exception:
        return None


def _load_capitoltrades(days: int) -> list[dict]:
    cutoff = datetime.now(tz=UTC) - timedelta(days=days)
    session = _bff_session()
    out: list[dict] = []
    page = 1
    while True:
        params = {"page": page, "pageSize": 100, "sortBy": "-txDate"}
        logger.info("capitoltrades BFF page %d …", page)
        r = session.get(CAPITOL_BFF, params=params, timeout=30)
        r.raise_for_status()
        payload = r.json()
        rows = payload.get("data") or []
        if not rows:
            break

        stop = False
        for raw in rows:
            t = _normalize_bff_trade(raw)
            if t is None:
                continue
            if t["date"] < cutoff:
                stop = True
                continue
            out.append(t)

        meta = (payload.get("meta") or {}).get("paging") or {}
        total_pages = int(meta.get("totalPages") or 1)
        if stop or page >= total_pages:
            break
        page += 1
        time.sleep(1.0)

    logger.info("  capitoltrades: %d usable trades", len(out))
    return out


# ---------------------------------------------------------------------------
# Master loader with caching + fallback chain
# ---------------------------------------------------------------------------

_RAW_TRADES: list[dict] | None = None


def clear_cache() -> None:
    """Forget any downloaded data so the next call refetches it."""
    global _RAW_TRADES
    _RAW_TRADES = None


def _load_from_sources(days: int) -> list[dict]:
    collected: list[dict] = []

    # Sources 1 & 2: Stock Watcher (combine House + Senate when available)
    for url, chamber in ((HOUSE_SW_URL, "house"), (SENATE_SW_URL, "senate")):
        try:
            collected.extend(_load_stockwatcher(url, chamber))
        except Exception as e:
            logger.warning("%s Stock Watcher unavailable: %s", chamber.title(), e)

    # Source 3: capitoltrades fallback only if the above produced nothing
    if not collected:
        try:
            collected = _load_capitoltrades(days)
        except Exception as e:
            logger.warning("capitoltrades.com unavailable: %s", e)

    if not collected:
        raise RuntimeError(
            "All congressional data sources failed. Check your internet connection. "
            "Run `python run_mirror.py diagnose` to see which source is reachable."
        )

    logger.info("Total trades loaded across all sources: %d", len(collected))
    return collected


def _ensure_loaded(days: int) -> list[dict]:
    global _RAW_TRADES
    if _RAW_TRADES is None:
        _RAW_TRADES = _load_from_sources(days)
    return _RAW_TRADES


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_all_trades(days: int = 365) -> dict[str, list[dict]]:
    """Return {politician_id: [trade_dict, ...]} for trades in the last `days`."""
    cutoff = datetime.now(tz=UTC) - timedelta(days=days)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for t in _ensure_loaded(days):
        if t["date"] >= cutoff:
            grouped[t["politician_id"]].append(t)
    return dict(grouped)


def get_politicians() -> list[dict]:
    """Return all politicians that have any trades in the lookback window."""
    grouped = get_all_trades()
    pols = []
    for pid, trades in grouped.items():
        first = trades[0]
        pols.append({
            "id":      pid,
            "name":    first["politician_name"],
            "party":   "",
            "state":   "",
            "chamber": first.get("chamber", ""),
        })
    return pols


def get_politician_trades(politician_id: str, days: int = 365) -> list[dict]:
    """Return all trades for a single politician over the last `days`."""
    return get_all_trades(days).get(politician_id, [])
