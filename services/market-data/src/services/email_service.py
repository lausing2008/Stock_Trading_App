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
    win_rate_90d: tuple[float, int] | None = None,
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
    mood, desc = direction_map.get((prev_signal, new_signal), ("neutral", "unchanged"))
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
    def _ml_auc_note(auc) -> str:
        if auc is None:
            return "—"
        q = "strong" if auc >= 0.70 else "good" if auc >= 0.60 else "fair" if auc >= 0.55 else "weak"
        return f"{float(auc):.3f} ({q})"

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

    # T174: catalyst intelligence scores from event-intelligence service (stored in signal reasons)
    _cat_score    = reasons.get("catalyst_score")
    _ins_score    = reasons.get("insider_score")
    _cong_score   = reasons.get("congress_score")
    _cat_prob_adj = reasons.get("catalyst_prob_adj")
    def _catalyst_note(score, adj=None, is_insider=False) -> str:
        if score is None:
            return "—"
        if is_insider:
            label = "Strong buying" if score >= 60 else "Moderate buying" if score >= 30 else "Mild buying" if score >= 0 else "Mild selling" if score >= -30 else "Significant selling"
        else:
            label = "Strong" if score >= 60 else "Moderate" if score >= 30 else "Weak" if score >= 0 else "Selling pressure"
        s = f"{float(score):.0f} ({label})"
        if adj:
            s += f"  → fused_prob adj {'+' if adj > 0 else ''}{float(adj)*100:.1f}%"
        return s

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
        ("ML model AUC",          _ml_auc_note(reasons.get("ml_test_auc"))),
        ("Next earnings",         earnings_note),
        ("Insider activity (6M)", insider_note),
        ("Catalyst score (EDGAR)", _catalyst_note(_cat_score, _cat_prob_adj)),
        ("Insider score (EDGAR)",  _catalyst_note(_ins_score, _cat_prob_adj, is_insider=True)),
        ("Congress score",         _catalyst_note(_cong_score)),
        ("90d signal accuracy",   f"{round(win_rate_90d[0]*100)}%WR ({win_rate_90d[1]} outcomes)" if win_rate_90d else "—"),
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

    # ── Active signal suppression conditions ──────────────────────────────
    _suppression_items = []
    if reasons.get("weekly_gate_fired"):
        _bars = reasons.get("weekly_gate_bars", "?")
        _mult = reasons.get("weekly_gate_mult")
        _mult_str = f" ({int(_mult*100)}× compress)" if _mult else ""
        _suppression_items.append(f"Weekly RSI bearish gate — {_bars} consecutive weeks below 38{_mult_str}")
    if reasons.get("weekly_overbought_gate"):
        _suppression_items.append("Weekly RSI overbought gate — weekly RSI > 75 (×0.85 compress)")
    if reasons.get("ml_oos_suppressed"):
        _suppression_items.append("ML out-of-sample suppression active — model OOS accuracy below threshold")
    _pillar = reasons.get("pillar_gate", "")
    if "compressed" in str(_pillar):
        _suppression_items.append(f"Pillar gate: {_pillar} — fewer than required TA dimensions agree")
    if reasons.get("compression_cap_applied"):
        _suppression_items.append("Compression cap applied — multiple filters stacked beyond 70% limit")
    _dte = reasons.get("days_to_earnings")
    if _dte is not None and isinstance(_dte, (int, float)) and 0 < _dte <= 10:
        _suppression_items.append(f"Earnings compression ({int(_dte)}d to earnings) — signal capped pre-announcement")
    if reasons.get("is_stale"):
        _suppression_items.append("Stale price data — bars are > 3 calendar days old, confidence reduced")

    suppression_html = ""
    suppression_text = ""
    if _suppression_items:
        _supp_rows = "".join(
            f'<tr><td style="padding:5px 12px;font-size:13px;color:#7c2d12;border-bottom:1px solid #fed7aa">'
            f'<span style="color:#ea580c;font-weight:700;margin-right:8px">⚠</span>{item}</td></tr>'
            for item in _suppression_items
        )
        suppression_html = f"""
    <div style="margin-top:16px">
      <div style="font-size:11px;font-weight:700;color:#ea580c;text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">Active Signal Suppressions</div>
      <table style="width:100%;border-collapse:collapse;background:#fff7ed;border-radius:8px;overflow:hidden;border:1px solid #fed7aa">
        {_supp_rows}
      </table>
    </div>"""
        suppression_text = "\nActive suppressions:\n" + "\n".join(f"  ⚠ {s}" for s in _suppression_items) + "\n"

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
    _conf_tag = f" · {float(confidence):.0f}% conf" if confidence is not None else ""
    _bp_tag = f" · {float(bullish_prob)*100:.0f}%BP" if bullish_prob is not None else ""
    subject = f"{subject_prefix}: {symbol} {prev_signal} → {new_signal}{horizon_tag}{_conf_tag}{_bp_tag}"
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
        + suppression_text
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
    {suppression_html}
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
    open_positions: list,
    pattern_alerts: list,
    market_sections: list | None = None,
    swing_opportunities: list | None = None,
    growth_opportunities: list | None = None,
    market: str = "US",
    signal_performance: dict | None = None,
) -> bool:
    """Send the combined daily pre-market digest email (all markets in one email)."""
    # Normalise: if caller passes market_sections list, use it; otherwise wrap legacy args
    if market_sections is None:
        market_sections = [{"market": market, "swing": swing_opportunities or [], "growth": growth_opportunities or []}]
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
    # HK has no VIX equivalent (US-only index) — vix is always None for the HK regime.
    # Detect by market_sections rather than a hardcoded SPY/VIX template so the HK digest
    # doesn't show a meaningless "VIX —" line.
    _is_hk_digest = bool(market_sections) and all(s.get("market") == "HK" for s in market_sections)
    _idx_label = "HSI" if _is_hk_digest else "SPY"
    _price_fmt = lambda p: f"${p:,.2f}"
    spy_str = _price_fmt(spy_price) if spy_price else "—"
    vix_str = f"{vix:.1f}" if vix else "—"
    ret20 = regime.get("spy_20d_ret")
    ret20_str = (f"+{ret20:.1f}%" if ret20 and ret20 > 0 else f"{ret20:.1f}%" if ret20 is not None else None)
    ret20_color = "#22c55e" if ret20 and ret20 > 0 else "#ef4444" if ret20 is not None and ret20 < 0 else "#94a3b8"
    vix_trend = regime.get("vix_5d_trend")
    breadth_weak = regime.get("breadth_weak", False)
    regime_notes_html = "".join(
        f'<li style="font-size:12px;color:#64748b;margin:2px 0">{n}</li>'
        for n in (regime_notes or [])[:4]
    )

    # ── Symbol 90d win-rate lookup (from signal_performance.by_symbol) ─────────
    _sym_wr: dict[str, tuple[float, int]] = {}  # symbol → (win_rate_pct, count)
    for _s in (signal_performance or {}).get("by_symbol", []):
        if (_s.get("count") or 0) >= 3:
            _sym_wr[_s["symbol"]] = (round((_s.get("win_rate") or 0) * 100), _s["count"])

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
            conf = o.get("confidence")
            conf_str = f"{conf:.0f}%" if conf is not None else "—"
            dte = o.get("days_to_earnings")
            earnings_badge = ""
            earnings_text = ""
            if dte is not None and 0 <= dte <= 5:
                earnings_badge = f' <span style="background:#fef3c733;color:#f59e0b;font-size:10px;font-weight:700;padding:1px 5px;border-radius:3px;border:1px solid #fde68a">⚠️ Earn {dte}d</span>'
                earnings_text = f" ⚠️Earn {dte}d"
            wr_badge = ""
            wr_text = ""
            if o["symbol"] in _sym_wr:
                wr_pct, wr_n = _sym_wr[o["symbol"]]
                wr_color = "#22c55e" if wr_pct >= 55 else "#f59e0b" if wr_pct >= 45 else "#ef4444"
                wr_badge = f' <span style="color:{wr_color};font-size:10px;font-weight:700" title="{wr_n} outcomes 90d">{wr_pct}%WR</span>'
                wr_text = f" {wr_pct}%WR"
            bullets = o.get("reasons_bullets") or []
            bullets_html = ""
            if bullets:
                dots = " · ".join(bullets)
                bullets_html = f'<div style="font-size:10px;color:#64748b;margin-top:2px;font-style:italic">{dots}</div>'
            rows_html += (
                f'<tr style="border-bottom:1px solid #f1f5f9">'
                f'<td style="padding:7px 10px">'
                f'<div style="font-weight:700;font-size:13px">{o["symbol"]}{earnings_badge}{wr_badge}</div>'
                f'{bullets_html}'
                f'</td>'
                f'<td style="padding:7px 10px;font-size:12px;color:#64748b">{o.get("name","")[:22]}</td>'
                f'<td style="padding:7px 10px;font-size:13px;font-weight:700;color:{accent}">{score_str}</td>'
                f'<td style="padding:7px 10px"><span style="background:{sig_color}22;color:{sig_color};font-size:11px;font-weight:700;padding:2px 6px;border-radius:4px">{sig}</span></td>'
                f'<td style="padding:7px 10px;font-size:12px;color:#64748b">{conf_str}</td>'
                f'<td style="padding:7px 10px;font-size:12px;color:#94a3b8">{price_str}</td>'
                f'</tr>'
            )
            bullet_text = f"     → {' · '.join(bullets)}\n" if bullets else ""
            rows_text += f"  {i}. {o['symbol']:6}{earnings_text}{wr_text} Score {score_str:4}  Signal {sig:4}  Conf {conf_str:5}  {o.get('name','')[:20]}\n{bullet_text}"

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
          <th style="padding:6px 10px;font-size:11px;color:#475569;font-weight:600;text-align:left">Conf%</th>
          <th style="padding:6px 10px;font-size:11px;color:#475569;font-weight:600;text-align:left">Price</th>
        </tr>
        {rows_html}
      </table>
    </div>"""
        return section_html, f"\n{label}\n{rows_text}"

    # ── Top SWING + GROWTH sections — one block per market ───────────────────
    _mkt_name = {"HK": "HK Market (HKEX)", "US": "US Markets (NYSE/NASDAQ)"}
    opp_section_html = ""
    opp_section_text = ""
    for _sec in market_sections:
        _mkt = _sec.get("market", "US").upper()
        _mlabel = _mkt_name.get(_mkt, _mkt)
        _mkt_hdr_html = (
            f'<div style="margin-top:28px;padding:6px 0 4px;border-top:2px solid #e2e8f0">'
            f'<span style="font-size:13px;font-weight:800;color:#1e293b">{_mlabel}</span>'
            f'</div>'
        )
        _mkt_hdr_text = f"\n{'='*40}\n{_mlabel}\n{'='*40}\n"
        sh, st = _opp_table(_sec.get("swing") or [], f"Top 5 SWING — {_mkt}", "#6366f1")
        gh, gt = _opp_table(_sec.get("growth") or [], f"Top 5 GROWTH — {_mkt}", "#f97316")
        if sh or gh:
            opp_section_html += _mkt_hdr_html + sh + gh
            opp_section_text += _mkt_hdr_text + st + gt

    # ── Open positions section ────────────────────────────────────────────────
    pos_rows_html = ""
    pos_rows_text = ""
    _sig_colors = {"BUY": "#22c55e", "HOLD": "#facc15", "WAIT": "#f97316", "SELL": "#ef4444"}
    for p in open_positions:
        pnl = p.get("pnl_pct", 0.0) or 0.0
        pnl_color = "#22c55e" if pnl >= 0 else "#ef4444"
        pnl_str = f"{pnl:+.1f}%"
        stop_dist = p.get("stop_dist_pct")
        stop_str = f"{stop_dist:.1f}% below" if stop_dist is not None else "—"
        last_p = p.get("last_price")
        price_str = f"${last_p:,.2f}" if last_p else "—"
        entry_str = f"${p['entry_price']:,.2f}"
        cur_sig = p.get("current_signal") or ""
        sig_c = _sig_colors.get(cur_sig, "#94a3b8")
        sig_badge = (
            f'<span style="background:{sig_c}22;color:{sig_c};font-size:10px;font-weight:700;padding:1px 5px;border-radius:3px">{cur_sig}</span>'
            if cur_sig else '<span style="color:#94a3b8;font-size:11px">—</span>'
        )
        exit_warn = (
            ' <span style="color:#ef4444;font-size:10px;font-weight:700">⚠️ Exit?</span>'
            if cur_sig == "SELL" else ""
        )
        pos_rows_html += (
            f'<tr style="border-bottom:1px solid #f1f5f9">'
            f'<td style="padding:7px 10px;font-weight:700;font-size:13px">{p["symbol"]}{exit_warn}</td>'
            f'<td style="padding:7px 10px;font-size:12px;color:#64748b">{entry_str} → {price_str}</td>'
            f'<td style="padding:7px 10px;font-size:13px;font-weight:700;color:{pnl_color}">{pnl_str}</td>'
            f'<td style="padding:7px 10px">{sig_badge}</td>'
            f'<td style="padding:7px 10px;font-size:12px;color:#ef4444">{stop_str}</td>'
            f'<td style="padding:7px 10px;font-size:12px;color:#94a3b8">{p.get("hold_days",0)}d</td>'
            f'</tr>'
        )
        sig_text = f"[{cur_sig}]" if cur_sig else ""
        pos_rows_text += f"  {p['symbol']:6} {entry_str} → {price_str}  P&L {pnl_str}  Sig {sig_text:6}  Stop {stop_str}  {p.get('hold_days',0)}d\n"

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
          <th style="padding:6px 10px;font-size:11px;color:#475569;font-weight:600;text-align:left">Signal</th>
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

    # ── Signal performance (30d outcomes) section ──────────────────────────
    perf_section_html = ""
    if signal_performance and signal_performance.get("total", 0) > 0 and signal_performance.get("win_rate") is not None:
        sp_wr = signal_performance["win_rate"]
        sp_wr_pct = round(sp_wr * 100, 1)
        sp_wr_color = "#22c55e" if sp_wr >= 0.50 else "#f59e0b" if sp_wr >= 0.38 else "#ef4444"
        sp_ret = signal_performance.get("avg_return_pct")
        sp_ret_str = (f"+{sp_ret:.1f}%" if sp_ret and sp_ret > 0 else f"{sp_ret:.1f}%" if sp_ret is not None else "—")
        sp_ret_color = "#22c55e" if sp_ret and sp_ret > 0 else "#ef4444"
        sp_total = signal_performance.get("total", 0)
        by_h = signal_performance.get("by_horizon", {})

        def _h_row(h: str, v: dict) -> str:
            wr = (v.get("win_rate") or 0)
            wrc = "#22c55e" if wr >= 0.50 else "#f59e0b" if wr >= 0.38 else "#ef4444"
            ar = v.get("avg_return_pct")
            ar_s = (f"+{ar:.1f}%" if ar and ar > 0 else f"{ar:.1f}%" if ar is not None else "—")
            return (
                f'<tr style="border-bottom:1px solid #f1f5f9">'
                f'<td style="padding:5px 10px;font-size:12px;color:#64748b">{h}</td>'
                f'<td style="padding:5px 10px;font-size:12px;font-weight:700;text-align:right;color:{wrc}">{round(wr*100,1)}%</td>'
                f'<td style="padding:5px 10px;font-size:12px;text-align:right;color:#64748b">{v.get("count","—")}</td>'
                f'<td style="padding:5px 10px;font-size:12px;text-align:right;color:#94a3b8">{ar_s}</td>'
                f'</tr>'
            )

        h_rows = "".join(_h_row(h, v) for h, v in by_h.items() if v.get("count", 0) > 0)
        perf_section_html = f"""
    <div style="margin-top:24px">
      <div style="font-size:11px;font-weight:700;color:#818cf8;text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px">Signal Performance — Last 30 Days</div>
      <div style="display:flex;gap:16px;margin-bottom:10px">
        <div style="text-align:center">
          <div style="font-size:20px;font-weight:800;color:{sp_wr_color}">{sp_wr_pct}%</div>
          <div style="font-size:10px;color:#94a3b8">30d win rate</div>
        </div>
        <div style="text-align:center">
          <div style="font-size:20px;font-weight:800;color:{sp_ret_color}">{sp_ret_str}</div>
          <div style="font-size:10px;color:#94a3b8">avg return</div>
        </div>
        <div style="text-align:center">
          <div style="font-size:20px;font-weight:800;color:#94a3b8">{sp_total}</div>
          <div style="font-size:10px;color:#94a3b8">outcomes</div>
        </div>
      </div>
      {"" if not h_rows else f'<table style="width:100%;border-collapse:collapse;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden"><thead><tr style="background:#f8fafc"><th style="padding:5px 10px;font-size:10px;color:#94a3b8;text-align:left;text-transform:uppercase">Style</th><th style="padding:5px 10px;font-size:10px;color:#94a3b8;text-align:right;text-transform:uppercase">Win Rate</th><th style="padding:5px 10px;font-size:10px;color:#94a3b8;text-align:right;text-transform:uppercase">Signals</th><th style="padding:5px 10px;font-size:10px;color:#94a3b8;text-align:right;text-transform:uppercase">Avg Ret</th></tr></thead><tbody>{h_rows}</tbody></table>'}
    </div>"""

    # ── Top/Bottom symbol leaderboard (TIER97) ────────────────────────────
    sym_section_html = ""
    _by_sym = (signal_performance or {}).get("by_symbol", [])
    if len(_by_sym) >= 4:
        _top5 = _by_sym[:5]
        _top5_syms = {s["symbol"] for s in _top5}
        _bot5 = [s for s in reversed(_by_sym) if s["symbol"] not in _top5_syms][:5]

        def _sym_row(s: dict, color: str) -> str:
            ar = s.get("avg_return_pct")
            ar_s = (f"+{ar:.1f}%" if ar is not None and ar > 0 else f"{ar:.1f}%" if ar is not None else "—")
            wr_pct = round((s.get("win_rate") or 0) * 100)
            return (
                f'<tr style="border-bottom:1px solid #f1f5f9">'
                f'<td style="padding:4px 8px;font-weight:700;font-size:12px">{s["symbol"]}</td>'
                f'<td style="padding:4px 8px;font-size:12px;text-align:right;color:#64748b">{wr_pct}%</td>'
                f'<td style="padding:4px 8px;font-size:12px;text-align:right;font-weight:700;color:{color}">{ar_s}</td>'
                f'<td style="padding:4px 8px;font-size:11px;text-align:right;color:#94a3b8">{s.get("count", "—")}</td>'
                f'</tr>'
            )

        _col_hdr = (
            '<tr style="background:#f8fafc">'
            '<th style="padding:4px 8px;font-size:10px;color:#94a3b8;text-align:left;text-transform:uppercase">Symbol</th>'
            '<th style="padding:4px 8px;font-size:10px;color:#94a3b8;text-align:right;text-transform:uppercase">Win%</th>'
            '<th style="padding:4px 8px;font-size:10px;color:#94a3b8;text-align:right;text-transform:uppercase">Avg Ret</th>'
            '<th style="padding:4px 8px;font-size:10px;color:#94a3b8;text-align:right;text-transform:uppercase">N</th>'
            '</tr>'
        )
        _top_rows = "".join(_sym_row(s, "#22c55e") for s in _top5)
        _bot_rows = "".join(_sym_row(s, "#ef4444") for s in _bot5)
        sym_section_html = f"""
    <div style="margin-top:20px">
      <div style="font-size:11px;font-weight:700;color:#22c55e;text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px">Top Performers — Last 30 Days</div>
      <table style="width:100%;border-collapse:collapse;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;margin-bottom:14px">
        <thead>{_col_hdr}</thead><tbody>{_top_rows}</tbody>
      </table>
      <div style="font-size:11px;font-weight:700;color:#ef4444;text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px">Underperformers — Last 30 Days</div>
      <table style="width:100%;border-collapse:collapse;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden">
        <thead>{_col_hdr}</thead><tbody>{_bot_rows}</tbody>
      </table>
    </div>"""

    # ── BEAR regime warning banner ────────────────────────────────────────────
    bear_banner_html = ""
    bear_banner_text = ""
    if state == "bear":
        bear_banner_html = (
            '<div style="margin-top:14px;background:#fee2e2;border:1px solid #fca5a5;border-radius:8px;padding:10px 14px">'
            '<div style="font-size:12px;font-weight:700;color:#dc2626">⚠️ Bear Market Active</div>'
            '<div style="font-size:11px;color:#7f1d1d;margin-top:3px">'
            'Higher ML thresholds applied. Only BUY-signal opportunities shown. '
            'Reduce position sizing and prioritise capital preservation.'
            '</div></div>'
        )
        bear_banner_text = "\n⚠️  BEAR MARKET ACTIVE — higher thresholds; reduce size\n"

    _mkts_str = " + ".join(s["market"] for s in market_sections)
    subject = f"📊 Morning Digest [{_mkts_str}]: StockAI — {date_str} | Regime: {sl}"
    body_text = (
        f"StockAI Morning Digest [{_mkts_str}] — {date_str}\n"
        f"Market Regime: {sl}  |  {_idx_label}: {spy_str}{f' ({ret20_str} 20d)' if ret20_str else ''}"
        + ("" if _is_hk_digest else f"  |  VIX: {vix_str}{f' ({vix_trend})' if vix_trend else ''}") + "\n"
        + ("\n".join(regime_notes or []))
        + bear_banner_text
        + opp_section_text
        + pos_section_text
        + "\nNot financial advice. Paper trading simulation only.\n"
    )
    body_html = f"""<html><body style="font-family:sans-serif;color:#1e293b;background:#f8fafc;padding:24px;margin:0">
  <div style="max-width:560px;margin:auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08)">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px">
      <h2 style="margin:0;font-size:18px;color:#0f172a">📊 Morning Digest — HK + US</h2>
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
          <div style="font-size:11px;color:#64748b">{_idx_label} <strong style="color:#1e293b">{spy_str}</strong>{f' <span style="font-size:10px;color:{ret20_color};font-weight:700">{ret20_str} 20d</span>' if ret20_str else ''}</div>
          {f'<div style="font-size:11px;color:#64748b;margin-top:3px">VIX <strong style="color:#1e293b">{vix_str}</strong>{" <span style=\"font-size:10px;color:#f97316\">↑trend</span>" if vix_trend == "rising" else ""}</div>' if not _is_hk_digest else ''}
          {f'<div style="font-size:10px;color:#f59e0b;margin-top:3px">⚠ Breadth weak (small/mid-caps below 200MA)</div>' if breadth_weak else ''}
        </div>
        {f'<div style="flex:1"><ul style="margin:0;padding-left:16px">{regime_notes_html}</ul></div>' if regime_notes_html else ''}
      </div>
    </div>
    {bear_banner_html}

    {opp_section_html}
    {pos_section_html}
    {pat_section_html}
    {perf_section_html}
    {sym_section_html}

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


def send_post_open_digest_email(
    to: str,
    market: str,
    window: str,  # "30min" | "1hr"
    regime_changed: bool,
    prev_state: str | None,
    cur_state: str,
    cur_vix: float | None,
    positions: list,           # [{symbol, pnl_pct, current_price, current_stop, signal_now, signal_flipped, signal_prev}]
    new_signal_changes: list,  # [{symbol, signal, prev_signal}]
    top_movers: list,          # [{symbol, change_pct}]
    bottom_movers: list,       # [{symbol, change_pct}]
    vol_surge: list | None = None,  # [{symbol, volume_z}]
) -> bool:
    """Post-open market update — 30 min or 1 hour after {market} opens.

    Only sent when something changed (see send_post_open_digest's has_content check).
    The 1hr email is delta-only vs. the 30min email's snapshot — it will not repeat
    unchanged positions/signals already reported in the 30min email.
    """
    from datetime import date as _date
    date_str = _date.today().strftime("%b %d, %Y")
    window_label = "30 min after open" if window == "30min" else "1 hour after open"

    _state_color = {"bull": "#22c55e", "neutral": "#facc15", "choppy": "#f97316",
                     "risk_off": "#f97316", "bear": "#ef4444", "unknown": "#94a3b8"}
    _state_label = {"bull": "BULL", "neutral": "NEUTRAL", "choppy": "CHOPPY",
                     "risk_off": "RISK OFF", "bear": "BEAR", "unknown": "UNKNOWN"}

    # ── Regime change banner ──────────────────────────────────────────────────
    regime_html = ""
    regime_text = ""
    if regime_changed:
        pc = _state_color.get(prev_state, "#94a3b8")
        cc = _state_color.get(cur_state, "#94a3b8")
        pl = _state_label.get(prev_state, (prev_state or "?").upper())
        cl = _state_label.get(cur_state, cur_state.upper())
        vix_str = f" · VIX {cur_vix:.1f}" if cur_vix is not None else ""
        regime_html = f"""
    <div style="background:#fef3c7;border:1px solid #fde68a;border-radius:8px;padding:12px 16px;margin-bottom:20px">
      <div style="font-size:11px;font-weight:700;color:#92400e;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px">⚠ Regime Changed</div>
      <div style="font-size:14px;color:#374151">
        <span style="color:{pc};font-weight:700">{pl}</span> → <span style="color:{cc};font-weight:700">{cl}</span>{vix_str}
      </div>
    </div>"""
        regime_text = f"\n⚠ REGIME CHANGED: {pl} → {cl}{vix_str}\n"

    # ── Open positions ────────────────────────────────────────────────────────
    pos_rows_html = ""
    pos_lines_text = ""
    for p in positions:
        sym = p["symbol"]
        pct = p.get("pnl_pct")
        pct_str = f"{'+' if pct and pct >= 0 else ''}{pct:.1f}%" if pct is not None else "—"
        pct_color = "#22c55e" if pct and pct >= 0 else "#ef4444" if pct is not None else "#94a3b8"
        price = p.get("current_price")
        price_str = f"${price:,.2f}" if price else "—"
        stop = p.get("current_stop")
        stop_dist_str = "—"
        if price and stop:
            stop_dist_pct = (price - stop) / price * 100
            stop_dist_str = f"{stop_dist_pct:.1f}% to stop"
        flip_badge = ""
        flip_text = ""
        if p.get("signal_flipped"):
            sig_color = {"BUY": "#22c55e", "SELL": "#ef4444", "HOLD": "#facc15", "WAIT": "#f97316"}.get(p["signal_now"], "#94a3b8")
            flip_badge = f' <span style="background:{sig_color}22;color:{sig_color};font-size:10px;font-weight:700;padding:1px 6px;border-radius:3px;border:1px solid {sig_color}55">⚡ {p.get("signal_prev","?")}→{p["signal_now"]}</span>'
            flip_text = f" [SIGNAL FLIP: {p.get('signal_prev','?')}→{p['signal_now']}]"
        pos_rows_html += (
            f'<tr style="border-bottom:1px solid #f1f5f9">'
            f'<td style="padding:7px 10px;font-weight:700;font-size:13px">{sym}{flip_badge}</td>'
            f'<td style="padding:7px 10px;font-size:13px;font-weight:700;color:{pct_color}">{pct_str}</td>'
            f'<td style="padding:7px 10px;font-size:12px;color:#64748b">{price_str}</td>'
            f'<td style="padding:7px 10px;font-size:12px;color:#94a3b8">{stop_dist_str}</td>'
            f'</tr>'
        )
        pos_lines_text += f"  {sym:8}  {pct_str:>7}  {price_str:>10}  {stop_dist_str}{flip_text}\n"

    pos_section_html = ""
    pos_section_text = ""
    if pos_rows_html:
        pos_section_html = f"""
    <div style="margin-top:20px">
      <div style="font-size:11px;font-weight:700;color:#6366f1;text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px">Your Open Positions</div>
      <table style="width:100%;border-collapse:collapse;background:#fafafa;border-radius:8px;overflow:hidden;border:1px solid #e2e8f0">
        <tr style="background:#f1f5f9">
          <th style="padding:6px 10px;font-size:11px;color:#475569;text-align:left">Symbol</th>
          <th style="padding:6px 10px;font-size:11px;color:#475569;text-align:left">Move</th>
          <th style="padding:6px 10px;font-size:11px;color:#475569;text-align:left">Price</th>
          <th style="padding:6px 10px;font-size:11px;color:#475569;text-align:left">Stop Distance</th>
        </tr>
        {pos_rows_html}
      </table>
    </div>"""
        pos_section_text = f"\nYOUR OPEN POSITIONS:\n{pos_lines_text}"

    # ── New BUY/SELL signal changes ───────────────────────────────────────────
    sig_rows_html = ""
    sig_lines_text = ""
    for c in new_signal_changes[:10]:
        sig_color = "#22c55e" if c["signal"] == "BUY" else "#ef4444"
        prev_str = c.get("prev_signal") or "—"
        sig_rows_html += (
            f'<tr style="border-bottom:1px solid #f1f5f9">'
            f'<td style="padding:7px 10px;font-weight:700;font-size:13px">{c["symbol"]}</td>'
            f'<td style="padding:7px 10px;font-size:12px;color:#94a3b8">{prev_str} →</td>'
            f'<td style="padding:7px 10px;font-size:13px;font-weight:700;color:{sig_color}">{c["signal"]}</td>'
            f'</tr>'
        )
        sig_lines_text += f"  {c['symbol']:8}  {prev_str} → {c['signal']}\n"

    sig_section_html = ""
    sig_section_text = ""
    if sig_rows_html:
        sig_section_html = f"""
    <div style="margin-top:20px">
      <div style="font-size:11px;font-weight:700;color:#22c55e;text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px">New Signals Since Last Check</div>
      <table style="width:100%;border-collapse:collapse;background:#fafafa;border-radius:8px;overflow:hidden;border:1px solid #e2e8f0">
        {sig_rows_html}
      </table>
    </div>"""
        sig_section_text = f"\nNEW SIGNALS:\n{sig_lines_text}"

    # ── Top/bottom watchlist movers ───────────────────────────────────────────
    def _mover_row(m: dict) -> str:
        c = "#22c55e" if m["change_pct"] >= 0 else "#ef4444"
        s = "+" if m["change_pct"] >= 0 else ""
        return (
            f'<tr style="border-bottom:1px solid #f1f5f9">'
            f'<td style="padding:6px 10px;font-weight:700;font-size:13px">{m["symbol"]}</td>'
            f'<td style="padding:6px 10px;font-size:13px;font-weight:700;color:{c}">{s}{m["change_pct"]:.1f}%</td>'
            f'</tr>'
        )

    movers_html = ""
    movers_text = ""
    if top_movers or bottom_movers:
        gainers_html = "".join(_mover_row(m) for m in top_movers)
        losers_html = "".join(_mover_row(m) for m in bottom_movers)
        movers_html = f"""
    <div style="margin-top:20px;display:grid;grid-template-columns:1fr 1fr;gap:12px">
      <div>
        <div style="font-size:11px;font-weight:700;color:#22c55e;text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px">Top Gainers</div>
        <table style="width:100%;border-collapse:collapse;background:#fafafa;border-radius:8px;overflow:hidden;border:1px solid #e2e8f0">{gainers_html}</table>
      </div>
      <div>
        <div style="font-size:11px;font-weight:700;color:#ef4444;text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px">Top Losers</div>
        <table style="width:100%;border-collapse:collapse;background:#fafafa;border-radius:8px;overflow:hidden;border:1px solid #e2e8f0">{losers_html}</table>
      </div>
    </div>"""
        gainers_text = "".join(f"  {m['symbol']:8}  +{m['change_pct']:.1f}%\n" for m in top_movers)
        losers_text = "".join(f"  {m['symbol']:8}  {m['change_pct']:.1f}%\n" for m in bottom_movers)
        movers_text = f"\nTOP GAINERS:\n{gainers_text}\nTOP LOSERS:\n{losers_text}"

    # ── Volume surge — stocks trading meaningfully above their normal volume ────
    vol_surge_html = ""
    vol_surge_text = ""
    if vol_surge:
        def _vol_row(v: dict) -> str:
            vz = v["volume_z"]
            intensity = "#ef4444" if vz >= 3.0 else "#f97316" if vz >= 2.0 else "#f59e0b"
            return (
                f'<tr style="border-bottom:1px solid #f1f5f9">'
                f'<td style="padding:6px 10px;font-weight:700;font-size:13px">{v["symbol"]}</td>'
                f'<td style="padding:6px 10px;font-size:13px;font-weight:700;color:{intensity}">{vz:.1f}σ</td>'
                f'</tr>'
            )
        vol_rows_html = "".join(_vol_row(v) for v in vol_surge)
        vol_surge_html = f"""
    <div style="margin-top:20px">
      <div style="font-size:11px;font-weight:700;color:#f59e0b;text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px">Volume Surge (vs. 20d normal)</div>
      <table style="width:100%;border-collapse:collapse;background:#fafafa;border-radius:8px;overflow:hidden;border:1px solid #e2e8f0">{vol_rows_html}</table>
    </div>"""
        vol_surge_text = "\nVOLUME SURGE:\n" + "".join(f"  {v['symbol']:8}  {v['volume_z']:.1f}σ above normal\n" for v in vol_surge)

    subject_bits = []
    if regime_changed:
        subject_bits.append(f"Regime→{_state_label.get(cur_state, cur_state.upper())}")
    if any(p.get("signal_flipped") for p in positions):
        subject_bits.append("Signal flip")
    if new_signal_changes:
        subject_bits.append(f"{len(new_signal_changes)} new signal(s)")
    if vol_surge:
        subject_bits.append(f"{len(vol_surge)} volume surge")
    subject_detail = " · ".join(subject_bits) if subject_bits else "Update"
    subject = f"📈 {market} {window_label}: {subject_detail} — {date_str}"

    body_text = (
        f"{market} Post-Open Update — {window_label} — {date_str}\n"
        f"{regime_text}"
        f"{pos_section_text}"
        f"{sig_section_text}"
        f"{vol_surge_text}"
        f"{movers_text}"
    )
    body_html = f"""<!DOCTYPE html><html><body style="font-family:sans-serif;background:#f8fafc;padding:24px;margin:0">
  <div style="max-width:560px;margin:auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08)">
    <div style="margin-bottom:20px">
      <div style="font-size:12px;color:#94a3b8;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px">{market} Post-Open Update · {window_label} · {date_str}</div>
      <div style="font-size:20px;font-weight:700;color:#111827">What changed since {"open" if window == "30min" else "30 min ago"}</div>
    </div>
    {regime_html}
    {pos_section_html}
    {sig_section_html}
    {vol_surge_html}
    {movers_html}
    <p style="font-size:12px;color:#94a3b8;margin-top:24px;border-top:1px solid #e2e8f0;padding-top:12px">
      <a href="https://lausing.com/signal-filters" style="color:#6366f1">View signal filters →</a> ·
      <a href="https://lausing.com/paper-portfolio" style="color:#6366f1">View paper portfolio →</a>
    </p>
  </div>
</body></html>"""
    return send_email(to, subject, body_html, body_text)


def send_broker_reauth_email(to: str, broker_name: str, authorize_url: str) -> bool:
    """Notify the user that their broker OAuth tokens have expired and provide a re-auth link."""
    subject = f"Action Required: Re-authorize {broker_name} — tokens expired"
    body_text = (
        f"Your {broker_name} connection has expired and needs to be re-authorized.\n\n"
        f"E*Trade OAuth tokens expire every day at midnight ET.\n\n"
        f"Steps to re-authorize:\n"
        f"1. Visit this URL in your browser:\n   {authorize_url}\n\n"
        f"2. Log in to E*Trade and click Authorize\n\n"
        f"3. E*Trade will show you a PIN code — enter it at:\n"
        f"   https://lausing.com/paper-portfolio (Broker Settings → Re-authorize)\n\n"
        f"Until re-authorized, no new trades will be sent to {broker_name}.\n"
    )
    body_html = f"""
<html><body style="font-family:sans-serif;color:#1e293b;background:#f8fafc;padding:24px">
  <div style="max-width:520px;margin:auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08)">
    <h2 style="margin-top:0;color:#f97316">&#9888; Broker Re-authorization Required</h2>
    <p style="font-size:15px">Your <strong>{broker_name}</strong> connection has expired.
    E*Trade OAuth tokens expire every day at midnight ET and must be refreshed before trading begins.</p>
    <div style="background:#fef3c7;border:1px solid #f59e0b;border-radius:8px;padding:16px;margin:20px 0">
      <div style="font-size:13px;color:#92400e;font-weight:600">Until re-authorized, no new trades will be placed.</div>
    </div>
    <p style="font-weight:600;margin-bottom:8px">Step 1 — Click to authorize:</p>
    <a href="{authorize_url}" style="display:inline-block;background:#f97316;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;font-size:14px">
      Authorize {broker_name} &rarr;
    </a>
    <p style="margin-top:20px;font-size:14px;color:#475569">
      After clicking Authorize in E*Trade, you will see a <strong>PIN code</strong>.<br>
      Enter that PIN at <a href="https://lausing.com/paper-portfolio" style="color:#6366f1">lausing.com/paper-portfolio</a>
      under Broker Settings &rarr; Re-authorize.
    </p>
    <p style="font-size:12px;color:#94a3b8;margin-top:24px;border-top:1px solid #e2e8f0;padding-top:12px">
      StockAI sends this reminder each morning when an active broker connection needs re-authorization.
    </p>
  </div>
</body></html>"""
    return send_email(to, subject, body_html, body_text)


def send_webhook_notification(webhook_url: str, title: str, message: str, color: int = 0x3b82f6) -> bool:
    """Send a Discord/Slack-compatible webhook notification (embed format)."""
    try:
        import httpx as _httpx
        payload = {"embeds": [{"title": title, "description": message, "color": color}]}
        r = _httpx.post(webhook_url, json=payload, timeout=10)
        return r.status_code < 300
    except Exception as exc:
        log.warning("webhook.send_failed", url=webhook_url[:40], error=str(exc))
        return False
