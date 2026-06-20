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
    if not (to or "").strip():
        log.warning("email.invalid_recipient", to=repr(to))
        return False
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
    game_plan: dict | None = None,
    conviction_layers: list[str] | None = None,
    near_conviction: bool = False,
    near_conviction_failed: list[str] | None = None,
    horizon: str | None = None,
) -> bool:
    direction_map = {
        ("SELL", "HOLD"): ("cautious",  "moving out of sell territory"),
        ("SELL", "BUY"):  ("bullish",   "reversing from SELL directly to BUY"),
        ("HOLD", "BUY"):  ("bullish",   "confirming a buy signal"),
        ("WAIT", "HOLD"): ("cautious",  "stabilising from a bearish lean"),
        ("WAIT", "BUY"):  ("bullish",   "turning bullish from a wait signal"),
        ("BUY",  "HOLD"): ("cautious",  "momentum fading — signal weakening from BUY"),
        ("BUY",  "WAIT"): ("bearish",   "deteriorating from BUY — consider reviewing position"),
        ("BUY",  "SELL"): ("bearish",   "reversing from BUY to SELL — exit signal"),
    }
    mood, desc = direction_map.get((prev_signal, new_signal), ("bullish", "improving"))
    color = "#22c55e" if mood == "bullish" else "#ef4444" if mood == "bearish" else "#facc15"

    _signal_color = {"BUY": "#22c55e", "HOLD": "#facc15", "WAIT": "#f97316", "SELL": "#ef4444"}
    prev_color = _signal_color.get(prev_signal or "", "#94a3b8")
    new_color  = _signal_color.get(new_signal, color)

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

    # Stochastic RSI
    stoch_k = reasons.get("stoch_rsi_k")
    stoch_note = ""
    if stoch_k is not None:
        pct = stoch_k * 100
        if pct < 20:   stoch_note = f" — oversold ({pct:.0f}), potential entry"
        elif pct > 80: stoch_note = f" — overbought ({pct:.0f}), caution"
        else:          stoch_note = f" ({pct:.0f})"
    stoch_cross = " ↑ crossed up from oversold" if reasons.get("stoch_rsi_cross_up") else ""

    # RSI divergence
    div = reasons.get("rsi_divergence", "none")
    div_note = {"bearish": "⚠ Bearish — price up but momentum fading",
                "bullish": "✓ Bullish — price down but momentum recovering"}.get(div, "None detected")

    # MACD zero-line
    macd_zero = " ✓ just crossed above zero" if reasons.get("macd_zero_cross_up") else ""

    # Death cross warning
    death_cross = reasons.get("death_cross_event", False)

    # Market regime
    regime = reasons.get("market_regime", "unknown")
    regime_note = {"bull": "Bull (S&P above 200MA) — normal thresholds",
                   "bear": "Bear (S&P below 200MA) — higher BUY threshold applied"}.get(regime, "Unknown")

    reason_rows = [
        ("Market regime",         regime_note),
        ("Trend above SMA50",     _yn(reasons.get("trend_above_sma50"))),
        ("SMA50 above SMA200",    _yn(reasons.get("sma50_above_sma200"))),
        ("Golden cross fired",    _yn(reasons.get("golden_cross_event"))),
        ("Death cross fired",     "⚠ Yes" if death_cross else "No"),
        ("RSI (14)",              f"{_fmt(rsi_val)}{rsi_note}"),
        ("Stoch RSI %K",          f"{_fmt(stoch_k, 3) if stoch_k is not None else '—'}{stoch_note}{stoch_cross}"),
        ("RSI divergence",        div_note),
        ("MACD histogram",        f"{_fmt(reasons.get('macd_hist'), 3)} {'↑ rising' if reasons.get('macd_rising') else '↓ flat/falling'}{macd_zero}"),
        ("Bollinger %B",          _fmt(reasons.get("bb_pct_b"), 2)),
        ("ADX",                   f"{_fmt(adx_val)}{adx_note}"),
        ("OBV trend (10/30 MA)",  _yn(reasons.get("obv_trend_bullish"))),
        ("Volume Z-score",        _fmt(reasons.get("volume_z"), 2)),
        ("ML probability",        f"{float(ml_prob)*100:.1f}% bullish" if ml_prob is not None else "—"),
        ("Next earnings",         earnings_note),
        ("Insider activity (6M)", insider_note),
    ]

    rows_html = "".join(
        f'<tr><td style="padding:6px 10px;color:#64748b;font-size:13px;border-bottom:1px solid #f1f5f9">{k}</td>'
        f'<td style="padding:6px 10px;font-size:13px;font-weight:600;color:#1e293b;border-bottom:1px solid #f1f5f9">{v}</td></tr>'
        for k, v in reason_rows
    )
    rows_text = "\n".join(f"  {k}: {v}" for k, v in reason_rows)

    # ── Conviction layer summary (only for BUY transitions) ───────────────
    conviction_html = ""
    conviction_text = ""
    if conviction_layers and new_signal == "BUY":
        layer_rows = "".join(
            f'<tr><td style="padding:5px 12px;font-size:13px;color:#166534;border-bottom:1px solid #bbf7d0">'
            f'<span style="color:#16a34a;font-weight:700;margin-right:8px">✓</span>{layer}</td></tr>'
            for layer in conviction_layers
        )
        if near_conviction and near_conviction_failed:
            failed_rows = "".join(
                f'<tr><td style="padding:5px 12px;font-size:13px;color:#92400e;border-bottom:1px solid #fef08a">'
                f'<span style="color:#ca8a04;font-weight:700;margin-right:8px">⚠</span>{layer}</td></tr>'
                for layer in near_conviction_failed
            )
            conviction_html = f"""
    <div style="margin-top:20px">
      <div style="font-size:11px;font-weight:700;color:#ca8a04;text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">⚡ Near-Conviction BUY — 1 Soft Check Missed</div>
      <table style="width:100%;border-collapse:collapse;background:#f0fdf4;border-radius:8px;overflow:hidden;border:1px solid #bbf7d0">
        {layer_rows}
      </table>
      <table style="width:100%;border-collapse:collapse;background:#fefce8;border-radius:8px;overflow:hidden;border:1px solid #fef08a;margin-top:6px">
        {failed_rows}
      </table>
    </div>"""
            conviction_text = "\n⚡ Near-Conviction BUY (1 soft check missed):\n" + "\n".join(f"  ✓ {l}" for l in conviction_layers) + "\n" + "\n".join(f"  ⚠ {l}" for l in near_conviction_failed) + "\n"
        else:
            conviction_html = f"""
    <div style="margin-top:20px">
      <div style="font-size:11px;font-weight:700;color:#16a34a;text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">✅ 5-Layer Conviction Gate — All Passed</div>
      <table style="width:100%;border-collapse:collapse;background:#f0fdf4;border-radius:8px;overflow:hidden;border:1px solid #bbf7d0">
        {layer_rows}
      </table>
    </div>"""
            conviction_text = "\n✅ 5-Layer Conviction Gate — All Passed:\n" + "\n".join(f"  ✓ {l}" for l in conviction_layers) + "\n"

    # ── Game plan HTML (only for BUY transitions) ─────────────────────────
    game_plan_html = ""
    game_plan_text = ""
    if game_plan and new_signal == "BUY":
        cp = game_plan.get("current_price", 0)
        e1, e2, bo = game_plan["entry1"], game_plan["entry2"], game_plan["breakout"]
        sl, tp = game_plan["stop"], game_plan["take_profit"]
        cats = game_plan.get("catalysts", [])
        risk = game_plan.get("risk", "")
        gp_style = game_plan.get("style", horizon or "SWING")
        horizon_note = game_plan.get("horizon_note", "")
        _style_labels = {"SHORT": "Short-Term (1–5 Days)", "SWING": "Swing (5–30 Days)", "LONG": "Position (1–12 Months)"}
        plan_label = _style_labels.get(gp_style, gp_style)

        def _pct(target: float) -> str:
            if cp <= 0: return ""
            p = (target - cp) / cp * 100
            return f" ({p:+.1f}%)"

        cat_rows = "".join(
            f'<tr><td style="padding:5px 10px;font-size:12px;color:#1e293b;border-bottom:1px solid #f1f5f9">› {c}</td></tr>'
            for c in cats
        )
        horizon_note_html = (
            f'<div style="font-size:11px;color:#64748b;font-style:italic;margin-bottom:10px">{horizon_note}</div>'
            if horizon_note else ""
        )
        game_plan_html = f"""
    <div style="margin-top:24px">
      <div style="font-size:11px;font-weight:700;color:#16a34a;text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px">📋 Game Plan — {plan_label} — {symbol}</div>
      {horizon_note_html}

      <!-- Entry levels -->
      <table style="width:100%;border-collapse:collapse;background:#f0fdf4;border-radius:8px;overflow:hidden;border:1px solid #bbf7d0;margin-bottom:10px">
        <tr style="background:#dcfce7">
          <td colspan="3" style="padding:6px 10px;font-size:11px;font-weight:700;color:#15803d;text-transform:uppercase;letter-spacing:.05em">Entry Strategy</td>
        </tr>
        <tr>
          <td style="padding:6px 10px;font-size:12px;color:#166534;font-weight:600">Limit buy — 50%</td>
          <td style="padding:6px 10px;font-size:13px;font-weight:800;color:#16a34a;font-family:monospace">${e1:.2f}{_pct(e1)}</td>
          <td style="padding:6px 10px;font-size:11px;color:#64748b">{game_plan["entry1_note"]}</td>
        </tr>
        <tr style="background:#f8fffe">
          <td style="padding:6px 10px;font-size:12px;color:#166534;font-weight:600">Limit buy — 50%</td>
          <td style="padding:6px 10px;font-size:13px;font-weight:800;color:#16a34a;font-family:monospace">${e2:.2f}{_pct(e2)}</td>
          <td style="padding:6px 10px;font-size:11px;color:#64748b">{game_plan["entry2_note"]}</td>
        </tr>
        <tr>
          <td style="padding:6px 10px;font-size:12px;color:#92400e;font-weight:600">Breakout — 50%</td>
          <td style="padding:6px 10px;font-size:13px;font-weight:800;color:#d97706;font-family:monospace">${bo:.2f}{_pct(bo)}</td>
          <td style="padding:6px 10px;font-size:11px;color:#64748b">{game_plan["breakout_note"]}</td>
        </tr>
      </table>

      <!-- Stop & Target -->
      <table style="width:100%;border-collapse:collapse;margin-bottom:10px">
        <tr>
          <td style="width:50%;padding-right:5px">
            <div style="background:#fef2f2;border:1px solid #fca5a5;border-radius:8px;padding:10px 12px">
              <div style="font-size:10px;font-weight:700;color:#dc2626;text-transform:uppercase;letter-spacing:.05em">Stop Loss</div>
              <div style="font-size:16px;font-weight:800;color:#ef4444;font-family:monospace;margin:3px 0">${sl:.2f}{_pct(sl)}</div>
              <div style="font-size:10px;color:#64748b">{game_plan["stop_note"]}</div>
            </div>
          </td>
          <td style="width:50%;padding-left:5px">
            <div style="background:#f5f3ff;border:1px solid #c4b5fd;border-radius:8px;padding:10px 12px">
              <div style="font-size:10px;font-weight:700;color:#7c3aed;text-transform:uppercase;letter-spacing:.05em">Take Profit</div>
              <div style="font-size:16px;font-weight:800;color:#6366f1;font-family:monospace;margin:3px 0">${tp:.2f}{_pct(tp)}</div>
              <div style="font-size:10px;color:#64748b">{game_plan["take_profit_note"]}</div>
            </div>
          </td>
        </tr>
      </table>

      <!-- Catalysts -->
      <div style="font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">Catalysts in the Window</div>
      <table style="width:100%;border-collapse:collapse;background:#fafafa;border-radius:8px;overflow:hidden;border:1px solid #e2e8f0;margin-bottom:10px">
        {cat_rows}
      </table>

      <!-- Risk -->
      <div style="background:#fffbeb;border:1px solid #fbbf24;border-radius:8px;padding:10px 14px;font-size:12px;color:#92400e">
        <strong>⚠ Key Risk:</strong> {risk}
      </div>
    </div>"""

        game_plan_text = f"""
--- Game Plan ({plan_label}) for {symbol} ---
{horizon_note}
Entry 1 (50%): ${e1:.2f}{_pct(e1)} — {game_plan["entry1_note"]}
Entry 2 (50%): ${e2:.2f}{_pct(e2)} — {game_plan["entry2_note"]}
Breakout (50%): ${bo:.2f}{_pct(bo)} — {game_plan["breakout_note"]}
Stop Loss:  ${sl:.2f}{_pct(sl)} — {game_plan["stop_note"]}
Take Profit: ${tp:.2f}{_pct(tp)} — {game_plan["take_profit_note"]}
Catalysts:
{chr(10).join(f"  › {c}" for c in cats)}
Key Risk: {risk}
"""

    is_exit_alert = mood == "bearish"
    if new_signal == "SELL":
        subject_prefix = "⚠ SELL Alert"
    elif is_exit_alert:
        subject_prefix = "⚠ Signal Weakening"
    else:
        subject_prefix = "Signal Alert"
    horizon_tag = f" [{horizon}]" if horizon else ""
    subject = f"{subject_prefix}: {symbol} {prev_signal} → {new_signal}{horizon_tag} (Analyst: {analyst.upper()})"
    cta = (
        "AI signal has reversed — consider reviewing your position.\n"
        if is_exit_alert else
        "Both indicators are now aligned — review the stock detail before acting.\n"
    )
    body_text = (
        f"Your signal alert for {symbol} has fired.\n\n"
        f"AI Signal: {prev_signal} → {new_signal}{horizon_tag} ({desc})\n"
        f"Analyst consensus: {analyst.upper()}\n"
        + (f"Bullish probability: {float(bullish_prob)*100:.1f}%  |  Confidence: {float(confidence):.1f}%\n" if bullish_prob is not None else "")
        + f"\nWhy the signal changed:\n{rows_text}\n\n"
        + conviction_text
        + (f"{earnings_warn}\n\n" if earnings_warn else "")
        + game_plan_text
        + cta + "\n"
        f"Not personalised financial advice. Always do your own research.\n"
    )
    header_icon = "&#128202;" if not is_exit_alert else "&#9888;"
    if new_signal == "SELL":
        header_label = "StockAI SELL Alert"
    elif is_exit_alert:
        header_label = "StockAI Signal Weakening"
    else:
        header_label = "StockAI Signal Alert"

    # Horizon / trading style badge
    _horizon_colors = {"SHORT": "#ef4444", "SWING": "#6366f1", "LONG": "#22c55e"}
    _horizon_labels = {"SHORT": "Short-term (1–5d)", "SWING": "Swing (5–20d)", "LONG": "Position (30–90d)"}
    horizon_badge_html = ""
    if horizon:
        hc = _horizon_colors.get(horizon, "#6366f1")
        hl = _horizon_labels.get(horizon, horizon)
        horizon_badge_html = (
            f'<div style="margin-top:12px;display:inline-block;padding:4px 10px;'
            f'border-radius:6px;border:1px solid {hc}60;background:{hc}18;'
            f'font-size:11px;font-weight:700;color:{hc};letter-spacing:.06em">'
            f'&#128260; {horizon} &nbsp;·&nbsp; {hl}</div>'
        )
    cta_html = (
        f'<div style="background:#fef2f2;border:1px solid #fca5a5;border-radius:8px;padding:10px 14px;margin-top:16px;font-size:13px;color:#991b1b">'
        f'&#9888; {cta.strip()}</div>'
        if is_exit_alert else ""
    )
    body_html = f"""
<html><body style="font-family:sans-serif;color:#1e293b;background:#f8fafc;padding:24px">
  <div style="max-width:520px;margin:auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08)">
    <h2 style="margin-top:0;color:{'#ef4444' if is_exit_alert else '#6366f1'}">{header_icon} {header_label}</h2>
    <p style="font-size:16px"><strong>{symbol}</strong> AI Signal has changed:</p>
    {horizon_badge_html}

    <div style="background:#f1f5f9;border-radius:8px;padding:16px;margin:16px 0;display:flex;align-items:center;gap:24px">
      <div style="text-align:center">
        <div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:.06em">From</div>
        <div style="font-size:22px;font-weight:800;color:{prev_color}">{prev_signal}</div>
      </div>
      <div style="font-size:24px;color:#94a3b8">&#8594;</div>
      <div style="text-align:center">
        <div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:.06em">To</div>
        <div style="font-size:22px;font-weight:800;color:{new_color}">{new_signal}</div>
      </div>
      {f'<div style="margin-left:auto;text-align:right"><div style="font-size:11px;color:#94a3b8">Bullish prob</div><div style="font-size:20px;font-weight:800;color:{new_color}">{float(bullish_prob)*100:.0f}%</div><div style="font-size:10px;color:#94a3b8">Confidence {float(confidence):.0f}%</div></div>' if bullish_prob is not None else ""}
    </div>

    <p style="font-size:14px;color:#475569;margin:0 0 16px">
      Analyst consensus: <strong style="color:#6366f1">{analyst.upper()}</strong> &nbsp;·&nbsp; {desc.capitalize()}
    </p>

    <div style="font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">Why the signal changed</div>
    <table style="width:100%;border-collapse:collapse;background:#fafafa;border-radius:8px;overflow:hidden;border:1px solid #e2e8f0">
      {rows_html}
    </table>

    {cta_html}
    {f'<div style="background:#fef9c3;border:1px solid #fbbf24;border-radius:8px;padding:10px 14px;margin-top:16px;font-size:13px;color:#92400e">{earnings_warn}</div>' if earnings_warn else ''}
    {conviction_html}
    {game_plan_html}
    <p style="font-size:11px;color:#94a3b8;margin-top:24px;border-top:1px solid #e2e8f0;padding-top:16px">
      Not personalised financial advice. Always do your own research before acting.
    </p>
  </div>
</body></html>"""
    return send_email(to, subject, body_html, body_text)


def send_morning_digest_email(
    to: str,
    date_str: str,
    regime: dict,
    swing_opportunities: list,
    growth_opportunities: list,
    open_positions: list,
    pattern_alerts: list,
    market: str = "US",
) -> bool:
    """Send the daily pre-market digest email."""
    state = regime.get("state", "unknown")
    spy_price = regime.get("spy_price")
    vix = regime.get("vix")
    regime_notes = regime.get("notes", [])

    _state_color = {
        "bull":     "#22c55e",
        "neutral":  "#facc15",
        "choppy":   "#f97316",
        "risk_off": "#f97316",
        "bear":     "#ef4444",
    }
    _state_label = {
        "bull":     "BULL",
        "neutral":  "NEUTRAL",
        "choppy":   "CHOPPY",
        "risk_off": "RISK OFF",
        "bear":     "BEAR",
    }
    sc = _state_color.get(state, "#94a3b8")
    sl = _state_label.get(state, state.upper())

    # ── Market pulse section ──────────────────────────────────────────────────
    spy_str = f"${spy_price:,.2f}" if spy_price else "—"
    vix_str = f"{vix:.1f}" if vix else "—"
    regime_notes_html = "".join(
        f'<li style="font-size:12px;color:#64748b;margin:2px 0">{n}</li>'
        for n in (regime_notes or [])[:4]
    )

    # ── Opportunity table helper ──────────────────────────────────────────────
    def _opp_table(opportunities: list, label: str, accent: str) -> tuple[str, str]:
        rows_html = ""
        rows_text = ""
        for i, o in enumerate(opportunities[:5], 1):
            sig = o.get("signal") or "—"
            sig_color = {"BUY": "#22c55e", "HOLD": "#facc15", "WAIT": "#f97316", "SELL": "#ef4444"}.get(sig, "#94a3b8")
            ml = o.get("ml_prob")
            ml_str = f"{ml*100:.0f}%" if ml else "—"
            score_str = f"{o['score']:.0f}" if o.get("score") is not None else "—"
            price_str = f"${o['price']:,.2f}" if o.get("price") else "—"
            rows_html += (
                f'<tr style="border-bottom:1px solid #f1f5f9">'
                f'<td style="padding:7px 10px;font-weight:700;font-size:13px">{o["symbol"]}</td>'
                f'<td style="padding:7px 10px;font-size:12px;color:#64748b">{o.get("name","")[:22]}</td>'
                f'<td style="padding:7px 10px;font-size:13px;font-weight:700;color:{accent}">{score_str}</td>'
                f'<td style="padding:7px 10px"><span style="background:{sig_color}22;color:{sig_color};font-size:11px;font-weight:700;padding:2px 6px;border-radius:4px">{sig}</span></td>'
                f'<td style="padding:7px 10px;font-size:12px;color:#64748b">{ml_str}</td>'
                f'<td style="padding:7px 10px;font-size:12px;color:#94a3b8">{price_str}</td>'
                f'</tr>'
            )
            rows_text += f"  {i}. {o['symbol']:6} Score {score_str:4}  Signal {sig:4}  ML {ml_str:4}  {o.get('name','')[:20]}\n"

        if not rows_html:
            return "", ""
        section_html = f"""
    <div style="margin-top:24px">
      <div style="font-size:11px;font-weight:700;color:{accent};text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px">{label}</div>
      <table style="width:100%;border-collapse:collapse;background:#fafafa;border-radius:8px;overflow:hidden;border:1px solid #e2e8f0">
        <tr style="background:#f1f5f9">
          <th style="padding:6px 10px;font-size:11px;color:#475569;font-weight:600;text-align:left">Symbol</th>
          <th style="padding:6px 10px;font-size:11px;color:#475569;font-weight:600;text-align:left">Name</th>
          <th style="padding:6px 10px;font-size:11px;color:#475569;font-weight:600;text-align:left">Score</th>
          <th style="padding:6px 10px;font-size:11px;color:#475569;font-weight:600;text-align:left">Signal</th>
          <th style="padding:6px 10px;font-size:11px;color:#475569;font-weight:600;text-align:left">ML%</th>
          <th style="padding:6px 10px;font-size:11px;color:#475569;font-weight:600;text-align:left">Price</th>
        </tr>
        {rows_html}
      </table>
    </div>"""
        return section_html, f"\n{label}\n{rows_text}"

    # ── Top SWING + GROWTH sections ──────────────────────────────────────────
    market_label = market.upper()
    swing_html, swing_text = _opp_table(swing_opportunities, f"Top 5 SWING — {market_label}", "#6366f1")
    growth_html, growth_text = _opp_table(growth_opportunities, f"Top 5 GROWTH — {market_label}", "#f97316")
    opp_section_html = swing_html + growth_html
    opp_section_text = swing_text + growth_text

    # ── Open positions section ────────────────────────────────────────────────
    pos_rows_html = ""
    pos_rows_text = ""
    for p in open_positions:
        pnl = p.get("pnl_pct", 0.0) or 0.0
        pnl_color = "#22c55e" if pnl >= 0 else "#ef4444"
        pnl_str = f"{pnl:+.1f}%"
        stop_dist = p.get("stop_dist_pct")
        stop_str = f"{stop_dist:.1f}% below" if stop_dist is not None else "—"
        last_p = p.get("last_price")
        price_str = f"${last_p:,.2f}" if last_p else "—"
        entry_str = f"${p['entry_price']:,.2f}"
        pos_rows_html += (
            f'<tr style="border-bottom:1px solid #f1f5f9">'
            f'<td style="padding:7px 10px;font-weight:700;font-size:13px">{p["symbol"]}</td>'
            f'<td style="padding:7px 10px;font-size:12px;color:#64748b">{entry_str} → {price_str}</td>'
            f'<td style="padding:7px 10px;font-size:13px;font-weight:700;color:{pnl_color}">{pnl_str}</td>'
            f'<td style="padding:7px 10px;font-size:12px;color:#ef4444">{stop_str}</td>'
            f'<td style="padding:7px 10px;font-size:12px;color:#94a3b8">{p.get("hold_days",0)}d</td>'
            f'</tr>'
        )
        pos_rows_text += f"  {p['symbol']:6} {entry_str} → {price_str}  P&L {pnl_str}  Stop {stop_str}  {p.get('hold_days',0)}d\n"

    pos_section_html = ""
    if pos_rows_html:
        pos_section_html = f"""
    <div style="margin-top:24px">
      <div style="font-size:11px;font-weight:700;color:#f97316;text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px">Open Positions ({len(open_positions)})</div>
      <table style="width:100%;border-collapse:collapse;background:#fafafa;border-radius:8px;overflow:hidden;border:1px solid #e2e8f0">
        <tr style="background:#f1f5f9">
          <th style="padding:6px 10px;font-size:11px;color:#475569;font-weight:600;text-align:left">Symbol</th>
          <th style="padding:6px 10px;font-size:11px;color:#475569;font-weight:600;text-align:left">Entry → Close</th>
          <th style="padding:6px 10px;font-size:11px;color:#475569;font-weight:600;text-align:left">P&L</th>
          <th style="padding:6px 10px;font-size:11px;color:#475569;font-weight:600;text-align:left">Stop Distance</th>
          <th style="padding:6px 10px;font-size:11px;color:#475569;font-weight:600;text-align:left">Held</th>
        </tr>
        {pos_rows_html}
      </table>
    </div>"""
    pos_section_text = f"\nOPEN POSITIONS\n{pos_rows_text}" if pos_rows_text else ""

    # ── Pattern alerts section ────────────────────────────────────────────────
    _pattern_label = {
        "golden_cross":        "Golden Cross",
        "macd_bullish_cross":  "MACD Bullish Cross",
        "rsi_oversold_bounce": "RSI Oversold Bounce",
        "double_bottom":       "Double Bottom (W-pattern)",
        "breakout":            "Volume Breakout",
    }
    pat_rows_html = "".join(
        f'<tr style="border-bottom:1px solid #f1f5f9">'
        f'<td style="padding:7px 10px;font-weight:700;font-size:13px">{p["symbol"]}</td>'
        f'<td style="padding:7px 10px;font-size:12px;color:#22c55e">{_pattern_label.get(p["condition"], p["condition"])}</td>'
        f'</tr>'
        for p in pattern_alerts
    )
    pat_section_html = ""
    if pat_rows_html:
        pat_section_html = f"""
    <div style="margin-top:24px">
      <div style="font-size:11px;font-weight:700;color:#22c55e;text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px">Pattern Alerts Fired Yesterday</div>
      <table style="width:100%;border-collapse:collapse;background:#fafafa;border-radius:8px;overflow:hidden;border:1px solid #e2e8f0">
        {pat_rows_html}
      </table>
    </div>"""

    _market_name = {"US": "US Markets (NYSE/NASDAQ)", "HK": "HK Market (HKEX)"}.get(market.upper(), market.upper())
    subject = f"📊 Morning Digest [{market.upper()}]: StockAI — {date_str} | Regime: {sl}"
    body_text = (
        f"StockAI Morning Digest [{market.upper()}] — {date_str}\n"
        f"Market Regime: {sl}  |  SPY: {spy_str}  |  VIX: {vix_str}\n"
        + ("\n".join(regime_notes or []))
        + opp_section_text
        + pos_section_text
        + "\nNot financial advice. Paper trading simulation only.\n"
    )
    body_html = f"""<html><body style="font-family:sans-serif;color:#1e293b;background:#f8fafc;padding:24px;margin:0">
  <div style="max-width:560px;margin:auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08)">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px">
      <h2 style="margin:0;font-size:18px;color:#0f172a">📊 Morning Digest — {_market_name}</h2>
      <span style="font-size:13px;color:#94a3b8">{date_str}</span>
    </div>

    <!-- Market Regime -->
    <div style="margin-top:16px;background:#f8fafc;border-radius:10px;padding:16px;border:1px solid #e2e8f0">
      <div style="display:flex;align-items:center;gap:14px">
        <div>
          <div style="font-size:10px;color:#94a3b8;text-transform:uppercase;letter-spacing:.07em">Market Regime</div>
          <div style="font-size:22px;font-weight:800;color:{sc}">{sl}</div>
        </div>
        <div style="border-left:1px solid #e2e8f0;padding-left:14px">
          <div style="font-size:11px;color:#64748b">SPY <strong style="color:#1e293b">{spy_str}</strong></div>
          <div style="font-size:11px;color:#64748b;margin-top:3px">VIX <strong style="color:#1e293b">{vix_str}</strong></div>
        </div>
        {f'<div style="flex:1"><ul style="margin:0;padding-left:16px">{regime_notes_html}</ul></div>' if regime_notes_html else ''}
      </div>
    </div>

    {opp_section_html}
    {pos_section_html}
    {pat_section_html}

    <p style="font-size:11px;color:#94a3b8;margin-top:28px;border-top:1px solid #e2e8f0;padding-top:14px">
      Not financial advice. Paper trading simulation only. StockAI · {date_str}
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


def send_trade_exit_email(
    to: str,
    symbol: str,
    exit_reason: str,
    entry_price: float,
    exit_price: float,
    pnl_dollar: float,
    pnl_pct: float,
    hold_days: int,
    shares: float,
    style: str = "GROWTH",
    signal_at_exit: str | None = None,
    highest_price: float | None = None,
    entry_notes: list | None = None,
) -> bool:
    """Send a paper trade exit email — fired whenever the paper trading engine closes a position."""
    _EXIT_LABEL = {
        "signal_exit":       ("🔴 SELL Signal Exit",    "#ef4444", "The signal engine issued a SELL — position closed."),
        "stop_hit":          ("🛑 Stop Loss Triggered",  "#ef4444", "Price hit the trailing stop — capital protected."),
        "target_reached":    ("🎯 Take-Profit Reached",  "#22c55e", "Target price hit — profit locked in."),
        "hold_stall_timeout":("⏳ HOLD Stall Exit",      "#f97316", "Position stalled for 30+ days under 5% gain — freeing capital."),
        "time_stop":         ("⌛ Time Stop",            "#f97316", "Maximum hold period reached."),
        "momentum_exit":     ("📉 Momentum Lost",        "#f97316", "WAIT signal persisted too long — momentum faded."),
    }
    label, accent, reason_note = _EXIT_LABEL.get(exit_reason, ("📋 Position Closed", "#6366f1", "Position closed by paper trading engine."))

    is_win = pnl_dollar >= 0
    pnl_color  = "#22c55e" if is_win else "#ef4444"
    pnl_sign   = "+" if is_win else ""
    pnl_pct_f  = f"{pnl_sign}{pnl_pct:.2f}%"
    pnl_dollar_f = f"{pnl_sign}${abs(pnl_dollar):.2f}"

    mfe_row = ""
    if highest_price and highest_price > entry_price:
        mfe_pct = (highest_price - entry_price) / entry_price * 100
        mfe_row = f"""
      <tr><td style="color:#64748b">Max Favourable Excursion</td>
          <td style="text-align:right;color:#22c55e">${highest_price:.2f} (+{mfe_pct:.1f}%)</td></tr>"""

    notes_html = ""
    if entry_notes:
        bullets = "".join(f'<li style="margin:2px 0;color:#64748b">{n}</li>' for n in entry_notes[:4])
        notes_html = f'<div style="margin-top:16px"><p style="font-weight:600;margin:0 0 6px">Entry rationale</p><ul style="margin:0;padding-left:20px;font-size:13px">{bullets}</ul></div>'

    subject = f"[Paper Trade] {label} — {symbol} ({pnl_pct_f})"
    body_text = (
        f"{label}: {symbol}\n"
        f"P&L: {pnl_dollar_f} ({pnl_pct_f}) over {hold_days} day(s)\n"
        f"Entry: ${entry_price:.4f}  Exit: ${exit_price:.4f}\n"
        f"Signal at exit: {signal_at_exit or '—'}\n"
        f"Reason: {reason_note}"
    )
    body_html = f"""<!DOCTYPE html><html><body style="font-family:sans-serif;background:#f8fafc;padding:24px;margin:0">
  <div style="max-width:520px;margin:auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08)">
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:20px">
      <div style="background:{accent};border-radius:8px;padding:8px 14px;color:#fff;font-weight:700;font-size:18px">{symbol}</div>
      <div style="font-size:20px;font-weight:700;color:{accent}">{label}</div>
    </div>
    <div style="background:#f1f5f9;border-radius:10px;padding:20px;margin-bottom:20px;text-align:center">
      <div style="font-size:36px;font-weight:800;color:{pnl_color}">{pnl_dollar_f}</div>
      <div style="font-size:20px;color:{pnl_color};margin-top:4px">{pnl_pct_f}</div>
      <div style="font-size:13px;color:#94a3b8;margin-top:6px">{"PROFIT" if is_win else "LOSS"} over {hold_days} trading day(s)</div>
    </div>
    <table style="width:100%;border-collapse:collapse;font-size:14px">
      <tr><td style="color:#64748b;padding:6px 0">Entry Price</td>
          <td style="text-align:right;font-weight:600">${entry_price:.4f}</td></tr>
      <tr><td style="color:#64748b;padding:6px 0">Exit Price</td>
          <td style="text-align:right;font-weight:600">${exit_price:.4f}</td></tr>
      <tr><td style="color:#64748b;padding:6px 0">Shares</td>
          <td style="text-align:right">{shares:.2f}</td></tr>{mfe_row}
      <tr><td style="color:#64748b;padding:6px 0">Exit Reason</td>
          <td style="text-align:right;color:{accent};font-weight:600">{exit_reason.replace('_', ' ').title()}</td></tr>
      <tr><td style="color:#64748b;padding:6px 0">Signal at Exit</td>
          <td style="text-align:right">{signal_at_exit or '—'}</td></tr>
      <tr><td style="color:#64748b;padding:6px 0">Style</td>
          <td style="text-align:right">{style}</td></tr>
    </table>
    <div style="margin-top:16px;padding:12px;background:#fef2f2 if not is_win else #f0fdf4;border-radius:8px;font-size:13px;color:#64748b">
      {reason_note}
    </div>
    {notes_html}
    <p style="font-size:12px;color:#94a3b8;margin-top:24px;border-top:1px solid #e2e8f0;padding-top:12px">
      This is a paper trade simulation — no real money involved. StockAI Paper Trading Engine.
    </p>
  </div>
</body></html>"""
    return send_email(to, subject, body_html, body_text)


def send_paper_portfolio_digest_email(
    to: str,
    portfolio_name: str,
    total_return_pct: float,
    total_pnl: float,
    open_count: int,
    today_closed: list,  # list of {symbol, pnl, pnl_pct, exit_reason}
    top_positions: list,  # list of {symbol, unrealized_pct, style}
    sharpe: float | None,
) -> bool:
    """Daily after-market portfolio digest email."""
    from datetime import date as _date
    date_str = _date.today().strftime("%b %d, %Y")

    ret_color = "#22c55e" if total_return_pct >= 0 else "#ef4444"
    ret_sign = "+" if total_return_pct >= 0 else ""
    pnl_sign = "+" if total_pnl >= 0 else ""

    # ── Closed trades today ───────────────────────────────────────────────────
    closed_rows_html = ""
    closed_lines_text = ""
    for t in today_closed[:8]:
        sym = t.get("symbol", "")
        pnl = t.get("pnl", 0.0)
        pnl_pct = t.get("pnl_pct", 0.0)
        reason = (t.get("exit_reason") or "").replace("_", " ").title()
        c = "#22c55e" if pnl >= 0 else "#ef4444"
        s = "+" if pnl >= 0 else ""
        closed_rows_html += (
            f'<tr style="border-bottom:1px solid #f1f5f9">'
            f'<td style="padding:7px 10px;font-weight:700;font-size:13px">{sym}</td>'
            f'<td style="padding:7px 10px;font-size:13px;font-weight:700;color:{c}">{s}${pnl:,.2f} ({s}{pnl_pct:.1f}%)</td>'
            f'<td style="padding:7px 10px;font-size:12px;color:#64748b">{reason}</td>'
            f'</tr>'
        )
        closed_lines_text += f"  {sym:6}  {s}${pnl:,.2f} ({s}{pnl_pct:.1f}%)  {reason}\n"

    closed_section_html = ""
    if closed_rows_html:
        closed_section_html = f"""
        <h3 style="font-size:14px;font-weight:700;color:#374151;margin:24px 0 10px">Closed Today</h3>
        <table style="width:100%;border-collapse:collapse;font-size:13px">
          <tr style="background:#f8fafc"><th style="padding:7px 10px;text-align:left;font-size:11px;color:#64748b">Symbol</th>
          <th style="padding:7px 10px;text-align:left;font-size:11px;color:#64748b">P&amp;L</th>
          <th style="padding:7px 10px;text-align:left;font-size:11px;color:#64748b">Reason</th></tr>
          {closed_rows_html}
        </table>"""
        closed_section_text = f"\nCLOSED TODAY:\n{closed_lines_text}"
    else:
        closed_section_text = "\nNo trades closed today.\n"

    # ── Open positions ────────────────────────────────────────────────────────
    pos_rows_html = ""
    pos_lines_text = ""
    for p in top_positions[:6]:
        sym = p.get("symbol", "")
        pct = p.get("unrealized_pct", 0.0)
        style = p.get("style", "")
        c = "#22c55e" if pct >= 0 else "#ef4444"
        s = "+" if pct >= 0 else ""
        pos_rows_html += (
            f'<tr style="border-bottom:1px solid #f1f5f9">'
            f'<td style="padding:7px 10px;font-weight:700;font-size:13px">{sym}</td>'
            f'<td style="padding:7px 10px;font-size:13px;font-weight:700;color:{c}">{s}{pct:.1f}%</td>'
            f'<td style="padding:7px 10px;font-size:12px;color:#94a3b8">{style}</td>'
            f'</tr>'
        )
        pos_lines_text += f"  {sym:6}  {s}{pct:.1f}%  {style}\n"

    pos_section_html = ""
    if pos_rows_html:
        pos_section_html = f"""
        <h3 style="font-size:14px;font-weight:700;color:#374151;margin:24px 0 10px">Open Positions ({open_count})</h3>
        <table style="width:100%;border-collapse:collapse;font-size:13px">
          <tr style="background:#f8fafc"><th style="padding:7px 10px;text-align:left;font-size:11px;color:#64748b">Symbol</th>
          <th style="padding:7px 10px;text-align:left;font-size:11px;color:#64748b">Unrealized</th>
          <th style="padding:7px 10px;text-align:left;font-size:11px;color:#64748b">Style</th></tr>
          {pos_rows_html}
        </table>"""

    sharpe_str = f"{sharpe:.2f}" if sharpe is not None else "—"

    subject = f"[Paper Portfolio] {portfolio_name} — {date_str} · {ret_sign}{total_return_pct:.1f}%"
    body_text = (
        f"Paper Portfolio Digest — {portfolio_name} — {date_str}\n"
        f"Total Return: {ret_sign}{total_return_pct:.1f}%  Total P&L: {pnl_sign}${total_pnl:,.2f}\n"
        f"Open Positions: {open_count}  Sharpe: {sharpe_str}\n"
        f"{closed_section_text}"
        f"\nOPEN POSITIONS:\n{pos_lines_text}"
    )
    body_html = f"""<!DOCTYPE html><html><body style="font-family:sans-serif;background:#f8fafc;padding:24px;margin:0">
  <div style="max-width:540px;margin:auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08)">
    <div style="margin-bottom:20px">
      <div style="font-size:12px;color:#94a3b8;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px">Paper Portfolio Digest · {date_str}</div>
      <div style="font-size:20px;font-weight:700;color:#111827">{portfolio_name}</div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:20px">
      <div style="background:#f8fafc;border-radius:8px;padding:14px;text-align:center">
        <div style="font-size:22px;font-weight:800;color:{ret_color}">{ret_sign}{total_return_pct:.1f}%</div>
        <div style="font-size:11px;color:#94a3b8;margin-top:2px">Total Return</div>
      </div>
      <div style="background:#f8fafc;border-radius:8px;padding:14px;text-align:center">
        <div style="font-size:18px;font-weight:700;color:{ret_color}">{pnl_sign}${total_pnl:,.0f}</div>
        <div style="font-size:11px;color:#94a3b8;margin-top:2px">Total P&amp;L</div>
      </div>
      <div style="background:#f8fafc;border-radius:8px;padding:14px;text-align:center">
        <div style="font-size:18px;font-weight:700;color:#374151">{sharpe_str}</div>
        <div style="font-size:11px;color:#94a3b8;margin-top:2px">Sharpe</div>
      </div>
    </div>
    {closed_section_html}
    {pos_section_html}
    <p style="font-size:12px;color:#94a3b8;margin-top:24px;border-top:1px solid #e2e8f0;padding-top:12px">
      Paper trade simulation — no real money. <a href="https://lausing.com/paper-portfolio" style="color:#6366f1">View portfolio →</a>
    </p>
  </div>
</body></html>"""
    return send_email(to, subject, body_html, body_text)
