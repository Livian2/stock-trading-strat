"""Send daily email summaries via SMTP."""
from __future__ import annotations

import logging
import smtplib
import textwrap
from datetime import UTC, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from . import config

logger = logging.getLogger(__name__)


def send_summary(summary: dict) -> bool:
    """
    Email a formatted daily summary.

    Returns True if the email was sent successfully.
    Logs a warning (does not raise) on failure.
    """
    if not config.EMAIL_SMTP_PASSWORD or config.EMAIL_SMTP_PASSWORD == "YOUR_GMAIL_APP_PASSWORD_HERE":
        logger.warning(
            "EMAIL_SMTP_PASSWORD is not configured – skipping email. "
            "Set it in .env to enable daily summaries."
        )
        return False

    subject, html_body, text_body = _build_email(summary)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config.EMAIL_SENDER
    msg["To"] = config.EMAIL_RECIPIENT
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(config.EMAIL_SMTP_SERVER, config.EMAIL_SMTP_PORT) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(config.EMAIL_SENDER, config.EMAIL_SMTP_PASSWORD)
            smtp.sendmail(config.EMAIL_SENDER, config.EMAIL_RECIPIENT, msg.as_string())
        logger.info("Email sent to %s", config.EMAIL_RECIPIENT)
        return True
    except Exception as e:
        logger.error("Failed to send email: %s", e)
        return False


def _build_email(s: dict) -> tuple[str, str, str]:
    """Return (subject, html, plaintext) for the summary dict."""
    run_at = s.get("run_at", datetime.now(tz=UTC).isoformat())
    top = s.get("top_performer") or {}
    ranked = s.get("ranked", [])
    new_disc = s.get("new_disclosures", [])
    trades = s.get("trades_executed", [])
    account = s.get("account", {})
    errors = s.get("errors", [])

    date_str = run_at[:10]
    subject = f"Congress Mirror Daily Summary – {date_str}"

    # ---- Plain text ----
    lines: list[str] = [
        f"Congress Mirror  |  {run_at}",
        "=" * 55,
    ]

    if top:
        lines += [
            "",
            f"TOP PERFORMER: {top.get('name', '?')}",
            f"  12-month return : {top.get('return', 0) * 100:.1f}%",
            f"  Trade count     : {top.get('trade_count', 0)}",
        ]

    if account:
        lines += [
            "",
            "ACCOUNT",
            f"  Portfolio value : ${account.get('portfolio_value', 0):,.2f}",
            f"  Cash            : ${account.get('cash', 0):,.2f}",
            f"  Buying power    : ${account.get('buying_power', 0):,.2f}",
        ]

    if trades:
        lines += ["", "TRADES EXECUTED"]
        for t in trades:
            dry = " [DRY RUN]" if t.get("dry_run") else ""
            action = t.get("action") or t.get("side", "").upper()
            sym = t.get("symbol", "?")
            if t.get("notional"):
                lines.append(f"  {action} {sym}  ${t['notional']:,.2f}{dry}")
            elif t.get("qty"):
                lines.append(f"  {action} {sym}  {t['qty']} shares{dry}")
            else:
                lines.append(f"  {action} {sym}{dry}")

    if new_disc:
        lines += ["", f"NEW DISCLOSURES ({len(new_disc)})"]
        for d in new_disc[:20]:
            lines.append(
                f"  {d.get('date','')[:10]}  {d.get('politician_name','?')}  "
                f"{d.get('type','').upper()} {d.get('ticker','?')}  {d.get('amount','?')}"
            )
        if len(new_disc) > 20:
            lines.append(f"  … and {len(new_disc) - 20} more")

    if ranked:
        lines += ["", "TOP 10 RANKING"]
        for i, r in enumerate(ranked[:10], 1):
            lines.append(
                f"  {i:2d}. {r.get('name','?'):<30s} "
                f"{r.get('return', 0) * 100:+.1f}%  ({r.get('trade_count', 0)} trades)"
            )

    if errors:
        lines += ["", "ERRORS"]
        for e in errors:
            lines += textwrap.wrap(f"  {e}", width=72)

    text_body = "\n".join(lines)

    # ---- HTML ----
    def pct(v: float) -> str:
        sign = "+" if v >= 0 else ""
        color = "#16a34a" if v >= 0 else "#dc2626"
        return f'<span style="color:{color};font-weight:bold">{sign}{v*100:.1f}%</span>'

    rows_ranked = "".join(
        f"<tr><td>{i}</td><td>{r.get('name','')}</td>"
        f"<td>{pct(r.get('return',0))}</td>"
        f"<td>{r.get('trade_count',0)}</td></tr>"
        for i, r in enumerate(ranked[:10], 1)
    )

    rows_disc = "".join(
        f"<tr><td>{d.get('date','')[:10]}</td><td>{d.get('politician_name','')}</td>"
        f"<td><b>{d.get('type','').upper()}</b></td>"
        f"<td>{d.get('ticker','')}</td><td>{d.get('amount','')}</td></tr>"
        for d in new_disc[:20]
    )

    rows_trades = "".join(
        f"<tr><td>{t.get('action') or t.get('side','').upper()}</td>"
        f"<td>{t.get('symbol','')}</td>"
        f"<td>${t.get('notional',0):,.2f}</td>"
        f"<td>{'DRY RUN' if t.get('dry_run') else 'EXECUTED'}</td></tr>"
        for t in trades
    )

    html_body = f"""
<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;max-width:700px;margin:auto">
<h2 style="background:#1e3a5f;color:white;padding:12px">Congress Mirror – {date_str}</h2>

{''.join(f'<p style="background:#fef3c7;padding:8px;border-left:4px solid #f59e0b">⚠ {e}</p>' for e in errors)}

<h3>Top Performer</h3>
<table style="border-collapse:collapse;width:100%">
  <tr><th style="text-align:left">Name</th><th>12-month Return</th><th>Trades</th></tr>
  <tr>
    <td><b>{top.get('name','–')}</b></td>
    <td>{pct(top.get('return',0))}</td>
    <td>{top.get('trade_count',0)}</td>
  </tr>
</table>

<h3>Account</h3>
<table style="border-collapse:collapse;width:100%">
  <tr><td>Portfolio Value</td><td><b>${account.get('portfolio_value',0):,.2f}</b></td></tr>
  <tr style="background:#f8f8f8"><td>Cash</td><td>${account.get('cash',0):,.2f}</td></tr>
  <tr><td>Buying Power</td><td>${account.get('buying_power',0):,.2f}</td></tr>
</table>

{'<h3>Trades Executed</h3><table style="border-collapse:collapse;width:100%"><tr><th>Action</th><th>Symbol</th><th>Notional</th><th>Status</th></tr>' + rows_trades + '</table>' if trades else ''}

{'<h3>New Disclosures</h3><table style="border-collapse:collapse;width:100%"><tr><th>Date</th><th>Politician</th><th>Type</th><th>Ticker</th><th>Amount</th></tr>' + rows_disc + '</table>' if new_disc else '<p>No new disclosures since last run.</p>'}

<h3>Full Ranking (top 10)</h3>
<table style="border-collapse:collapse;width:100%">
  <tr><th>#</th><th>Name</th><th>12-mo Return</th><th>Trades</th></tr>
  {rows_ranked}
</table>

<p style="color:grey;font-size:11px">Generated {run_at}. Paper trading only – not financial advice.</p>
</body></html>
"""
    return subject, html_body, text_body
