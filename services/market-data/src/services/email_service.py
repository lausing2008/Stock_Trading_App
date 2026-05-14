"""Email delivery — supports Gmail SMTP and AWS SES.

Configure via .env:
  EMAIL_PROVIDER=smtp   → Gmail (or any SMTP relay)
  EMAIL_PROVIDER=ses    → AWS SES (boto3 must be installed + IAM role/creds set)
  EMAIL_PROVIDER=       → disabled (alerts still record in DB, no mail sent)
"""
from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from common.config import get_settings
from common.logging import get_logger

log = get_logger("email_service")
_settings = get_settings()


def _build_message(to: str, subject: str, body_html: str, body_text: str) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = _settings.email_from
    msg["To"] = to
    msg.attach(MIMEText(body_text, "plain"))
    msg.attach(MIMEText(body_html, "html"))
    return msg


def _send_smtp(to: str, subject: str, body_html: str, body_text: str) -> None:
    msg = _build_message(to, subject, body_html, body_text)
    with smtplib.SMTP(_settings.smtp_host, _settings.smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(_settings.smtp_user, _settings.smtp_password)
        server.sendmail(_settings.email_from, to, msg.as_string())


def _send_ses(to: str, subject: str, body_html: str, body_text: str) -> None:
    import boto3
    client = boto3.client("ses", region_name=_settings.ses_region)
    client.send_email(
        Source=_settings.email_from,
        Destination={"ToAddresses": [to]},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {
                "Text": {"Data": body_text, "Charset": "UTF-8"},
                "Html": {"Data": body_html, "Charset": "UTF-8"},
            },
        },
    )


def send_email(to: str, subject: str, body_html: str, body_text: str) -> bool:
    """Send an email. Returns True on success, False on failure or disabled."""
    provider = _settings.email_provider.lower()
    if not provider:
        log.info("email.disabled", to=to, subject=subject)
        return False
    if not _settings.email_from:
        log.warning("email.no_from_address")
        return False
    try:
        if provider == "smtp":
            _send_smtp(to, subject, body_html, body_text)
        elif provider == "ses":
            _send_ses(to, subject, body_html, body_text)
        else:
            log.warning("email.unknown_provider", provider=provider)
            return False
        log.info("email.sent", provider=provider, to=to, subject=subject)
        return True
    except Exception as exc:
        log.error("email.failed", provider=provider, to=to, error=str(exc))
        return False


def send_signal_alert_email(
    to: str, symbol: str, prev_signal: str | None, new_signal: str, analyst: str,
    signal_data: dict | None = None,
    fundamentals: dict | None = None,
) -> bool:
    direction_map = {
        ("SELL", "HOLD"): ("cautious",  "moving out of sell territory"),
        ("SELL", "BUY"):  ("bullish",   "reversing from SELL directly to BUY"),
        ("HOLD", "BUY"):  ("bullish",   "confirming a buy signal"),
        ("WAIT", "HOLD"): ("cautious",  "stabilising from a bearish lean"),
        ("WAIT", "BUY"):  ("bullish",   "turning bullish from a wait signal"),
    }
    mood, desc = direction_map.get((prev_signal, new_signal), ("bullish", "improving"))
    color = "#22c55e" if mood == "bullish" else "#facc15"

    # Build reasons summary from signal_data
    reasons = signal_data.get("reasons", {}) if signal_data else {}
    bullish_prob = signal_data.get("bullish_probability") if signal_data else None
    confidence   = signal_data.get("confidence") if signal_data else None
    ml_prob      = reasons.get("ml_probability")

    def _yn(v) -> str:
        return "Yes" if v else "No"
    def _fmt(v, d=1) -> str:
        return f"{v:.{d}f}" if v is not None else "—"

    rsi_val  = reasons.get("rsi")
    rsi_note = ""
    if rsi_val is not None:
        if rsi_val < 35:   rsi_note = " — oversold, potential reversal"
        elif rsi_val < 50: rsi_note = " — below midline, recovering"
        elif rsi_val < 65: rsi_note = " — healthy bullish zone"
        elif rsi_val < 75: rsi_note = " — strong momentum"
        else:              rsi_note = " — overbought, watch for pullback"

    adx_val = reasons.get("adx")
    adx_note = ""
    if adx_val is not None:
        if adx_val < 20:   adx_note = " (weak / choppy)"
        elif adx_val < 35: adx_note = " (moderate trend)"
        else:              adx_note = " (strong trend)"

    # Earnings calendar
    next_earnings = fundamentals.get("next_earnings_date") if fundamentals else None
    days_to_earnings = fundamentals.get("days_to_earnings") if fundamentals else None
    earnings_note = "—"
    earnings_warn = ""
    if next_earnings:
        earnings_note = f"{next_earnings}"
        if days_to_earnings is not None:
            earnings_note += f" ({days_to_earnings}d away)"
            if days_to_earnings <= 7:
                earnings_warn = "⚠ Earnings within 7 days — results may override the signal"
            elif days_to_earnings <= 21:
                earnings_warn = "Note: Earnings within 3 weeks — watch for volatility"

    # Insider activity
    insider_buy = fundamentals.get("insider_buy_shares_6m") if fundamentals else None
    insider_sell = fundamentals.get("insider_sell_shares_6m") if fundamentals else None
    insider_net_pct = fundamentals.get("insider_net_pct") if fundamentals else None
    insider_note = "—"
    if insider_buy is not None or insider_sell is not None:
        b = insider_buy or 0
        s = insider_sell or 0
        net = b - s
        insider_note = f"Buys {b:,}  /  Sales {s:,}  →  Net {'+' if net >= 0 else ''}{net:,}"
        if insider_net_pct is not None:
            insider_note += f"  ({insider_net_pct*100:+.2f}% of float)"

    reason_rows = [
        ("Trend above SMA50",    _yn(reasons.get("trend_above_sma50"))),
        ("SMA50 above SMA200",   _yn(reasons.get("sma50_above_sma200"))),
        ("Golden cross fired",   _yn(reasons.get("golden_cross_event"))),
        ("RSI (14)",             f"{_fmt(rsi_val)}{rsi_note}"),
        ("MACD histogram",       f"{_fmt(reasons.get('macd_hist'), 3)} {'↑ rising' if reasons.get('macd_rising') else '↓ flat/falling'}"),
        ("Bollinger %B",         _fmt(reasons.get("bb_pct_b"), 2)),
        ("ADX",                  f"{_fmt(adx_val)}{adx_note}"),
        ("Volume (OBV bullish)", _yn(reasons.get("obv_bullish"))),
        ("Volume Z-score",       _fmt(reasons.get("volume_z"), 2)),
        ("ML probability",       f"{float(ml_prob)*100:.1f}% bullish" if ml_prob is not None else "—"),
        ("Next earnings",        earnings_note),
        ("Insider activity (6M)", insider_note),
    ]

    rows_html = "".join(
        f'<tr><td style="padding:6px 10px;color:#64748b;font-size:13px;border-bottom:1px solid #f1f5f9">{k}</td>'
        f'<td style="padding:6px 10px;font-size:13px;font-weight:600;color:#1e293b;border-bottom:1px solid #f1f5f9">{v}</td></tr>'
        for k, v in reason_rows
    )
    rows_text = "\n".join(f"  {k}: {v}" for k, v in reason_rows)

    subject = f"Signal Alert: {symbol} {prev_signal} → {new_signal} (Analyst: {analyst.upper()})"
    body_text = (
        f"Your signal alert for {symbol} has fired.\n\n"
        f"AI Signal: {prev_signal} → {new_signal} ({desc})\n"
        f"Analyst consensus: {analyst.upper()}\n"
        + (f"Bullish probability: {float(bullish_prob)*100:.1f}%  |  Confidence: {float(confidence):.1f}%\n" if bullish_prob is not None else "")
        + f"\nWhy the signal changed:\n{rows_text}\n\n"
        + (f"{earnings_warn}\n\n" if earnings_warn else "")
        + f"Both indicators are now aligned — review the stock detail before acting.\n\n"
        f"Not personalised financial advice. Always do your own research.\n"
    )
    body_html = f"""
<html><body style="font-family:sans-serif;color:#1e293b;background:#f8fafc;padding:24px">
  <div style="max-width:520px;margin:auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08)">
    <h2 style="margin-top:0;color:#6366f1">&#128202; StockAI Signal Alert</h2>
    <p style="font-size:16px"><strong>{symbol}</strong> AI Signal has changed:</p>

    <div style="background:#f1f5f9;border-radius:8px;padding:16px;margin:16px 0;display:flex;align-items:center;gap:24px">
      <div style="text-align:center">
        <div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:.06em">From</div>
        <div style="font-size:22px;font-weight:800;color:#ef4444">{prev_signal}</div>
      </div>
      <div style="font-size:24px;color:#94a3b8">&#8594;</div>
      <div style="text-align:center">
        <div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:.06em">To</div>
        <div style="font-size:22px;font-weight:800;color:{color}">{new_signal}</div>
      </div>
      {f'<div style="margin-left:auto;text-align:right"><div style="font-size:11px;color:#94a3b8">Bullish prob</div><div style="font-size:20px;font-weight:800;color:{color}">{float(bullish_prob)*100:.0f}%</div><div style="font-size:10px;color:#94a3b8">Confidence {float(confidence):.0f}%</div></div>' if bullish_prob is not None else ""}
    </div>

    <p style="font-size:14px;color:#475569;margin:0 0 16px">
      Analyst consensus: <strong style="color:#6366f1">{analyst.upper()}</strong> &nbsp;·&nbsp; {desc.capitalize()}
    </p>

    <div style="font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">Why the signal changed</div>
    <table style="width:100%;border-collapse:collapse;background:#fafafa;border-radius:8px;overflow:hidden;border:1px solid #e2e8f0">
      {rows_html}
    </table>

    {f'<div style="background:#fef9c3;border:1px solid #fbbf24;border-radius:8px;padding:10px 14px;margin-top:16px;font-size:13px;color:#92400e">{earnings_warn}</div>' if earnings_warn else ''}
    <p style="font-size:11px;color:#94a3b8;margin-top:24px;border-top:1px solid #e2e8f0;padding-top:16px">
      Not personalised financial advice. Always do your own research before acting.
    </p>
  </div>
</body></html>"""
    return send_email(to, subject, body_html, body_text)


def send_price_alert_email(to: str, symbol: str, condition: str, threshold: float, price: float, note: str | None) -> bool:
    direction = "risen above" if condition == "above" else "fallen below"
    subject = f"Price Alert: {symbol} has {direction} {threshold}"
    body_text = (
        f"Your price alert for {symbol} has triggered.\n\n"
        f"{symbol} is now {price:.4f} ({direction} your target of {threshold}).\n"
        + (f"\nNote: {note}\n" if note else "")
        + "\nLog in to your StockAI dashboard to review.\n"
    )
    body_html = f"""
<html><body style="font-family:sans-serif;color:#1e293b;background:#f8fafc;padding:24px">
  <div style="max-width:480px;margin:auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08)">
    <h2 style="margin-top:0;color:#6366f1">📈 StockAI Price Alert</h2>
    <p style="font-size:16px"><strong>{symbol}</strong> has <strong>{direction}</strong> your target of <strong>{threshold}</strong>.</p>
    <div style="background:#f1f5f9;border-radius:8px;padding:16px;margin:16px 0">
      <div style="font-size:28px;font-weight:700;color:{'#22c55e' if condition == 'above' else '#ef4444'}">{price:.4f}</div>
      <div style="font-size:13px;color:#64748b;margin-top:4px">Current price</div>
    </div>
    {f'<p style="color:#64748b;font-size:14px"><em>{note}</em></p>' if note else ''}
    <p style="font-size:13px;color:#94a3b8;margin-top:24px">This alert has been marked as triggered and will not fire again.</p>
  </div>
</body></html>"""
    return send_email(to, subject, body_html, body_text)
