"""
Congressional trade data.

The problem
-----------
- capitoltrades.com is behind Cloudflare and rejects basic scripted requests
  (HTTP 403/503). `requests` and `cloudscraper` both fail against modern
  Cloudflare because their TLS fingerprints don't match real browsers.
- The official House/Senate clerk sites only publish PDFs.
- Public S3 datasets (House/Senate Stock Watcher) have been taken down.

The solution
------------
Three data paths, tried in order. Whichever works first wins. Data is
downloaded ONCE per run and cached in memory; call ``clear_cache()`` at the
start of each daily cycle to force a refresh.

Sources (in priority order)
    1. capitoltrades.com BFF JSON API via ``curl_cffi`` (mimics Chrome's
       real TLS fingerprint; defeats Cloudflare). Covers both chambers.
    2. House + Senate Stock Watcher S3 JSON (legacy; usually 403 now but
       cheap to try in case the bucket comes back).
    3. Manual file: ``data/congress_trades.json`` or ``.csv`` exported from
       capitoltrades.com or any other source. See README for the schema.

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

import csv
import json
import logging
import re
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
# Source 1 (primary): capitoltrades.com BFF JSON API via curl_cffi
# ---------------------------------------------------------------------------
# curl_cffi uses the real Chrome TLS fingerprint, which is currently the only
# reliable way to pass Cloudflare for this site. cloudscraper / requests get
# rejected because their TLS handshakes look like bots.

def _bff_session():
    """Return a session that can pass Cloudflare. Prefer curl_cffi."""
    try:
        from curl_cffi import requests as cffi_requests
        s = cffi_requests.Session(impersonate="chrome120")
        s.headers.update({
            "Accept":          "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin":          "https://www.capitoltrades.com",
            "Referer":         "https://www.capitoltrades.com/trades",
            "Sec-Fetch-Dest":  "empty",
            "Sec-Fetch-Mode":  "cors",
            "Sec-Fetch-Site":  "same-site",
        })
        # Warm-up: visit the main page so we collect any cf_clearance cookies
        try:
            s.get("https://www.capitoltrades.com/trades", timeout=30)
        except Exception:
            pass
        return s, "curl_cffi"
    except ImportError:
        pass

    # Last-ditch fallback (will likely fail with 403/503)
    try:
        import cloudscraper
        return cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        ), "cloudscraper"
    except ImportError:
        pass

    s = requests.Session()
    s.headers.update({"User-Agent": _UA, "Accept": "application/json"})
    return s, "requests"


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


def _load_via_playwright(days: int) -> list[dict]:
    """
    Drive a real headless Chromium via Playwright.

    The browser passes Cloudflare's full challenge (TLS + JS + fingerprint),
    then we call the BFF API from inside the page so it inherits the
    cf_clearance cookie. This is the most reliable path against modern
    Cloudflare protections.

    Requires:
        pip install playwright
        playwright install chromium
    """
    from playwright.sync_api import sync_playwright  # imported lazily

    cutoff = datetime.now(tz=UTC) - timedelta(days=days)
    out: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=_UA,
            viewport={"width": 1366, "height": 900},
            locale="en-US",
        )
        page = ctx.new_page()
        logger.info("Playwright: navigating to capitoltrades.com to pass Cloudflare …")
        page.goto("https://www.capitoltrades.com/trades", wait_until="domcontentloaded",
                  timeout=60_000)
        # Give Cloudflare's JS challenge a moment, then wait for the page to settle
        page.wait_for_load_state("networkidle", timeout=30_000)

        page_num = 1
        while True:
            logger.info("Playwright: fetching BFF page %d …", page_num)
            url = (
                "https://bff.capitoltrades.com/trades"
                f"?page={page_num}&pageSize=96&sortBy=-txDate"
            )
            payload = page.evaluate(
                """async (u) => {
                    const r = await fetch(u, {
                        headers: { 'Accept': 'application/json' },
                        credentials: 'include'
                    });
                    if (!r.ok) return { __error: r.status };
                    return await r.json();
                }""",
                url,
            )

            if not isinstance(payload, dict) or payload.get("__error"):
                logger.warning("Playwright BFF returned %s", payload)
                break

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
            if stop or page_num >= total_pages:
                break
            page_num += 1
            time.sleep(0.4)

        browser.close()

    logger.info("Playwright: %d usable trades", len(out))
    return out


def _load_capitoltrades(days: int) -> list[dict]:
    cutoff = datetime.now(tz=UTC) - timedelta(days=days)
    session, backend = _bff_session()
    logger.info("capitoltrades.com via %s", backend)
    out: list[dict] = []
    page = 1
    while True:
        params = {"page": page, "pageSize": 96, "sortBy": "-txDate"}
        logger.info("  page %d …", page)
        r = session.get(CAPITOL_BFF, params=params, timeout=30)
        if r.status_code in (403, 503):
            raise RuntimeError(
                f"capitoltrades.com blocked (HTTP {r.status_code}). "
                "Install curl_cffi (`pip install curl_cffi`) for a working "
                "TLS fingerprint, or drop a manual export at "
                "data/congress_trades.json."
            )
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
        time.sleep(0.5)

    logger.info("  capitoltrades: %d usable trades", len(out))
    return out


# ---------------------------------------------------------------------------
# Source 3: manual file dropped by user at data/congress_trades.{json,csv}
# ---------------------------------------------------------------------------
# Schema (JSON): a list of objects, each with these keys (camel or snake):
#   ticker, type ("buy"|"sell"|"purchase"|"sale"), date (YYYY-MM-DD),
#   amount (range string or numeric), politician_name, chamber ("house"|"senate")
# CSV equivalent uses the same column names.

_MANUAL_PATHS = [
    Path(__file__).parent.parent / "data" / "congress_trades.json",
    Path(__file__).parent.parent / "data" / "congress_trades.csv",
]


def _load_manual_file() -> list[dict]:
    for path in _MANUAL_PATHS:
        if not path.exists():
            continue
        logger.info("Loading manual export from %s", path)
        rows: list[dict]
        if path.suffix == ".json":
            rows = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(rows, dict):
                rows = rows.get("data") or rows.get("transactions") or []
        else:
            with path.open(encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))

        out: list[dict] = []
        for r in rows:
            t = _normalize_manual_row(r)
            if t is not None:
                out.append(t)
        logger.info("  manual file: %d usable trades", len(out))
        return out
    return []


def _normalize_manual_row(row: dict) -> dict | None:
    try:
        ticker = _norm_ticker(row.get("ticker") or row.get("Ticker") or "")
        if ticker is None:
            return None
        ttype = _norm_type(row.get("type") or row.get("Type") or row.get("transactionType") or "")
        if ttype is None:
            return None
        date = _parse_date(str(
            row.get("date") or row.get("Date")
            or row.get("transaction_date") or row.get("TransactionDate") or ""
        ))
        if date is None:
            return None
        name = _clean_name(
            row.get("politician_name") or row.get("politicianName")
            or row.get("representative") or row.get("senator") or row.get("name") or ""
        )
        if not name:
            return None
        amount = row.get("amount") or row.get("Amount") or ""
        try:
            amount_mid = float(amount) if str(amount).replace(".", "", 1).isdigit() else _parse_amount(str(amount))
        except (TypeError, ValueError):
            amount_mid = _parse_amount(str(amount))
        chamber = (row.get("chamber") or "").lower() or "unknown"
        return {
            "ticker":          ticker,
            "type":            ttype,
            "amount":          str(amount),
            "amount_mid":      amount_mid or 8_000,
            "date":            date,
            "politician_name": name,
            "politician_id":   _slugify(name),
            "chamber":         chamber,
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Master loader with caching + fallback chain
# ---------------------------------------------------------------------------

_RAW_TRADES: list[dict] | None = None


def clear_cache() -> None:
    """Forget any downloaded data so the next call refetches it."""
    global _RAW_TRADES
    _RAW_TRADES = None


def _load_from_sources(days: int) -> list[dict]:
    # 1. Manual file overrides everything (offline / explicit user choice)
    manual = _load_manual_file()
    if manual:
        return manual

    collected: list[dict] = []

    # 2. Playwright (real browser, defeats full Cloudflare challenge)
    try:
        import playwright  # noqa: F401 — just to detect if installed
        try:
            collected = _load_via_playwright(days)
        except Exception as e:
            logger.warning("Playwright path failed: %s", e)
    except ImportError:
        logger.info(
            "Playwright not installed – skipping. For the most reliable path:\n"
            "    pip install playwright\n"
            "    playwright install chromium"
        )

    # 3. capitoltrades.com BFF directly (curl_cffi). Often blocked by Cloudflare.
    if not collected:
        try:
            collected = _load_capitoltrades(days)
        except Exception as e:
            logger.warning("capitoltrades.com (direct) unavailable: %s", e)

    # 4. Stock Watcher S3 buckets (legacy; usually 403 now)
    if not collected:
        for url, chamber in ((HOUSE_SW_URL, "house"), (SENATE_SW_URL, "senate")):
            try:
                collected.extend(_load_stockwatcher(url, chamber))
            except Exception as e:
                logger.warning("%s Stock Watcher unavailable: %s", chamber.title(), e)

    if not collected:
        raise RuntimeError(
            "All congressional data sources failed.\n"
            "  Most reliable fix:\n"
            "    pip install playwright && playwright install chromium\n"
            "  Then: python run_mirror.py diagnose\n"
            "  Or:  drop a manual export at data/congress_trades.json\n"
            "       (schema in congress_mirror/README.md)"
        )

    logger.info("Total trades loaded: %d", len(collected))
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
