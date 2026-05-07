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
