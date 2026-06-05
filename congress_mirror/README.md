# Congress Mirror

Automatically ranks members of Congress by their last-12-months stock returns
and mirrors the **top performer's** open positions into your Alpaca **paper**
account. Re-checks for new disclosures every weekday at market open and emails
you a summary.

> ⚠️ Paper trading / educational only. Disclosure data is delayed (the STOCK Act
> allows up to ~45 days), amounts are broad ranges, and past performance does
> not predict future returns. This is **not** financial advice.

## Setup

```bash
pip install -r requirements.txt        # or: pip install -e ".[dev]"
```

Credentials live in `.env` (falls back to `.env.example`). Alpaca keys are
already filled in. To enable the daily email, set a Gmail **App Password**
(https://myaccount.google.com/apppasswords) as `EMAIL_SMTP_PASSWORD`.

## Commands

```bash
python run_mirror.py diagnose          # test connectivity to data sources + Alpaca
python run_mirror.py status            # show account balance + current positions
python run_mirror.py rank              # print the 12-month return ranking
python run_mirror.py once --dry-run    # full cycle, NO orders placed
python run_mirror.py once              # full cycle, places paper orders
python run_mirror.py start             # scheduler: runs now + every weekday 09:30 ET
```

**Start here:** run `python run_mirror.py diagnose` first. It tells you which
data source is reachable from your machine and whether Alpaca is connected.

## How it works

1. **Data** — downloads structured congressional trade disclosures from the
   public House/Senate Stock Watcher datasets (no auth). If those are
   unreachable it falls back to the capitoltrades.com JSON API via
   `cloudscraper`. (capitoltrades.com's normal site is Cloudflare-protected and
   blocks scripts, which is why the structured datasets are preferred.)
2. **Returns** — for each politician, every disclosed buy still held is priced
   from its disclosure date to today via `yfinance`. Returns are dollar-weighted
   by the midpoint of each disclosed amount range.
3. **Ranking** — politicians with at least `MIN_TRADES_FOR_RANKING` buys are
   ranked best-to-worst by weighted return.
4. **Mirror** — the top performer's open positions are reproduced in your Alpaca
   paper account, weighted by disclosed size, up to `MAX_POSITIONS`, deploying
   `PORTFOLIO_FRACTION` of buying power. Positions the top performer no longer
   holds are sold.
5. **Email** — an HTML summary (top performer, ranking, new disclosures, trades
   executed, account snapshot) is sent after each cycle.

## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `MIN_TRADES_FOR_RANKING` | 5 | Min disclosed buys to be eligible |
| `LOOKBACK_DAYS` | 365 | Rolling return window |
| `PORTFOLIO_FRACTION` | 0.90 | Fraction of buying power to deploy |
| `MAX_POSITIONS` | 15 | Max simultaneous positions |
