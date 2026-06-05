"""
Congressional trade data.

Source priority
---------------
1. capitoltrades.com  – covers both House + Senate; uses cloudscraper to
                        bypass Cloudflare bot protection.
2. Official House PTR  – fallback ZIP/CSV from disclosures-clerk.house.gov;
                         only House members but 100 % reliable.

Both sources expose the same public interface:
    get_politicians() -> list[dict]
    get_politician_trades(politician_id, days) -> list[dict]
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
import time
import zipfile
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HTTP session — prefer cloudscraper (handles Cloudflare JS challenges)
# ---------------------------------------------------------------------------
try:
    import cloudscraper as _cs
    _session = _cs.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )
    logger.debug("HTTP client: cloudscraper")
except ImportError:
    _session = requests.Session()
    _session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    logger.warning("cloudscraper not installed – install it with: pip install cloudscraper")

BASE_URL = "https://www.capitoltrades.com"

# Midpoints of STOCK Act disclosure amount ranges
AMOUNT_MIDPOINTS: dict[str, float] = {
    "$1,001 - $15,000":           8_000,
    "$15,001 - $50,000":         32_500,
    "$50,001 - $100,000":        75_000,
    "$100,001 - $250,000":      175_000,
    "$250,001 - $500,000":      375_000,
    "$500,001 - $1,000,000":    750_000,
    "$1,000,001 - $5,000,000": 3_000_000,
    "Over $5,000,000":         5_000_000,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_amount(text: str) -> float:
    text = text.strip()
    for pattern, mid in AMOUNT_MIDPOINTS.items():
        if text.lower() in pattern.lower() or pattern.lower() in text.lower():
            return mid
    nums = re.findall(r"[\d,]+", text)
    if len(nums) >= 2:
        return (float(nums[0].replace(",", "")) + float(nums[1].replace(",", ""))) / 2
    if len(nums) == 1:
        return float(nums[0].replace(",", ""))
    return 8_000


def _parse_date(text: str) -> datetime | None:
    text = text.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    return None


def _get(url: str, params: dict | None = None, retries: int = 3) -> requests.Response:
    for attempt in range(retries):
        try:
            time.sleep(1.5 + attempt)
            r = _session.get(url, params=params, timeout=25)
            r.raise_for_status()
            return r
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else 0
            if code in (429, 503) and attempt < retries - 1:
                wait = 8 * (attempt + 1)
                logger.warning("capitoltrades.com blocked (HTTP %d); waiting %ds", code, wait)
                time.sleep(wait)
            elif attempt == retries - 1:
                raise
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Failed to fetch {url} after {retries} attempts")


def _next_data(html: str) -> dict | None:
    """Extract __NEXT_DATA__ JSON embedded by Next.js."""
    tag = BeautifulSoup(html, "lxml").find("script", id="__NEXT_DATA__")
    if tag and tag.string:
        try:
            return json.loads(tag.string)
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------------------
# Source 1: capitoltrades.com
# ---------------------------------------------------------------------------

def _ct_get_politicians() -> list[dict]:
    politicians: list[dict] = []
    page = 1
    while True:
        logger.info("capitoltrades.com politicians page %d", page)
        r = _get(f"{BASE_URL}/politicians", params={"page": page} if page > 1 else None)

        nd = _next_data(r.text)
        if nd:
            props = nd.get("props", {}).get("pageProps", {})
            for key in ("politicians", "data", "results", "items"):
                pols = props.get(key)
                if isinstance(pols, list) and pols:
                    politicians.extend(_normalize_ct_politician(p) for p in pols)
                    pagination = props.get("pagination") or props.get("meta") or {}
                    total = int(pagination.get("totalPages") or pagination.get("total_pages") or 1)
                    if page >= total:
                        return politicians
                    page += 1
                    break
            else:
                break
            continue

        soup = BeautifulSoup(r.text, "lxml")
        added = 0
        for elem in soup.find_all("a", href=re.compile(r"/politicians/[^/?#]+$")):
            slug = elem["href"].rstrip("/").split("/")[-1]
            if not slug or slug == "politicians":
                continue
            name = elem.get_text(separator=" ", strip=True)
            if not any(p["id"] == slug for p in politicians):
                politicians.append({"id": slug, "name": name, "party": "", "state": ""})
            added += 1

        if not added:
            break

        nxt = soup.find("a", string=re.compile(r"next|›|»", re.I)) or \
              soup.find("a", attrs={"aria-label": re.compile(r"next", re.I)})
        if not nxt or "disabled" in nxt.get("class", []):
            break
        page += 1

    return politicians


def _normalize_ct_politician(raw: dict) -> dict:
    return {
        "id":    raw.get("id") or raw.get("politicianId") or raw.get("slug") or "",
        "name":  raw.get("name") or raw.get("fullName") or raw.get("displayName") or "",
        "party": raw.get("party") or "",
        "state": raw.get("state") or "",
    }


def _ct_get_trades(politician_id: str, days: int) -> list[dict]:
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    trades: list[dict] = []
    page = 1
    while True:
        url = f"{BASE_URL}/politicians/{politician_id}"
        params = {"tab": "trades"} if page == 1 else {"tab": "trades", "page": page}
        r = _get(url, params=params)

        nd = _next_data(r.text)
        if nd:
            props = nd.get("props", {}).get("pageProps", {})
            raw_trades = (
                props.get("trades")
                or (props.get("data") or {}).get("trades")
                or props.get("data")
                or []
            )
            if isinstance(raw_trades, list):
                for t in raw_trades:
                    parsed = _normalize_ct_trade(t)
                    if parsed and parsed["date"] >= cutoff:
                        trades.append(parsed)
                pagination = props.get("pagination") or props.get("meta") or {}
                total = int(pagination.get("totalPages") or pagination.get("total_pages") or 1)
                if page >= total:
                    break
                page += 1
                continue

        soup = BeautifulSoup(r.text, "lxml")
        rows = soup.select("table tbody tr") or [
            tr for tr in soup.find_all("tr")
            if tr.find(string=re.compile(r"^[A-Z]{1,5}$"))
        ]
        old_on_page = False
        for row in rows:
            t = _parse_html_row(row)
            if t is None:
                continue
            if t["date"] < cutoff:
                old_on_page = True
            else:
                trades.append(t)
        if old_on_page or not rows:
            break
        nxt = soup.find("a", string=re.compile(r"next|›|»", re.I)) or \
              soup.find("a", attrs={"aria-label": re.compile(r"next", re.I)})
        if not nxt or "disabled" in nxt.get("class", []):
            break
        page += 1

    return trades


def _normalize_ct_trade(raw: dict) -> dict | None:
    try:
        ticker = (
            raw.get("ticker") or raw.get("symbol")
            or (raw.get("asset") or {}).get("ticker") or ""
        ).upper().strip()
        if not ticker:
            return None
        tt = (raw.get("type") or raw.get("transactionType") or "").lower()
        if "buy" in tt or "purchase" in tt:
            tt = "buy"
        elif "sell" in tt or "sale" in tt:
            tt = "sell"
        else:
            return None
        amt = str(raw.get("amount") or raw.get("size") or raw.get("value") or "")
        date_str = str(
            raw.get("date") or raw.get("tradeDate") or raw.get("transactionDate") or ""
        )
        d = _parse_date(date_str)
        if d is None:
            return None
        return {"ticker": ticker, "type": tt, "amount": amt, "amount_mid": _parse_amount(amt), "date": d}
    except Exception:
        return None


def _parse_html_row(row) -> dict | None:
    try:
        cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
        ticker = next((t for t in cells if re.fullmatch(r"[A-Z]{1,5}", t)), None)
        if not ticker:
            return None
        tt = None
        for c in cells:
            lc = c.lower()
            if "buy" in lc or "purchase" in lc:
                tt = "buy"; break
            if "sell" in lc or "sale" in lc:
                tt = "sell"; break
        if tt is None:
            return None
        amt = next((c for c in cells if "$" in c or re.search(r"\d{1,3},\d{3}", c)), "")
        d = next((d for c in cells if (d := _parse_date(c)) is not None), None)
        if d is None:
            return None
        return {"ticker": ticker, "type": tt, "amount": amt, "amount_mid": _parse_amount(amt), "date": d}
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Source 2: Official House PTR (Periodic Transaction Reports)
# Zip files published at disclosures-clerk.house.gov — no auth, no bot block
# ---------------------------------------------------------------------------

# Module-level cache: {year: [raw_row_dicts]}
_house_ptr_cache: dict[int, list[dict]] = {}


def _fetch_house_ptr_year(year: int) -> list[dict]:
    """Download and parse the House PTR CSV for one calendar year."""
    if year in _house_ptr_cache:
        return _house_ptr_cache[year]

    url = f"https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}FDptr.zip"
    logger.info("Downloading House PTR data for %d …", year)
    r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()

    rows: list[dict] = []
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        csv_names = [n for n in zf.namelist() if n.lower().endswith((".csv", ".txt"))]
        for csv_name in csv_names:
            with zf.open(csv_name) as f:
                reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig", errors="replace"))
                rows.extend(reader)

    _house_ptr_cache[year] = rows
    logger.info("House PTR %d: %d rows loaded", year, len(rows))
    return rows


def _house_ptr_all_rows(days: int) -> list[dict]:
    cutoff_year = (datetime.now(tz=timezone.utc) - timedelta(days=days)).year
    current_year = datetime.now(tz=timezone.utc).year
    rows: list[dict] = []
    for year in range(cutoff_year, current_year + 1):
        try:
            rows.extend(_fetch_house_ptr_year(year))
        except Exception as e:
            logger.warning("House PTR download failed for %d: %s", year, e)
    return rows


def _house_row_to_trade(row: dict) -> dict | None:
    """Normalise a House PTR CSV row into a trade dict."""
    try:
        ticker = (row.get("Ticker") or "").strip().upper()
        # Some rows use asset description if no ticker
        if not ticker or not re.fullmatch(r"[A-Z]{1,5}", ticker):
            return None

        tt_raw = (row.get("TransactionType") or "").lower()
        if "purchase" in tt_raw or "buy" in tt_raw:
            tt = "buy"
        elif "sale" in tt_raw or "sell" in tt_raw:
            tt = "sell"
        elif "exchange" in tt_raw:
            tt = "buy"  # treat exchange as buy
        else:
            return None

        amt = row.get("Amount") or ""
        # Prefer TransactionDate; fall back to FilingDate
        d = _parse_date(row.get("TransactionDate") or row.get("FilingDate") or "")
        if d is None:
            return None

        first = (row.get("First") or "").strip()
        last  = (row.get("Last")  or "").strip()
        name  = f"{first} {last}".strip()
        pol_id = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

        return {
            "ticker":           ticker,
            "type":             tt,
            "amount":           amt,
            "amount_mid":       _parse_amount(amt),
            "date":             d,
            "politician_name":  name,
            "politician_id":    pol_id,
        }
    except Exception:
        return None


def _house_get_politicians(days: int = 365) -> list[dict]:
    rows = _house_ptr_all_rows(days)
    seen: dict[str, dict] = {}
    for row in rows:
        first = (row.get("First") or "").strip()
        last  = (row.get("Last")  or "").strip()
        name  = f"{first} {last}".strip()
        pol_id = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if pol_id and pol_id not in seen:
            seen[pol_id] = {"id": pol_id, "name": name, "party": "", "state": ""}
    return list(seen.values())


def _house_get_trades(politician_id: str, days: int = 365) -> list[dict]:
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    rows = _house_ptr_all_rows(days)
    trades = []
    for row in rows:
        t = _house_row_to_trade(row)
        if t and t["politician_id"] == politician_id and t["date"] >= cutoff:
            trades.append(t)
    return trades


# ---------------------------------------------------------------------------
# Public API — tries capitoltrades.com, falls back to House PTR
# ---------------------------------------------------------------------------

_use_house_fallback: bool = False  # set True after first CT failure


def get_politicians() -> list[dict]:
    global _use_house_fallback
    if not _use_house_fallback:
        try:
            pols = _ct_get_politicians()
            if pols:
                logger.info("capitoltrades.com: %d politicians", len(pols))
                return pols
        except Exception as e:
            logger.warning("capitoltrades.com unavailable (%s) — switching to House PTR fallback", e)
            _use_house_fallback = True

    logger.info("Using House PTR fallback data source")
    return _house_get_politicians()


def get_politician_trades(politician_id: str, days: int = 365) -> list[dict]:
    global _use_house_fallback
    if not _use_house_fallback:
        try:
            return _ct_get_trades(politician_id, days)
        except Exception as e:
            logger.warning("capitoltrades.com failed for %s (%s) — using House PTR", politician_id, e)
            _use_house_fallback = True

    return _house_get_trades(politician_id, days)
