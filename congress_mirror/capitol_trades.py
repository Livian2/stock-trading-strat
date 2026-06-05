"""
Scraper for capitoltrades.com.

Strategy:
  1. Try to extract the embedded Next.js __NEXT_DATA__ JSON (fast, no HTML parsing).
  2. Fall back to BeautifulSoup HTML parsing.
  3. Paginate automatically until all records are collected.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://www.capitoltrades.com"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}

# Midpoints of STOCK Act disclosure ranges (used for position sizing / return weighting)
AMOUNT_MIDPOINTS: dict[str, float] = {
    "$1,001 - $15,000":       8_000,
    "$15,001 - $50,000":     32_500,
    "$50,001 - $100,000":    75_000,
    "$100,001 - $250,000":  175_000,
    "$250,001 - $500,000":  375_000,
    "$500,001 - $1,000,000": 750_000,
    "$1,000,001 - $5,000,000": 3_000_000,
    "Over $5,000,000":       5_000_000,
}


def _parse_amount(text: str) -> float:
    """Return dollar midpoint for a STOCK Act range string."""
    text = text.strip()
    for pattern, mid in AMOUNT_MIDPOINTS.items():
        if text.lower() in pattern.lower() or pattern.lower() in text.lower():
            return mid
    # Numeric fallback: strip symbols and parse
    nums = re.findall(r"[\d,]+", text)
    if len(nums) >= 2:
        lo = float(nums[0].replace(",", ""))
        hi = float(nums[1].replace(",", ""))
        return (lo + hi) / 2
    if len(nums) == 1:
        return float(nums[0].replace(",", ""))
    return 8_000  # default to smallest range midpoint


_session = requests.Session()
_session.headers.update(_HEADERS)


def _get(url: str, params: dict | None = None, retries: int = 3) -> requests.Response:
    """GET with retry and polite rate-limiting."""
    for attempt in range(retries):
        try:
            time.sleep(1.5 + attempt)
            r = _session.get(url, params=params, timeout=20)
            r.raise_for_status()
            return r
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                wait = 10 * (attempt + 1)
                logger.warning("Rate-limited; sleeping %ds", wait)
                time.sleep(wait)
            elif attempt == retries - 1:
                raise
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Failed to fetch {url} after {retries} attempts")


def _next_data(html: str) -> dict | None:
    """Extract __NEXT_DATA__ JSON embedded by Next.js, if present."""
    soup = BeautifulSoup(html, "lxml")
    tag = soup.find("script", id="__NEXT_DATA__")
    if tag and tag.string:
        try:
            return json.loads(tag.string)
        except json.JSONDecodeError:
            return None
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_politicians() -> list[dict]:
    """
    Return list of all politicians on capitoltrades.com.

    Each dict has at least: id, name.
    Optional fields (when available): party, state, trade_count.
    """
    politicians: list[dict] = []
    page = 1

    while True:
        logger.info("Fetching politicians page %d", page)
        r = _get(f"{BASE_URL}/politicians", params={"page": page} if page > 1 else None)
        html = r.text

        # --- Next.js path ---
        nd = _next_data(html)
        if nd:
            props = nd.get("props", {}).get("pageProps", {})
            # The data key varies; try common names
            for key in ("politicians", "data", "results", "items"):
                pols = props.get(key)
                if isinstance(pols, list) and pols:
                    for p in pols:
                        politicians.append(_normalize_politician(p))
                    pagination = props.get("pagination", props.get("meta", {}))
                    total = pagination.get("totalPages", pagination.get("total_pages", 1))
                    if page >= int(total):
                        return politicians
                    page += 1
                    break
            else:
                break  # Next.js data present but unrecognized shape — fall through to HTML
            continue

        # --- HTML path ---
        soup = BeautifulSoup(html, "lxml")
        added = 0

        # Pattern A: cards/articles with a link to /politicians/<slug>
        for elem in soup.find_all("a", href=re.compile(r"/politicians/[^/]+$")):
            slug = elem["href"].rstrip("/").split("/")[-1]
            if not slug or slug == "politicians":
                continue
            name = elem.get_text(separator=" ", strip=True)
            # Avoid duplicates from multiple links to the same politician
            if not any(p["id"] == slug for p in politicians):
                politicians.append({"id": slug, "name": name})
            added += 1

        # Pattern B: table rows
        if not added:
            for row in soup.select("table tbody tr"):
                link = row.find("a", href=re.compile(r"/politicians/"))
                if link:
                    slug = link["href"].rstrip("/").split("/")[-1]
                    name = link.get_text(strip=True)
                    if not any(p["id"] == slug for p in politicians):
                        politicians.append({"id": slug, "name": name})
                    added += 1

        if not added:
            logger.warning("No politicians found on page %d; stopping pagination", page)
            break

        # Check for a "next page" link
        next_link = soup.find(
            "a",
            string=re.compile(r"next|›|»|>", re.I),
        ) or soup.find("a", attrs={"aria-label": re.compile(r"next", re.I)})

        if not next_link or "disabled" in next_link.get("class", []):
            break
        page += 1

    return politicians


def _normalize_politician(raw: dict) -> dict:
    """Normalise a politician record from either Next.js JSON or HTML."""
    # Accept camelCase or snake_case keys
    return {
        "id":          raw.get("id") or raw.get("politicianId") or raw.get("slug") or "",
        "name":        raw.get("name") or raw.get("fullName") or raw.get("displayName") or "",
        "party":       raw.get("party") or "",
        "state":       raw.get("state") or "",
        "trade_count": raw.get("tradeCount") or raw.get("trade_count") or 0,
    }


def get_politician_trades(politician_id: str, days: int = 365) -> list[dict]:
    """
    Return all trades for a politician over the last `days` days.

    Each dict contains: ticker, type (buy/sell), amount, amount_mid, date.
    """
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    trades: list[dict] = []
    page = 1

    while True:
        url = f"{BASE_URL}/politicians/{politician_id}"
        logger.info("Fetching trades for %s page %d", politician_id, page)
        r = _get(url, params={"page": page, "tab": "trades"} if page > 1 else {"tab": "trades"})
        html = r.text

        # --- Next.js path ---
        nd = _next_data(html)
        if nd:
            props = nd.get("props", {}).get("pageProps", {})
            raw_trades = (
                props.get("trades")
                or props.get("data", {}).get("trades")
                or props.get("data")
                or []
            )
            if isinstance(raw_trades, list):
                for t in raw_trades:
                    parsed = _normalize_trade(t)
                    if parsed and parsed["date"] >= cutoff:
                        trades.append(parsed)
                pagination = props.get("pagination", props.get("meta", {}))
                total = int(pagination.get("totalPages", pagination.get("total_pages", 1)))
                if page >= total:
                    break
                page += 1
                continue

        # --- HTML path ---
        soup = BeautifulSoup(html, "lxml")
        rows = soup.select("table tbody tr") or soup.select("[data-trade-row]")
        if not rows:
            # Try generic row detection: look for ticker-like cells
            rows = [
                tr for tr in soup.find_all("tr")
                if tr.find(string=re.compile(r"^[A-Z]{1,5}$"))
            ]

        page_had_old = False
        for row in rows:
            t = _parse_trade_row(row)
            if t is None:
                continue
            if t["date"] < cutoff:
                page_had_old = True
                continue
            trades.append(t)

        if page_had_old or not rows:
            break

        next_link = soup.find("a", string=re.compile(r"next|›|»", re.I)) or soup.find(
            "a", attrs={"aria-label": re.compile(r"next", re.I)}
        )
        if not next_link or "disabled" in next_link.get("class", []):
            break
        page += 1

    return trades


def _normalize_trade(raw: dict) -> dict | None:
    """Normalise a trade record from Next.js JSON."""
    try:
        ticker = (
            raw.get("ticker") or raw.get("symbol") or raw.get("asset", {}).get("ticker") or ""
        )
        ticker = ticker.upper().strip()
        if not ticker:
            return None

        trade_type = (raw.get("type") or raw.get("transactionType") or "").lower()
        if "buy" in trade_type or "purchase" in trade_type:
            trade_type = "buy"
        elif "sell" in trade_type or "sale" in trade_type:
            trade_type = "sell"
        else:
            return None

        amount_str = str(raw.get("amount") or raw.get("size") or raw.get("value") or "")
        amount_mid = _parse_amount(amount_str)

        date_str = str(raw.get("date") or raw.get("tradeDate") or raw.get("transactionDate") or "")
        trade_date = _parse_date(date_str)
        if trade_date is None:
            return None

        return {
            "ticker":     ticker,
            "type":       trade_type,
            "amount":     amount_str,
            "amount_mid": amount_mid,
            "date":       trade_date,
        }
    except Exception:
        return None


def _parse_trade_row(row) -> dict | None:
    """Parse a BeautifulSoup <tr> element into a trade dict."""
    try:
        cells = row.find_all(["td", "th"])
        text_cells = [c.get_text(strip=True) for c in cells]

        # Find ticker (1–5 uppercase letters)
        ticker = next(
            (t for t in text_cells if re.fullmatch(r"[A-Z]{1,5}", t)),
            None,
        )
        if not ticker:
            return None

        # Find trade type
        trade_type = None
        for cell in text_cells:
            lc = cell.lower()
            if "buy" in lc or "purchase" in lc:
                trade_type = "buy"
                break
            if "sell" in lc or "sale" in lc:
                trade_type = "sell"
                break
        if trade_type is None:
            return None

        # Find amount range (look for $ or range-like text)
        amount_str = next(
            (t for t in text_cells if "$" in t or re.search(r"\d{1,3},\d{3}", t)),
            "",
        )
        amount_mid = _parse_amount(amount_str)

        # Find date (YYYY-MM-DD or MM/DD/YYYY)
        date_obj = None
        for cell in text_cells:
            date_obj = _parse_date(cell)
            if date_obj:
                break
        if date_obj is None:
            return None

        return {
            "ticker":     ticker,
            "type":       trade_type,
            "amount":     amount_str,
            "amount_mid": amount_mid,
            "date":       date_obj,
        }
    except Exception:
        return None


def _parse_date(text: str) -> datetime | None:
    """Try several date formats; return timezone-aware datetime or None."""
    text = text.strip()
    short_formats = ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"]
    long_formats  = ["%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y"]
    for fmt in short_formats:
        try:
            return datetime.strptime(text[:10], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    for fmt in long_formats:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    # ISO 8601 with time component
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    return None
