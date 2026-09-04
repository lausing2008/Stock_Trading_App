"""Email delivery — supports Gmail SMTP and AWS SES.

Configure via .env:
  EMAIL_PROVIDER=smtp   → Gmail (or any SMTP relay)
  EMAIL_PROVIDER=ses    → AWS SES (boto3 must be installed + IAM role/creds set)
  EMAIL_PROVIDER=       → disabled (alerts still record in DB, no mail sent)
"""
from __future__ import annotations

import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from common.config import get_settings
from common.logging import get_logger

log = get_logger("email_service")
_settings = get_settings()

# T239-EMAIL2: Gmail's daily sending quota (550 5.4.5) is a TRANSIENT, self-healing failure —
# it clears on a rolling ~24h window — unlike a genuinely broken SMTP config (bad password,
# wrong host), which never recovers on its own. The scheduler's retry-give-up logic (DP-1,
# 5 retries then force-advance state) was designed for the latter and silently, permanently
# dropped real signal-change alerts during a multi-hour quota outage: 14 distinct alerts hit
# the 5-retry cap and gave up on 2026-07-08 while Gmail stayed capped for 6+ hours straight.
# Track quota-exceeded separately so callers can skip counting it toward that give-up limit.
_QUOTA_MARKERS = ("5.4.5", "daily user sending limit", "user-reported spam")
_quota_exceeded_until: float = 0.0  # unix timestamp; 0 = not currently quota-limited


def is_quota_exceeded() -> bool:
    """True if the last known SMTP failure was a Gmail daily-quota rejection, within the
    last hour. Callers should keep retrying (not give up) while this is True."""
    import time as _time
    return _time.time() < _quota_exceeded_until


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
    global _quota_exceeded_until
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
        _quota_exceeded_until = 0.0  # a real success means we're no longer quota-limited
        return True
    except Exception as exc:
        # T239-EMAIL1: subject was missing here (present on the success log two lines up),
        # making every failure indistinguishable from any other — impossible to tell which
        # alert/digest actually failed to send without cross-referencing caller-side logs that
        # also don't record it. Log the same fields as the success path.
        log.error("email.failed", provider=provider, to=to, subject=subject, error=str(exc))
        if any(marker in str(exc).lower() for marker in _QUOTA_MARKERS):
            import time as _time
            _quota_exceeded_until = _time.time() + 3600  # re-check in 1h, not indefinitely
            log.warning("email.quota_exceeded_detected",
                        note="treating as transient — will not count toward alert give-up retries")
        return False


def send_signal_alert_email(
    to: str, symbol: str, prev_signal: str | None, new_signal: str, analyst: str,
    signal_data: dict | None = None,
    fundamentals: dict | None = None,
    game_plan: dict | None = None,
    options_game_plan: object | None = None,
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
    # MD-SIGPRICE1: last_price is the price the signal was actually computed against (set at
    # signal-compute time in signal-engine) — using this rather than a separately-fetched live
    # quote keeps the displayed price consistent with what this exact signal/gate evaluation
    # saw, since the alert itself reads the stored DB signal (live=False), not a live one.
    current_price = reasons.get("last_price")

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
        ("Current price",         f"${current_price:,.2f}" if current_price is not None else "—"),
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

    # AUD-OPTIONS4-GAMEPLANBATCH: Advanced-tier-only (the caller already gates this — None here
    # means either the recipient isn't Advanced-tier, the symbol is outside the bounded daily
    # snapshot set, or no snapshot exists yet today; all 3 degrade to simply omitting this
    # section, never a fabricated plan). Uses the SAME real, already-computed daily snapshot the
    # scan-list row reads — never a live per-recipient fetch.
    options_game_plan_html = ""
    options_game_plan_text = ""
    if options_game_plan is not None and new_signal == "BUY":
        _ogp = options_game_plan
        _ogp_rows_html = ""
        _ogp_rows_text = ""
        if _ogp.put_strike is not None:
            _ogp_rows_html += (
                f'<tr><td style="padding:6px 10px;font-size:12px;color:#166534;font-weight:600">🛡️ Protective Put</td>'
                f'<td style="padding:6px 10px;font-size:12px;color:#64748b;font-family:monospace">'
                f'${_ogp.put_strike:.2f} exp {_ogp.put_expiry} · mid ${_ogp.put_mid_price:.2f}'
                f'</td></tr>'
            )
            _ogp_rows_text += f"  Protective Put: ${_ogp.put_strike:.2f} exp {_ogp.put_expiry}, mid ${_ogp.put_mid_price:.2f}\n"
        if _ogp.call_strike is not None:
            _ogp_rows_html += (
                f'<tr><td style="padding:6px 10px;font-size:12px;color:#166534;font-weight:600">💰 Covered Call</td>'
                f'<td style="padding:6px 10px;font-size:12px;color:#64748b;font-family:monospace">'
                f'${_ogp.call_strike:.2f} exp {_ogp.call_expiry} · mid ${_ogp.call_mid_price:.2f}'
                f'</td></tr>'
            )
            _ogp_rows_text += f"  Covered Call: ${_ogp.call_strike:.2f} exp {_ogp.call_expiry}, mid ${_ogp.call_mid_price:.2f}\n"
        if _ogp_rows_html:
            options_game_plan_html = f"""
    <div style="margin-top:16px">
      <div style="font-size:11px;font-weight:700;color:#38bdf8;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px">📊 Options Game Plan — {symbol} (Advanced tier)</div>
      <table style="width:100%;border-collapse:collapse;background:#eff6ff;border-radius:8px;overflow:hidden;border:1px solid #bfdbfe">
        {_ogp_rows_html}
      </table>
      <div style="font-size:10px;color:#64748b;margin-top:4px">As of {_ogp.as_of.isoformat() if hasattr(_ogp.as_of, "isoformat") else _ogp.as_of} — real, currently-listed contract prices, not a prediction.</div>
    </div>"""
            options_game_plan_text = f"\n--- Options Game Plan for {symbol} (Advanced tier) ---\n{_ogp_rows_text}"

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
    _price_tag = f" · ${current_price:,.2f}" if current_price is not None else ""
    subject = f"{subject_prefix}: {symbol} {prev_signal} → {new_signal}{horizon_tag}{_price_tag}{_conf_tag}{_bp_tag}"
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
    {options_game_plan_html}
    <p style="font-size:11px;color:#94a3b8;margin-top:24px;border-top:1px solid #e2e8f0;padding-top:16px">
      Not personalised financial advice. Always do your own research before acting.
    </p>
  </div>
</body></html>"""
    body_text += options_game_plan_text
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
    _vix_trend_badge = ' <span style="font-size:10px;color:#f97316">↑trend</span>' if vix_trend == "rising" else ""
    vix_line_html = (
        "" if _is_hk_digest else
        f'<div style="font-size:11px;color:#64748b;margin-top:3px">VIX <strong style="color:#1e293b">{vix_str}</strong>{_vix_trend_badge}</div>'
    )
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
          {vix_line_html}
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


def send_premarket_brief_email(
    to: str,
    date_str: str,
    market: str,
    macro_events: list[dict],
    my_earnings: list,
    recent_reactions: list,
    overnight_futures: list[dict] | None = None,
    premarket_movers: list[dict] | None = None,
    options_flow: list[dict] | None = None,
    attention_list: list[dict] | None = None,
) -> bool:
    """T249-MARKETMOVER-P3: pre-market brief — combines P0 (today's macro releases), P1
    (recipient's own symbols reporting earnings today), and P2 (macro reactions generated in
    the last 18h) into one email. Framed as historical-scenario education, not a prediction —
    every section describes what these kinds of events HAVE caused before, never what today's
    will do. `macro_events` items are the dict shape _macro_events_from_db() returns
    (type/date/title/description/impact/days_to_event); `my_earnings` items are
    {"symbol": str, "event": EarningsEvent}; `recent_reactions` items are EconomicEvent rows
    with reaction_text/reaction_generated_at populated.

    T257-OVERNIGHT-FLOW-BRIEF Phase 1: `overnight_futures` items are the dict shape
    _fetch_overnight_futures() returns ({"name", "ticker", "price", "change_pct"}) — reports
    a MEASURED overnight change (futures ARE the market's own current expectation for the
    open), never a prediction of whether that holds through the cash open. Defaults to `None`
    (treated as empty) so existing callers built before this field existed keep working.

    `premarket_movers` items are the dict shape _fetch_premarket_gappers() returns
    ({"symbol", "pre_close", "prior_close", "change_pct", "as_of"}) — reports a MEASURED gap
    vs. yesterday's close, same non-predictive framing as overnight_futures. Also defaults to
    `None` (treated as empty) for the same backward-compatibility reason.

    T257-OVERNIGHT-FLOW-BRIEF Phase 2: `options_flow` items are the dict shape
    _fetch_recent_options_flow() returns ({"symbol", "cp_ratio", "sentiment", "call_premium",
    "put_premium", "whale_count", "top_whale_premium"}) — reports yesterday's OBSERVED options
    positioning (a real, already-happened flow read), never a prediction of what today's flow
    will do. Also defaults to `None` (treated as empty) for the same reason.

    T257-OVERNIGHT-FLOW-BRIEF Phase 3: `attention_list` items are the dict shape
    _build_attention_list() returns ({"symbol", "reasons"}) — each already-computed, measured
    fact that made this symbol qualify (>=2 of premarket gap / unusual options flow / earnings
    today / a high-impact macro release today). Deliberately never a buy/sell direction call
    of its own — that's what the signal pipeline and the T257-TOP3 conviction alert are for,
    with their own tracked accuracy. Also defaults to `None` (treated as empty).
    """
    overnight_futures = overnight_futures or []
    premarket_movers = premarket_movers or []
    options_flow = options_flow or []
    attention_list = attention_list or []
    subject = f"🔔 Pre-Market Brief — {market} — {date_str}"

    _impact_color = {"critical": "#ef4444", "high": "#f97316", "medium": "#facc15"}

    # ── Section 1: today's macro releases ─────────────────────────────────────
    macro_rows_html = ""
    macro_rows_text = ""
    for e in macro_events:
        c = _impact_color.get(e.get("impact"), "#94a3b8")
        macro_rows_html += (
            f'<div style="padding:8px 0;border-bottom:1px solid #f1f5f9">'
            f'<span style="background:{c}22;color:{c};font-size:10px;font-weight:700;'
            f'padding:1px 6px;border-radius:4px;text-transform:uppercase">{e.get("impact","")}</span> '
            f'<strong style="font-size:13px">{e.get("title","")}</strong>'
            f'<div style="font-size:11px;color:#64748b;margin-top:2px">{e.get("description","")}</div>'
            f'</div>'
        )
        macro_rows_text += f'  [{(e.get("impact") or "").upper()}] {e.get("title","")}\n'

    # ── Section 2: recipient's own symbols reporting today ─────────────────────
    earnings_rows_html = ""
    earnings_rows_text = ""
    for item in my_earnings:
        sym, ev = item["symbol"], item["event"]
        est = f"${ev.eps_estimate:.2f}" if ev.eps_estimate is not None else "—"
        earnings_rows_html += (
            f'<div style="padding:8px 0;border-bottom:1px solid #f1f5f9">'
            f'<strong style="font-size:13px">{sym}</strong> reports today '
            f'<span style="font-size:11px;color:#64748b">(EPS est. {est})</span>'
            f'</div>'
        )
        earnings_rows_text += f"  {sym} reports today (EPS est. {est})\n"

    # ── Section 3: recent macro reactions (last 18h) ────────────────────────────
    reaction_rows_html = ""
    reaction_rows_text = ""
    for ev in recent_reactions[:5]:
        reaction_rows_html += (
            f'<div style="padding:8px 0;border-bottom:1px solid #f1f5f9">'
            f'<strong style="font-size:13px">{ev.title}</strong>'
            f'<div style="font-size:12px;color:#64748b;margin-top:3px;line-height:1.5">{ev.reaction_text}</div>'
            f'</div>'
        )
        reaction_rows_text += f"  {ev.title}: {ev.reaction_text}\n"

    # ── Section 4: overnight futures (T257-OVERNIGHT-FLOW-BRIEF Phase 1) ───────
    futures_rows_html = ""
    futures_rows_text = ""
    for f in overnight_futures:
        chg = f.get("change_pct")
        chg_color = "#16a34a" if (chg or 0) >= 0 else "#dc2626"
        chg_str = f"{chg:+.2f}%" if chg is not None else "—"
        price_str = f"{f.get('price'):,.2f}" if f.get("price") is not None else "—"
        futures_rows_html += (
            f'<div style="padding:8px 0;border-bottom:1px solid #f1f5f9;display:flex;'
            f'justify-content:space-between">'
            f'<strong style="font-size:13px">{f.get("name","")}</strong>'
            f'<span style="font-size:13px"><span style="color:#64748b">{price_str}</span> '
            f'<span style="color:{chg_color};font-weight:600">{chg_str}</span></span>'
            f'</div>'
        )
        futures_rows_text += f'  {f.get("name","")}: {price_str} ({chg_str})\n'

    # ── Section 5: premarket gappers (T257-OVERNIGHT-FLOW-BRIEF) ────────────────
    movers_rows_html = ""
    movers_rows_text = ""
    for m in premarket_movers:
        chg = m.get("change_pct")
        chg_color = "#16a34a" if (chg or 0) >= 0 else "#dc2626"
        chg_str = f"{chg:+.2f}%" if chg is not None else "—"
        pre_str = f"{m.get('pre_close'):,.2f}" if m.get("pre_close") is not None else "—"
        movers_rows_html += (
            f'<div style="padding:8px 0;border-bottom:1px solid #f1f5f9;display:flex;'
            f'justify-content:space-between">'
            f'<strong style="font-size:13px">{m.get("symbol","")}</strong>'
            f'<span style="font-size:13px"><span style="color:#64748b">{pre_str}</span> '
            f'<span style="color:{chg_color};font-weight:600">{chg_str}</span></span>'
            f'</div>'
        )
        movers_rows_text += f'  {m.get("symbol","")}: {pre_str} ({chg_str} vs. yesterday\'s close)\n'

    # ── Section 6: late-day options flow (T257-OVERNIGHT-FLOW-BRIEF Phase 2) ───
    flow_rows_html = ""
    flow_rows_text = ""
    for o in options_flow:
        cp = o.get("cp_ratio")
        cp_str = f"{cp:.2f}" if cp is not None else "—"
        sentiment = (o.get("sentiment") or "neutral").replace("_", " ")
        whale_note = ""
        if o.get("whale_count"):
            whale_note = f' · {o["whale_count"]} whale trade{"s" if o["whale_count"] != 1 else ""} (${o.get("top_whale_premium", 0):,.0f} top)'
        flow_rows_html += (
            f'<div style="padding:8px 0;border-bottom:1px solid #f1f5f9;display:flex;'
            f'justify-content:space-between">'
            f'<strong style="font-size:13px">{o.get("symbol","")}</strong>'
            f'<span style="font-size:12px;color:#64748b">cp_ratio {cp_str} · {sentiment}{whale_note}</span>'
            f'</div>'
        )
        flow_rows_text += f'  {o.get("symbol","")}: cp_ratio {cp_str}, {sentiment}{whale_note}\n'

    # ── Section 7: today's attention list (T257-OVERNIGHT-FLOW-BRIEF Phase 3) ──
    attention_rows_html = ""
    attention_rows_text = ""
    for a in attention_list:
        reasons_html = "".join(f'<li>{r}</li>' for r in a.get("reasons", []))
        attention_rows_html += (
            f'<div style="padding:8px 0;border-bottom:1px solid #f1f5f9">'
            f'<strong style="font-size:13px">{a.get("symbol","")}</strong>'
            f'<ul style="margin:4px 0 0;padding-left:18px;font-size:11px;color:#64748b">{reasons_html}</ul>'
            f'</div>'
        )
        reasons_text = "; ".join(a.get("reasons", []))
        attention_rows_text += f'  {a.get("symbol","")}: {reasons_text}\n'

    def _section(title: str, rows_html: str, empty_note: str) -> str:
        if not rows_html:
            return (
                f'<div style="margin-top:20px">'
                f'<div style="font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;'
                f'letter-spacing:.07em;margin-bottom:6px">{title}</div>'
                f'<div style="font-size:12px;color:#94a3b8">{empty_note}</div>'
                f'</div>'
            )
        return (
            f'<div style="margin-top:20px">'
            f'<div style="font-size:11px;font-weight:700;color:#6366f1;text-transform:uppercase;'
            f'letter-spacing:.07em;margin-bottom:6px">{title}</div>'
            f'{rows_html}'
            f'</div>'
        )

    body_html = f"""<html><body style="font-family:sans-serif;color:#1e293b;background:#f8fafc;padding:24px;margin:0">
  <div style="max-width:520px;margin:auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08)">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px">
      <h2 style="margin:0;font-size:18px;color:#0f172a">🔔 Pre-Market Brief — {market}</h2>
      <span style="font-size:13px;color:#94a3b8">{date_str}</span>
    </div>

    {_section("Overnight Futures", futures_rows_html, "Overnight futures data unavailable this morning.")}
    {_section("Premarket Movers", movers_rows_html, "No significant premarket movers detected.")}
    {_section("Late-Day Options Flow", flow_rows_html, "No notable options flow detected in yesterday's session.")}
    {_section("Today's Attention List", attention_rows_html, "No symbols currently qualify (need 2+ independent signals).")}
    {_section("Today's Macro Releases", macro_rows_html, "No high/critical-importance releases scheduled today.")}
    {_section("Your Symbols Reporting Today", earnings_rows_html, "None of your watched symbols report earnings today.")}
    {_section("Recent Macro Reactions (18h)", reaction_rows_html, "No macro reactions generated in the last 18 hours.")}

    <p style="font-size:11px;color:#94a3b8;margin-top:28px;border-top:1px solid #e2e8f0;padding-top:14px">
      Futures reflect the market's own current expectation for the open — not a prediction of
      whether it holds through the cash session. Options flow reflects yesterday's already-
      observed positioning, not a forecast. The Attention List surfaces symbols where 2+
      independent signals overlap — it is not a buy/sell recommendation. Historical-scenario
      context only elsewhere in this brief — not financial advice. StockAI · {date_str}
    </p>
  </div>
</body></html>"""

    body_text = (
        f"StockAI Pre-Market Brief — {market} — {date_str}\n\n"
        f"OVERNIGHT FUTURES\n"
        + (futures_rows_text or "  Unavailable this morning.\n")
        + f"\nPREMARKET MOVERS\n"
        + (movers_rows_text or "  None detected.\n")
        + f"\nLATE-DAY OPTIONS FLOW\n"
        + (flow_rows_text or "  None detected in yesterday's session.\n")
        + f"\nTODAY'S ATTENTION LIST\n"
        + (attention_rows_text or "  No symbols currently qualify (need 2+ independent signals).\n")
        + f"\nTODAY'S MACRO RELEASES\n"
        + (macro_rows_text or "  None scheduled today.\n")
        + f"\nYOUR SYMBOLS REPORTING TODAY\n"
        + (earnings_rows_text or "  None.\n")
        + f"\nRECENT MACRO REACTIONS (18h)\n"
        + (reaction_rows_text or "  None.\n")
        + "\nFutures reflect the market's current expectation for the open, not a prediction of"
        " whether it holds. Options flow reflects yesterday's already-observed positioning, not"
        " a forecast. The Attention List surfaces symbols with 2+ independent signals — it is"
        " not a buy/sell recommendation. Historical-scenario context only elsewhere — not"
        " financial advice.\n"
    )
    return send_email(to, subject, body_html, body_text)


def send_volume_anomaly_email(to: str, alerts: list[dict]) -> bool:
    """T257-VOLUME-ANOMALY-ALERT: one email per recipient listing every symbol that tripped
    the abnormal-volume scan this cycle (already capped/deduped by the caller). Each alert
    dict: {symbol, rvol, price, change_pct, level_note (optional, e.g. "testing resistance
    at $105.00")}. Reports MEASURED facts only — never a "this WILL break out" prediction,
    matching this repo's established honesty discipline for alerts of this kind.
    """
    n = len(alerts)
    subject = f"📊 Abnormal Volume — {n} stock{'s' if n != 1 else ''} trading unusually heavy"

    rows_html = ""
    rows_text = ""
    for a in alerts:
        sym = a["symbol"]
        rvol = a["rvol"]
        chg = a.get("change_pct")
        chg_color = "#22c55e" if (chg or 0) >= 0 else "#ef4444"
        chg_str = f"{'+' if chg is not None and chg >= 0 else ''}{chg:.2f}%" if chg is not None else "—"
        price_str = f"${a['price']:.2f}" if a.get("price") else "—"
        level_note = a.get("level_note")
        level_html = f'<div style="font-size:11px;color:#94a3b8;margin-top:2px">{level_note}</div>' if level_note else ""
        level_text = f" ({level_note})" if level_note else ""
        rows_html += (
            f'<div style="padding:10px 0;border-bottom:1px solid #f1f5f9">'
            f'<div style="display:flex;justify-content:space-between;align-items:baseline">'
            f'<strong style="font-size:14px">{sym}</strong>'
            f'<span style="font-size:13px;color:{chg_color};font-weight:700">{chg_str}</span>'
            f'</div>'
            f'<div style="font-size:12px;color:#64748b;margin-top:2px">{price_str} · <strong style="color:#f59e0b">{rvol:.1f}x</strong> normal volume</div>'
            f'{level_html}'
            f'</div>'
        )
        rows_text += f"  {sym}: {price_str}, {chg_str}, {rvol:.1f}x normal volume{level_text}\n"

    body_html = f"""<html><body style="font-family:sans-serif;color:#1e293b;background:#f8fafc;padding:24px;margin:0">
  <div style="max-width:480px;margin:auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08)">
    <h2 style="margin-top:0;color:#f59e0b">📊 Abnormal Volume Detected</h2>
    <p style="font-size:13px;color:#64748b;margin-top:-8px">{n} stock{'s' if n != 1 else ''} trading at an unusual multiple of normal volume this cycle.</p>
    <div style="margin-top:12px">{rows_html}</div>
    <p style="font-size:11px;color:#94a3b8;margin-top:24px;border-top:1px solid #e2e8f0;padding-top:14px">
      Volume ratio and price level are measured facts as of this scan — not a prediction of
      whether any level actually breaks. Not financial advice.
    </p>
  </div>
</body></html>"""
    body_text = (
        f"Abnormal Volume Detected — {n} stock{'s' if n != 1 else ''}\n\n"
        + rows_text
        + "\nMeasured facts as of this scan, not a prediction. Not financial advice.\n"
    )
    return send_email(to, subject, body_html, body_text)


# T270-SQUEEZE-DAYSTOCOVER-ALERT: display-only label matching scheduler.py's own
# _SQUEEZE_CRITICAL_DAYS_TO_COVER=2.0 constant (this file has no import path to that module,
# so the value is restated here for the email copy — the actual gating decision is made once,
# upstream in scheduler.py, before a candidate ever reaches this function).
_SQUEEZE_CRITICAL_DAYS_TO_COVER_LABEL = "2.0"
# T260-SQUEEZE-IGNITION: same display-only-restatement convention, matching scheduler.py's own
# _SQUEEZE_MIN_INTRADAY_MOVE_PCT=3.0 (the classic short-squeeze alert's own move floor, which
# this alert's own candidates are, by construction, always below).
_SQUEEZE_MIN_INTRADAY_MOVE_PCT_LABEL = "3%"


def _regime_warning_lines(regime: str | None) -> tuple[str, str]:
    """T264-SQUEEZEFAMILY-REGIME-FLAG (2026-08-15): shared across all 3 squeeze-family emails
    (send_short_squeeze_email/send_gamma_unwind_email/send_prebreakout_email) — a SOFT,
    informational-only line, never a reason an alert was suppressed (that decision is never
    made; every candidate that clears its own rule gate still fires regardless of regime — see
    each check_*_alerts() function's own docstring in scheduler.py for why). Returns ("", "")
    for a bull (or missing/unknown) regime — the common case — so callers can always splice
    this in unconditionally without an extra if-check of their own."""
    if not regime or regime == "bull":
        return "", ""
    html = (
        f'<p style="font-size:11px;color:#b45309;background:#fffbeb;border-radius:6px;'
        f'padding:8px 10px;margin:10px 0 0 0">⚠ Market regime: <strong>{regime}</strong> — '
        f'broader market conditions are weak right now. This alert still fired on its own '
        f'merits; use extra caution.</p>'
    )
    text = f"\n⚠ Market regime: {regime} — broader market conditions are weak right now. This alert still fired on its own merits; use extra caution.\n"
    return html, text


def _short_interest_age_str(short_interest_date: str | None) -> str:
    """Renders the short-interest reading's own age, with a staleness-tier callout past 15
    days — AUD-SQUEEZE250725-ISSUE2: the audit recommended EITHER tightening the hard reject
    from 30 to 21 days, OR adding a staleness tier surfaced in the email. A hard tighten would
    silently drop candidates 21-30 days old that currently fire, a real behavior change with no
    visibility into what changed; a visual tier lets the recipient judge for themselves (the
    same "surface, don't silently reject" pattern already used for 0-DTE gamma-unwind rows and
    the browsable screener's own is_stale flag) while keeping the existing 30-day hard reject
    as the outer floor. Shared by send_short_squeeze_email() and send_prebreakout_email(), which
    previously duplicated this exact age-string logic with no tier at all.

    Bands: <=15d "fresh" (no callout), 15-21d "moderately stale", 21-30d "very stale" — matching
    the audit's own suggested band boundaries.
    """
    if not short_interest_date:
        return ""
    try:
        age_days = (date.today() - date.fromisoformat(short_interest_date)).days
    except (ValueError, TypeError):
        return f" (as of {short_interest_date})"
    if age_days <= 15:
        return f" (as of {short_interest_date}, {age_days}d ago)"
    tier = "very stale" if age_days > 21 else "moderately stale"
    return f" (as of {short_interest_date}, {age_days}d ago — {tier})"


def send_short_squeeze_email(to: str, candidates: list[dict]) -> bool:
    """One email per recipient listing every symbol that NEWLY crossed into "shorts likely
    getting squeezed RIGHT NOW" territory this cycle: short_percent_of_float >= 15 AND the
    stock is already up >=3% intraday. Each dict: {symbol, short_percent_of_float, change_pct,
    price, short_ratio (optional), days_to_cover_critical (optional), game_plan (optional)}.
    Explicitly framed as a BUY-direction signal — the thesis is that heavily-shorted sellers
    are being forced to cover into a rise already in progress, adding buying pressure on top
    of whatever started the move. Reports the MEASURED setup, never a claim that the squeeze
    will keep going — that depends on the move continuing, which this cannot predict.

    T270-SQUEEZE-DAYSTOCOVER-ALERT: days_to_cover_critical (short_ratio <= 2.0, the ~25th
    percentile of real candidates that already clear the float-short bar — see the constant's
    own comment in scheduler.py) is an ESCALATION on top of the existing thesis, not a second,
    separate signal — a critical candidate means shorts don't just have a lot of stock
    borrowed, they can't quietly unwind that position over a handful of normal trading days
    even if they wanted to, which is the concrete mechanism behind "shorts may be forced to
    cover." The subject line and each critical row are visually distinguished; the underlying
    thesis and disclaimer are unchanged.

    game_plan (when present, from scheduler.py's _squeeze_game_plan()) reuses the SAME
    entry/stop/target math the real paper-trading engine computes for every actual trade —
    not separate, invented numbers for this alert — via _build_game_plan_for_style()'s
    SWING-style profile. Omitted for a symbol with no recent SWING signal on file (a real,
    honest gap — not every squeeze candidate has one), in which case the email simply skips
    that section for that stock rather than showing a placeholder.

    AUD288-SQUEEZE-NO-VOLUME-CONFIRM: rvol (session-elapsed-scaled RVOL, the volume-
    confirmation floor this alert now requires — see check_short_squeeze_alerts()'s own
    docstring) rendered alongside short_percent_of_float, same "Nx avg volume" phrasing
    send_squeeze_ignition_email() already established for its own earlier-stage sibling.
    """
    n = len(candidates)
    n_critical = sum(1 for c in candidates if c.get("days_to_cover_critical"))
    if n_critical:
        subject = (
            f"🚨 Short Squeeze Alert (BUY signal) — {n_critical} CRITICAL, {n} total "
            f"stock{'s' if n != 1 else ''} shorts may be covering"
        )
    else:
        subject = f"🚀 Short Squeeze Alert (BUY signal) — {n} stock{'s' if n != 1 else ''} shorts may be covering"

    rows_html = ""
    rows_text = ""
    for c in candidates:
        sym = c["symbol"]
        spf = c["short_percent_of_float"]
        chg = c.get("change_pct")
        price = c.get("price")
        # AUD265-SHORT-INTEREST-AGE-NEVER-CHECKED: surfaces the real settlement date so a
        # recipient can judge for themselves how current the short-interest figure is, rather
        # than every reading implicitly reading as "measured just now." AUD-SQUEEZE250725-
        # ISSUE2 extends this with a moderately/very-stale tier past 15/21 days.
        si_date = c.get("short_interest_date")
        si_str = _short_interest_age_str(si_date)
        chg_str = f"+{chg:.2f}%" if chg is not None else "—"
        price_str = f"${price:.2f}" if price else "—"
        rvol = c.get("rvol")
        rvol_str = f" · {rvol:.1f}x avg volume" if rvol is not None else ""
        short_ratio = c.get("short_ratio")
        is_critical = bool(c.get("days_to_cover_critical"))
        dtc_str = ""
        if short_ratio is not None:
            dtc_str = f' · <strong style="color:{"#dc2626" if is_critical else "#64748b"}">{short_ratio:.1f}d to cover</strong>'
            if is_critical:
                dtc_str += " 🚨"
        plan = c.get("game_plan")
        plan_html = ""
        plan_text = ""
        if plan:
            plan_html = (
                f'<div style="font-size:11px;color:#475569;margin-top:6px;padding-top:6px;border-top:1px dashed #e2e8f0">'
                f'Game plan (SWING): entry ~${plan["entry1"]:.2f} · stop ${plan["stop"]:.2f} · '
                f'target ${plan["take_profit"]:.2f}'
                f'</div>'
            )
            plan_text = (
                f"    Game plan (SWING): entry ~${plan['entry1']:.2f}, "
                f"stop ${plan['stop']:.2f}, target ${plan['take_profit']:.2f}\n"
            )
        # T264-SHORTSQUEEZE-PREBREAKOUT-CONFIDENCE (extended 2026-08-15): same measured-
        # win-rate rendering as send_prebreakout_email()'s own cal_str/cal_text.
        cal_win_rate = c.get("calibrated_win_rate")
        cal_count = c.get("calibrated_win_rate_count")
        cal_html = ""
        cal_text = ""
        if cal_win_rate is not None and cal_count is not None:
            cal_html = (
                f'<div style="font-size:11px;color:#475569;margin-top:4px">'
                f'Measured historical win rate: {cal_win_rate * 100:.0f}% <span style="color:#94a3b8">(n={cal_count})</span></div>'
            )
            cal_text = f"    Measured historical win rate: {cal_win_rate * 100:.0f}% (n={cal_count})\n"
        else:
            cal_html = '<div style="font-size:11px;color:#94a3b8;margin-top:4px">Not enough resolved history yet for a measured win rate</div>'
            cal_text = "    Not enough resolved history yet for a measured win rate\n"
        regime_html, regime_text = _regime_warning_lines(c.get("market_regime"))
        # AUD-SQUEEZE3-UWSHORTINTERESTCORROBORATION: a real, material disagreement between the
        # free-tier short_percent_of_float (already shown above) and Unusual Whales' own
        # independently-sourced reading — surfaced as extra context for the recipient to weigh,
        # never used to suppress the alert (see the scheduler's own comment for why).
        uw_disagree_html = ""
        uw_disagree_text = ""
        if c.get("uw_disagrees"):
            _uw_spf = c["uw_short_percent_of_float"]
            uw_disagree_html = (
                f'<div style="font-size:11px;color:#b45309;margin-top:4px">'
                f'⚠️ Unusual Whales reports a different short-float reading: <strong>{_uw_spf:.1f}%</strong> '
                f'(vs. {spf:.1f}% above) — the two sources disagree materially, worth checking before acting.'
                f'</div>'
            )
            uw_disagree_text = f"    UW disagrees: reports {_uw_spf:.1f}% short of float (vs. {spf:.1f}% above)\n"
        row_border = "border:1px solid rgba(220,38,38,0.3);border-radius:8px;padding:10px 12px;margin-bottom:6px" if is_critical else "padding:10px 0;border-bottom:1px solid #f1f5f9"
        rows_html += (
            f'<div style="{row_border}">'
            f'<div style="display:flex;justify-content:space-between;align-items:baseline">'
            f'<strong style="font-size:14px">{sym}</strong>'
            f'<span style="font-size:13px;color:#22c55e;font-weight:700">{chg_str}</span>'
            f'</div>'
            f'<div style="font-size:12px;color:#64748b;margin-top:2px">{price_str} · <strong style="color:#ef4444">{spf:.1f}%</strong> of float short{rvol_str}{si_str}{dtc_str}</div>'
            f'{plan_html}{cal_html}{regime_html}{uw_disagree_html}'
            f'</div>'
        )
        dtc_text = f", {short_ratio:.1f}d to cover" + (" [CRITICAL]" if is_critical else "") if short_ratio is not None else ""
        rows_text += f"  {sym}: {price_str}, {chg_str} today, {spf:.1f}% of float short{rvol_str}{si_str}{dtc_text}\n" + plan_text + cal_text + regime_text + uw_disagree_text

    critical_note = (
        f'<p style="font-size:12px;color:#dc2626;font-weight:600;margin-top:-4px">'
        f'🚨 {n_critical} of these would take {_SQUEEZE_CRITICAL_DAYS_TO_COVER_LABEL} or fewer days of average volume just to close out their short position — a critically thin exit.'
        f'</p>' if n_critical else ""
    )
    body_html = f"""<html><body style="font-family:sans-serif;color:#1e293b;background:#f8fafc;padding:24px;margin:0">
  <div style="max-width:480px;margin:auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08)">
    <h2 style="margin-top:0;color:#ef4444">🚀 Short Squeeze Alert — BUY-direction signal</h2>
    <p style="font-size:13px;color:#64748b;margin-top:-8px">{n} heavily-shorted stock{'s' if n != 1 else ''} just started moving up hard, right now.</p>
    {critical_note}
    <div style="margin-top:12px">{rows_html}</div>
    <p style="font-size:11px;color:#94a3b8;margin-top:24px;border-top:1px solid #e2e8f0;padding-top:14px">
      Thesis: high short interest + a real rally already in progress means shorts may be
      forced to cover, adding buying pressure on top of the move. "Days to cover" (short_ratio
      = shares short ÷ average daily volume) measures how many days of NORMAL trading it would
      take shorts to fully exit — a low reading means they cannot quietly unwind even if they
      wanted to, sharpening (not replacing) the same squeeze thesis. This reports a MEASURED
      setup, not a prediction the move continues — a squeeze can reverse just as fast as it
      started. Game plan (where shown) is the same illustrative SWING-style entry/stop/target
      math the paper-trading engine uses — a reference point, not a guaranteed fill. Not
      financial advice.
    </p>
  </div>
</body></html>"""
    body_text = (
        f"Short Squeeze Alert (BUY signal) — {n} stock{'s' if n != 1 else ''}\n\n"
        + rows_text
        + "\nMeasured setup, not a prediction the move continues. Game plan (where shown) is "
        + "illustrative SWING-style entry/stop/target math, not a guaranteed fill. "
        + "Not financial advice.\n"
    )
    return send_email(to, subject, body_html, body_text)


def send_squeeze_ignition_email(to: str, candidates: list[dict]) -> bool:
    """T260-SQUEEZE-IGNITION: the EARLY-WARNING sibling of send_short_squeeze_email() — fires
    while a high-short-float stock's intraday move is still BELOW the classic alert's 3% floor
    (1-2.9%), but its trading volume has already started picking up in a session-elapsed-scaled
    sense (see check_squeeze_ignition_alerts()'s own docstring for the full mechanism). Each
    dict: {symbol, short_percent_of_float, change_pct, rvol, price, short_ratio (optional),
    game_plan (optional), calibrated_win_rate/_count (optional), market_regime (optional)}.

    Deliberately softer framing than the classic alert's "BUY signal" subject line — this is a
    WATCH, not a firm signal, since most candidates here are expected to fade back into
    ordinary trading rather than becoming a real squeeze. The measured-win-rate field (reused
    from the classic short_squeeze alert's own calibration, since both gate on the same short-
    float metric) exists specifically so a recipient isn't left guessing how seriously to take
    an earlier-stage, lower-confidence read.
    """
    n = len(candidates)
    subject = f"👀 Squeeze Watch (early stage) — {n} high-short-interest stock{'s' if n != 1 else ''} starting to move"

    rows_html = ""
    rows_text = ""
    for c in candidates:
        sym = c["symbol"]
        spf = c["short_percent_of_float"]
        chg = c.get("change_pct")
        rvol = c.get("rvol")
        price = c.get("price")
        si_date = c.get("short_interest_date")
        si_str = _short_interest_age_str(si_date)
        chg_str = f"+{chg:.2f}%" if chg is not None else "—"
        rvol_str = f"{rvol:.1f}x avg volume" if rvol is not None else "—"
        price_str = f"${price:.2f}" if price else "—"
        short_ratio = c.get("short_ratio")
        dtc_str = f' · {short_ratio:.1f}d to cover' if short_ratio is not None else ""
        plan = c.get("game_plan")
        plan_html = ""
        plan_text = ""
        if plan:
            plan_html = (
                f'<div style="font-size:11px;color:#475569;margin-top:6px;padding-top:6px;border-top:1px dashed #e2e8f0">'
                f'Game plan (SWING): entry ~${plan["entry1"]:.2f} · stop ${plan["stop"]:.2f} · '
                f'target ${plan["take_profit"]:.2f}'
                f'</div>'
            )
            plan_text = (
                f"    Game plan (SWING): entry ~${plan['entry1']:.2f}, "
                f"stop ${plan['stop']:.2f}, target ${plan['take_profit']:.2f}\n"
            )
        cal_win_rate = c.get("calibrated_win_rate")
        cal_count = c.get("calibrated_win_rate_count")
        cal_html = ""
        cal_text = ""
        if cal_win_rate is not None and cal_count is not None:
            cal_html = (
                f'<div style="font-size:11px;color:#475569;margin-top:4px">'
                f'Measured historical win rate (short_squeeze family): {cal_win_rate * 100:.0f}% '
                f'<span style="color:#94a3b8">(n={cal_count})</span></div>'
            )
            cal_text = f"    Measured historical win rate (short_squeeze family): {cal_win_rate * 100:.0f}% (n={cal_count})\n"
        else:
            cal_html = '<div style="font-size:11px;color:#94a3b8;margin-top:4px">Not enough resolved history yet for a measured win rate</div>'
            cal_text = "    Not enough resolved history yet for a measured win rate\n"
        regime_html, regime_text = _regime_warning_lines(c.get("market_regime"))
        rows_html += (
            f'<div style="padding:10px 0;border-bottom:1px solid #f1f5f9">'
            f'<div style="display:flex;justify-content:space-between;align-items:baseline">'
            f'<strong style="font-size:14px">{sym}</strong>'
            f'<span style="font-size:13px;color:#0ea5e9;font-weight:700">{chg_str}</span>'
            f'</div>'
            f'<div style="font-size:12px;color:#64748b;margin-top:2px">{price_str} · <strong style="color:#ef4444">{spf:.1f}%</strong> of float short · {rvol_str}{dtc_str}{si_str}</div>'
            f'{plan_html}{cal_html}{regime_html}'
            f'</div>'
        )
        rows_text += f"  {sym}: {price_str}, {chg_str} today, {spf:.1f}% of float short, {rvol_str}{dtc_str}{si_str}\n" + plan_text + cal_text + regime_text

    body_html = f"""<html><body style="font-family:sans-serif;color:#1e293b;background:#f8fafc;padding:24px;margin:0">
  <div style="max-width:480px;margin:auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08)">
    <h2 style="margin-top:0;color:#0ea5e9">👀 Squeeze Watch — early stage, not a firm signal</h2>
    <p style="font-size:13px;color:#64748b;margin-top:-8px">{n} heavily-shorted stock{'s' if n != 1 else ''} showing an early volume pickup, still below the {_SQUEEZE_MIN_INTRADAY_MOVE_PCT_LABEL} move that triggers the full Short Squeeze Alert.</p>
    <div style="margin-top:12px">{rows_html}</div>
    <p style="font-size:11px;color:#94a3b8;margin-top:24px;border-top:1px solid #e2e8f0;padding-top:14px">
      Thesis: high short interest + volume ALREADY starting to build, before a real move has
      fully confirmed. This is an intentionally EARLIER, LOWER-CONFIDENCE read than the Short
      Squeeze Alert — most candidates here are expected to fade back into ordinary trading
      rather than becoming a real squeeze; that trade-off is the whole point of trading some
      false positives for an earlier warning. If the move keeps building, you will also
      receive the regular Short Squeeze Alert once it clears the higher confirmation bar. Game
      plan (where shown) is the same illustrative SWING-style entry/stop/target math the paper-
      trading engine uses — a reference point, not a guaranteed fill. Not financial advice.
    </p>
  </div>
</body></html>"""
    body_text = (
        f"Squeeze Watch (early stage) — {n} stock{'s' if n != 1 else ''}\n\n"
        + rows_text
        + "\nEarly-stage, lower-confidence read — most candidates fade back to ordinary "
        + "trading. If the move keeps building you'll also get the full Short Squeeze Alert. "
        + "Game plan (where shown) is illustrative SWING-style entry/stop/target math, not a "
        + "guaranteed fill. Not financial advice.\n"
    )
    return send_email(to, subject, body_html, body_text)


def send_gamma_unwind_email(to: str, candidates: list[dict]) -> bool:
    """Options-expiry gamma-unwind alert — the SECOND squeeze mechanism (see check_gamma_
    unwind_alerts()'s own docstring for the full explanation vs. the classic short-squeeze
    alert above). Each dict: {symbol, expiry, days_to_expiry, dominant_side ("calls"/"puts"),
    concentration_pct, total_oi_near_money, price, calibrated_win_rate/_count (optional, see
    _build_squeeze_family_calibration()'s own docstring in scheduler.py)}.

    Deliberately framed as a DIRECTIONAL WATCH, not a firm BUY/SELL call — unlike the classic
    short-squeeze alert (which has a clean long-only thesis), which way a gamma unwind actually
    pushes price depends on whether market makers are net long or short gamma at that strike,
    which this app does not compute. A calls-dominant near-the-money block near expiry has
    historically been associated with EITHER a sharp upside continuation (dealers short gamma,
    forced to chase) OR a "max pain" pin/reversal toward the heaviest strike — reported as
    "watch closely," never asserted as one specific direction. The calibrated_win_rate field
    (2026-08-15) measures "did THIS side (calls or puts) go on to a real 10d win" per its own
    resolved SqueezeAlertOutcome rows — it does not resolve the directional-uncertainty caveat
    above, it just tells you how this specific side has performed historically once enough
    resolved outcomes exist.
    """
    n = len(candidates)
    subject = f"⚡ Options Expiry Watch — {n} stock{'s' if n != 1 else ''} with concentrated OI near expiry"

    rows_html = ""
    rows_text = ""
    for c in candidates:
        sym = c["symbol"]
        side = c["dominant_side"]
        side_color = "#22c55e" if side == "calls" else "#ef4444"
        conc = c["concentration_pct"]
        dte = c["days_to_expiry"]
        # AUD265-ZERO-DTE-OI-IS-STALE-BY-CONSTRUCTION: open interest is exchange-published once
        # per day, as of the PRIOR session's close — genuinely current for a 1-5 day-to-expiry
        # row, but for a dte=0 (expires TODAY) row the OI figure is already up to a full trading
        # session stale relative to whatever has happened intraday today, right when it matters
        # most (the day the position actually unwinds). Qualify only the 0-DTE row, since it's
        # the one case where "as of when" materially changes what the number means.
        #
        # AUD-SQUEEZE250725-ISSUE4: previously this qualifier was inline text only, easy for a
        # user scanning quickly to miss — now also drives an amber row border/badge, matching
        # send_short_squeeze_email()'s own is_critical/row_border pattern for days_to_cover
        # (amber rather than that pattern's red, since this is a staleness NOTE, not a risk
        # escalation like critical days-to-cover).
        is_zero_dte = dte == 0
        if is_zero_dte:
            dte_str = "expires TODAY (OI as of yesterday's close) ⚠️"
        else:
            dte_str = f"expires in {dte}d"
        oi = c["total_oi_near_money"]
        price_str = f"${c['price']:.2f}" if c.get("price") else "—"
        cal_win_rate = c.get("calibrated_win_rate")
        cal_count = c.get("calibrated_win_rate_count")
        cal_html = ""
        cal_text = ""
        if cal_win_rate is not None and cal_count is not None:
            cal_html = (
                f'<div style="font-size:11px;color:#475569;margin-top:4px">'
                f'Measured historical win rate ({side}-dominant): {cal_win_rate * 100:.0f}% '
                f'<span style="color:#94a3b8">(n={cal_count})</span></div>'
            )
            cal_text = f"    Measured historical win rate ({side}-dominant): {cal_win_rate * 100:.0f}% (n={cal_count})\n"
        else:
            cal_html = '<div style="font-size:11px;color:#94a3b8;margin-top:4px">Not enough resolved history yet for a measured win rate</div>'
            cal_text = "    Not enough resolved history yet for a measured win rate\n"
        regime_html, regime_text = _regime_warning_lines(c.get("market_regime"))
        row_border = (
            "border:1px solid rgba(217,119,6,0.35);border-radius:8px;padding:10px 12px;margin-bottom:6px"
            if is_zero_dte else "padding:10px 0;border-bottom:1px solid #f1f5f9"
        )
        rows_html += (
            f'<div style="{row_border}">'
            f'<div style="display:flex;justify-content:space-between;align-items:baseline">'
            f'<strong style="font-size:14px">{sym}</strong>'
            f'<span style="font-size:13px;color:{side_color};font-weight:700">{conc:.0f}% {side}</span>'
            f'</div>'
            f'<div style="font-size:12px;color:#64748b;margin-top:2px">{price_str} · {oi:,} contracts near the money · {dte_str} ({c["expiry"]})</div>'
            f'{cal_html}{regime_html}'
            f'</div>'
        )
        rows_text += f"  {sym}: {price_str}, {conc:.0f}% {side}-dominant, {oi:,} near-money OI, {dte_str} ({c['expiry']})\n" + cal_text + regime_text

    body_html = f"""<html><body style="font-family:sans-serif;color:#1e293b;background:#f8fafc;padding:24px;margin:0">
  <div style="max-width:480px;margin:auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08)">
    <h2 style="margin-top:0;color:#f59e0b">⚡ Options Expiry Watch — directional watch, not a call</h2>
    <p style="font-size:13px;color:#64748b;margin-top:-8px">{n} stock{'s' if n != 1 else ''} with heavy, lopsided options open interest near the current price, close to expiry.</p>
    <div style="margin-top:12px">{rows_html}</div>
    <p style="font-size:11px;color:#94a3b8;margin-top:24px;border-top:1px solid #e2e8f0;padding-top:14px">
      Thesis: when market makers who sold this options block unwind their hedge near/at
      expiry, that unwind itself can move the stock sharply. This is a proxy signal (near-
      the-money open-interest concentration), NOT a real gamma-exposure calculation — which
      way the unwind actually pushes price is genuinely uncertain from this data alone, so
      this is a WATCH, not a directional BUY/SELL signal. Not financial advice.
    </p>
  </div>
</body></html>"""
    body_text = (
        f"Options Expiry Watch — {n} stock{'s' if n != 1 else ''} (directional watch, not a call)\n\n"
        + rows_text
        + "\nProxy signal, not a real gamma-exposure calc — direction is genuinely uncertain. Not financial advice.\n"
    )
    return send_email(to, subject, body_html, body_text)


def send_options_flow_alert_email(to: str, candidates: list[dict], omitted_count: int = 0) -> bool:
    """MPE-OPTIONS-FLOW-ALERT: real Unusual Whales unusual-options-activity alert. Each dict:
    {symbol, option_chain, option_type ("call"/"put"), direction ("bullish"/"bearish"), strike,
    expiry, price, total_premium, ask_side_dominant, volume_oi_ratio, has_sweep, alert_rule,
    calibrated_win_rate/_count (optional)}.

    Unlike send_gamma_unwind_email()'s hedged "watch, not a call" framing (which is honest about
    NOT knowing dealer positioning), this alert reports a genuinely MEASURED direction — UW's
    own real ask-side/bid-side premium split, not a proxy — so it's framed with the same
    confidence as send_short_squeeze_email()'s BUY-thesis framing, just direction-aware (both
    bullish and bearish rows in the same email, color-coded). Still explicitly a MEASURED-FACT
    report, never a claim the stock will actually move — see check_options_flow_alerts()'s own
    docstring for the full honesty framing this mirrors.

    AUD-OPTIONSFLOW-FLOODED: `candidates` is already capped by the caller (largest premium
    first) to keep this email readable — `omitted_count` is how many additional, real,
    genuinely-recorded candidates didn't make the cap (still visible on the options-flow
    dashboard page, never silently dropped from anywhere but this one email).
    """
    n = len(candidates)
    n_bullish = sum(1 for c in candidates if c["direction"] == "bullish")
    n_bearish = n - n_bullish
    subject = (
        f"🎯 Unusual Options Activity — {n} alert{'s' if n != 1 else ''} "
        f"({n_bullish} bullish, {n_bearish} bearish)"
    )

    rows_html = ""
    rows_text = ""
    for c in candidates:
        sym = c["symbol"]
        direction = c["direction"]
        dir_color = "#22c55e" if direction == "bullish" else "#ef4444"
        opt_type = c["option_type"]
        strike = c.get("strike")
        expiry = c.get("expiry")
        price = c.get("price")
        premium = c.get("total_premium")
        ask_dominant = c.get("ask_side_dominant")
        side_str = "aggressive BUYING (ask-side)" if ask_dominant else "aggressive SELLING (bid-side)"
        strike_str = f"${strike:.2f}" if strike is not None else "—"
        price_str = f"${price:.2f}" if price else "—"
        premium_str = f"${premium:,.0f}" if premium is not None else "—"
        expiry_str = expiry or "—"
        has_sweep = c.get("has_sweep")
        sweep_str = " · SWEEP" if has_sweep else ""
        vol_oi = c.get("volume_oi_ratio")
        vol_oi_str = f" · {vol_oi:.1f}x existing OI" if vol_oi is not None else ""
        cal_win_rate = c.get("calibrated_win_rate")
        cal_count = c.get("calibrated_win_rate_count")
        cal_html = ""
        cal_text = ""
        if cal_win_rate is not None and cal_count is not None:
            cal_html = (
                f'<div style="font-size:11px;color:#475569;margin-top:4px">'
                f'Measured historical win rate ({direction}): {cal_win_rate * 100:.0f}% '
                f'<span style="color:#94a3b8">(n={cal_count})</span></div>'
            )
            cal_text = f"    Measured historical win rate ({direction}): {cal_win_rate * 100:.0f}% (n={cal_count})\n"
        else:
            cal_html = '<div style="font-size:11px;color:#94a3b8;margin-top:4px">Not enough resolved history yet for a measured win rate</div>'
            cal_text = "    Not enough resolved history yet for a measured win rate\n"
        rows_html += (
            f'<div style="padding:10px 0;border-bottom:1px solid #f1f5f9">'
            f'<div style="display:flex;justify-content:space-between;align-items:baseline">'
            f'<strong style="font-size:14px">{sym}</strong>'
            f'<span style="font-size:13px;color:{dir_color};font-weight:700">{direction.upper()} · {opt_type.upper()}</span>'
            f'</div>'
            f'<div style="font-size:12px;color:#64748b;margin-top:2px">'
            f'{price_str} underlying · {strike_str} strike, exp {expiry_str} · {premium_str} premium, {side_str}{sweep_str}{vol_oi_str}'
            f'</div>'
            f'{cal_html}'
            f'</div>'
        )
        rows_text += (
            f"  {sym}: {direction.upper()} {opt_type.upper()}, {price_str} underlying, "
            f"{strike_str} strike exp {expiry_str}, {premium_str} premium, {side_str}{sweep_str}{vol_oi_str}\n"
            + cal_text
        )

    omitted_html = (
        f'<p style="font-size:12px;color:#7c3aed;margin-top:8px">+ {omitted_count} more alert'
        f'{"s" if omitted_count != 1 else ""} today (smaller premium) — see the Options Flow '
        f'dashboard for the full list.</p>'
    ) if omitted_count > 0 else ""
    omitted_text = (
        f"\n+ {omitted_count} more alert{'s' if omitted_count != 1 else ''} today (smaller "
        f"premium) — see the Options Flow dashboard for the full list.\n"
    ) if omitted_count > 0 else ""

    body_html = f"""<html><body style="font-family:sans-serif;color:#1e293b;background:#f8fafc;padding:24px;margin:0">
  <div style="max-width:480px;margin:auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08)">
    <h2 style="margin-top:0;color:#6d28d9">🎯 Unusual Options Activity</h2>
    <p style="font-size:13px;color:#64748b;margin-top:-8px">{n} real Unusual Whales flow alert{'s' if n != 1 else ''} — large, urgent options positioning detected right now.</p>
    <div style="margin-top:12px">{rows_html}</div>
    {omitted_html}
    <p style="font-size:11px;color:#94a3b8;margin-top:24px;border-top:1px solid #e2e8f0;padding-top:14px">
      This reports a MEASURED fact — real, unusual options activity (a rule-based sweep/repeated-
      hits detection over the full options tape) with a real ask-side/bid-side directional
      split — not a prediction that the stock will actually move, or by how much. Not financial
      advice.
    </p>
  </div>
</body></html>"""
    body_text = (
        f"Unusual Options Activity — {n} alert{'s' if n != 1 else ''} ({n_bullish} bullish, {n_bearish} bearish)\n\n"
        + rows_text
        + omitted_text
        + "\nMeasured fact (real options flow), not a prediction. Not financial advice.\n"
    )
    return send_email(to, subject, body_html, body_text)


def send_dark_pool_alert_email(to: str, candidates: list[dict], omitted_count: int = 0) -> bool:
    """T323-DARKPOOL: real large off-exchange block print detected via Unusual Whales'
    `/api/darkpool/{ticker}`. Each dict: {symbol, price, size, premium, venue, executed_at}.

    HONEST FRAMING, matching this app's own established alert-honesty discipline (T257-VOLUME-
    ANOMALY-ALERT, send_options_flow_alert_email, every squeeze alert already shipped): this
    reports a MEASURED fact — a large block genuinely printed off-exchange, real size, real
    price, real venue — never a claim about WHY it happened or that the stock will move as a
    result. Institutional block trades cross dark pools for many reasons (index rebalancing,
    portfolio hedging, block-crossing to avoid market impact) that have nothing to do with a
    directional view — this is explicitly NOT framed as "smart money is bullish/bearish," only
    "size just moved off-exchange, here's the print."

    `candidates` is already capped by the caller (largest premium first) to keep this email
    readable — `omitted_count` is how many additional, real, genuinely-recorded prints didn't
    make the cap.
    """
    n = len(candidates)
    subject = f"🌊 Dark Pool Activity — {n} large block print{'s' if n != 1 else ''}"

    rows_html = ""
    rows_text = ""
    for c in candidates:
        sym = c["symbol"]
        price = c.get("price")
        size = c.get("size")
        premium = c.get("premium")
        venue = c.get("venue") or "—"
        price_str = f"${price:.2f}" if price is not None else "—"
        size_str = f"{size:,}" if size is not None else "—"
        premium_str = f"${premium:,.0f}" if premium is not None else "—"
        rows_html += (
            f'<div style="padding:10px 0;border-bottom:1px solid #f1f5f9">'
            f'<div style="display:flex;justify-content:space-between;align-items:baseline">'
            f'<strong style="font-size:14px">{sym}</strong>'
            f'<span style="font-size:13px;color:#0369a1;font-weight:700">{premium_str}</span>'
            f'</div>'
            f'<div style="font-size:12px;color:#64748b;margin-top:2px">'
            f'{size_str} shares @ {price_str} · venue {venue}'
            f'</div>'
            f'</div>'
        )
        rows_text += f"  {sym}: {size_str} shares @ {price_str} = {premium_str} premium (venue {venue})\n"

    omitted_html = (
        f'<p style="font-size:12px;color:#0369a1;margin-top:8px">+ {omitted_count} more print'
        f'{"s" if omitted_count != 1 else ""} today (smaller size) — see the stock page\'s '
        f'Market Pressure panel for the full recent list.</p>'
    ) if omitted_count > 0 else ""
    omitted_text = (
        f"\n+ {omitted_count} more print{'s' if omitted_count != 1 else ''} today (smaller "
        f"size) — see the stock page's Market Pressure panel for the full recent list.\n"
    ) if omitted_count > 0 else ""

    body_html = f"""<html><body style="font-family:sans-serif;color:#1e293b;background:#f8fafc;padding:24px;margin:0">
  <div style="max-width:480px;margin:auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08)">
    <h2 style="margin-top:0;color:#0369a1">🌊 Dark Pool Activity</h2>
    <p style="font-size:13px;color:#64748b;margin-top:-8px">{n} real large off-exchange block print{'s' if n != 1 else ''} on your watched symbols.</p>
    <div style="margin-top:12px">{rows_html}</div>
    {omitted_html}
    <p style="font-size:11px;color:#94a3b8;margin-top:24px;border-top:1px solid #e2e8f0;padding-top:14px">
      This reports a MEASURED fact — a real, large block trade printed off-exchange (FINRA-
      reported dark pool venue) — not a claim about why it happened or that the stock will move
      as a result. Institutional blocks cross dark pools for many reasons unrelated to a
      directional view. Not financial advice.
    </p>
  </div>
</body></html>"""
    body_text = (
        f"Dark Pool Activity — {n} large block print{'s' if n != 1 else ''}\n\n"
        + rows_text
        + omitted_text
        + "\nMeasured fact (a real off-exchange print), not a prediction of direction. Not financial advice.\n"
    )
    return send_email(to, subject, body_html, body_text)


def send_prebreakout_email(to: str, candidates: list[dict]) -> bool:
    """T264-SHORTSQUEEZE-PREBREAKOUT: "coiling" alert — the pre-move counterpart to
    send_short_squeeze_email() above, direct user request: "predict the short sell not able to
    recover and send me the alert BEFORE it starts to breakout." Whereas the classic short-
    squeeze alert fires once a real move is ALREADY in progress, this fires while the stock is
    still compressing — high short interest + Bollinger Band width and ATR both near a 6-month
    low, a real precondition for a squeeze, with no breakout confirmed yet.

    Each dict: {symbol, short_percent_of_float, bb_width_pctile, atr_pctile, volume_dried_up,
    price, options_cp_ratio (optional, only when the ~2-week-deep OptionsFlowSnapshot table
    has a recent reading for this symbol), ml_price_direction_confidence/_model_version
    (optional), calibrated_win_rate/_count (optional) — see check_prebreakout_alerts()'s own
    docstring for exactly what each new field measures.

    HONESTY, stated explicitly per this app's own standing discipline: there is currently no
    trained SQUEEZE-BREAKOUT-specific model behind this alert (see
    PreBreakoutAlertOutcome.model_confidence, always None today) — real historical backtesting
    found only ~68 qualifying historical days across this app's whole universe, far too few to
    fit and validate a real model without overfitting noise, and won't clear that bar for well
    over a year at the current weekly-snapshot pace. The rule gate reports a measured
    precondition (coiling + high short interest), never a probability or a timeline for
    when/whether a breakout actually happens.

    T264-SHORTSQUEEZE-PREBREAKOUT-CONFIDENCE (2026-08-15) added two honestly-scoped SECOND
    signals shown alongside the rule gate — both clearly labeled for what they actually are,
    not conflated with a squeeze-specific prediction: ml_price_direction_confidence reuses
    this app's EXISTING, already-trained general per-symbol price-direction model (a genuinely
    independent read, not fit on this alert's own thin dataset at all); calibrated_win_rate is
    a MEASURED historical win rate from this alert's own resolved outcomes, bucketed by
    short-interest band, shown with its real sample count and only once that count clears 30 —
    below that, the email says so explicitly rather than showing a number.
    """
    n = len(candidates)
    subject = f"⏳ Pre-Breakout Watch — {n} stock{'s' if n != 1 else ''} coiling with high short interest"

    rows_html = ""
    rows_text = ""
    for c in candidates:
        sym = c["symbol"]
        spf = c["short_percent_of_float"]
        price = c.get("price")
        price_str = f"${price:.2f}" if price else "—"
        bb_pctile = c.get("bb_width_pctile")
        atr_pctile = c.get("atr_pctile")
        vol_dried = c.get("volume_dried_up")
        compress_str = ""
        if bb_pctile is not None and atr_pctile is not None:
            compress_str = f"BB width {bb_pctile * 100:.0f}th pctile, ATR {atr_pctile * 100:.0f}th pctile (6mo)"
        vol_str = " · volume drying up" if vol_dried else ""
        # AUD265-SHORT-INTEREST-AGE-NEVER-CHECKED (extended to this alert 2026-08-15): same
        # shared age/staleness-tier rendering as send_short_squeeze_email() — a recipient
        # shouldn't have to guess how current the short-interest figure is.
        si_date = c.get("short_interest_date")
        si_str = _short_interest_age_str(si_date)

        cp_ratio = c.get("options_cp_ratio")
        options_str = ""
        options_text = ""
        if cp_ratio is not None:
            lean = "call-heavy" if cp_ratio > 1.2 else ("put-heavy" if cp_ratio < 1 / 1.2 else "balanced")
            options_str = f'<div style="font-size:11px;color:#475569;margin-top:4px">Options flow: {lean} (cp_ratio {cp_ratio:.2f})</div>'
            options_text = f"    Options flow: {lean} (cp_ratio {cp_ratio:.2f})\n"

        ml_conf = c.get("ml_price_direction_confidence")
        ml_str = ""
        ml_text = ""
        if ml_conf is not None:
            ml_str = (
                f'<div style="font-size:11px;color:#475569;margin-top:4px">'
                f'General ML price-direction read: {ml_conf:.0f} confidence '
                f'<span style="color:#94a3b8">(this app\'s existing model — NOT squeeze-specific)</span></div>'
            )
            ml_text = f"    General ML price-direction read: {ml_conf:.0f} confidence (existing model, NOT squeeze-specific)\n"

        cal_win_rate = c.get("calibrated_win_rate")
        cal_count = c.get("calibrated_win_rate_count")
        cal_str = ""
        cal_text = ""
        if cal_win_rate is not None and cal_count is not None:
            cal_str = (
                f'<div style="font-size:11px;color:#475569;margin-top:4px">'
                f'Measured historical win rate: {cal_win_rate * 100:.0f}% <span style="color:#94a3b8">(n={cal_count})</span></div>'
            )
            cal_text = f"    Measured historical win rate: {cal_win_rate * 100:.0f}% (n={cal_count})\n"
        else:
            cal_str = '<div style="font-size:11px;color:#94a3b8;margin-top:4px">Not enough resolved history yet for a measured win rate</div>'
            cal_text = "    Not enough resolved history yet for a measured win rate\n"

        regime_html, regime_text = _regime_warning_lines(c.get("market_regime"))
        rows_html += (
            f'<div style="padding:10px 0;border-bottom:1px solid #f1f5f9">'
            f'<div style="display:flex;justify-content:space-between;align-items:baseline">'
            f'<strong style="font-size:14px">{sym}</strong>'
            f'<span style="font-size:12px;color:#64748b">{price_str}</span>'
            f'</div>'
            f'<div style="font-size:12px;color:#64748b;margin-top:2px"><strong style="color:#ef4444">{spf:.1f}%</strong> of float short{si_str} · {compress_str}{vol_str}</div>'
            f'{options_str}{ml_str}{cal_str}{regime_html}'
            f'</div>'
        )
        rows_text += f"  {sym}: {price_str}, {spf:.1f}% of float short{si_str}, {compress_str}{vol_str}\n" + options_text + ml_text + cal_text + regime_text

    body_html = f"""<html><body style="font-family:sans-serif;color:#1e293b;background:#f8fafc;padding:24px;margin:0">
  <div style="max-width:480px;margin:auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08)">
    <h2 style="margin-top:0;color:#f59e0b">⏳ Pre-Breakout Watch</h2>
    <p style="font-size:13px;color:#64748b;margin-top:-8px">{n} heavily-shorted stock{'s' if n != 1 else ''} {'is' if n == 1 else 'are'} compressing (coiling) — no breakout confirmed yet.</p>
    <div style="margin-top:12px">{rows_html}</div>
    <p style="font-size:11px;color:#94a3b8;margin-top:24px;border-top:1px solid #e2e8f0;padding-top:14px">
      Thesis: high short interest + price/volatility compressing toward a 6-month low is the
      real precondition a short squeeze needs to build — this reports the SETUP, not a
      prediction of if or when it resolves into a move. RULE-BASED ONLY: no trained model
      backs this alert yet (too little historical data exists to validate one honestly — see
      the Squeeze Alert Performance admin page). Options flow (where shown) is a real reading
      from a still-thin (~2-week) data history — treat as a minor tilt, not a signal on its
      own. Not financial advice.
    </p>
  </div>
</body></html>"""
    body_text = (
        f"Pre-Breakout Watch — {n} stock{'s' if n != 1 else ''} coiling with high short interest\n\n"
        + rows_text
        + "\nRULE-BASED ONLY (no trained model yet — see Squeeze Alert Performance admin page). "
        + "Reports a measured setup, not a prediction of if/when it resolves. Not financial advice.\n"
    )
    return send_email(to, subject, body_html, body_text)


def send_squeeze_watch_revert_email(
    to: str, symbol: str, watch_type: str, reason: str,
    current_price: float | None, current_metric: float | None,
) -> bool:
    """T260-BEARISH-PUTS-WATCHLIST: one-shot email the moment a user's manually-tracked
    short-side watch (from short-squeeze.tsx's "Add to watch" button) shows real evidence the
    short-side pressure has faded — sent once, then the SqueezeWatch row is marked reverted so
    it never fires again for the same watch (re-adding it re-arms tracking from scratch).

    watch_type is "short_squeeze" (classic short-interest-of-float squeeze) or "bearish_puts"
    (the puts-heavy options-expiry watch) — the copy differs slightly per type since the two
    mechanisms are genuinely different (see SqueezeWatch's own docstring in shared/db/models.py).
    """
    label = "Short Squeeze Watch" if watch_type == "short_squeeze" else "Bearish Puts Watch"
    subject = f"↩ {label} Reverted — {symbol}"
    price_str = f"${current_price:.2f}" if current_price is not None else "—"
    metric_label = "Short % of float" if watch_type == "short_squeeze" else "Puts concentration"
    metric_str = f"{current_metric:.1f}%" if current_metric is not None else "—"

    body_html = f"""<html><body style="font-family:sans-serif;color:#1e293b;background:#f8fafc;padding:24px;margin:0">
  <div style="max-width:480px;margin:auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08)">
    <h2 style="margin-top:0;color:#22c55e">↩ {label} Reverted — {symbol}</h2>
    <p style="font-size:13px;color:#64748b;margin-top:-8px">
      The short-side setup you were tracking on <strong>{symbol}</strong> shows real signs of fading.
    </p>
    <div style="background:#f1f5f9;border-radius:8px;padding:16px;margin:16px 0">
      <div style="font-size:13px;color:#1e293b"><strong>Why:</strong> {reason}</div>
      <div style="font-size:12px;color:#64748b;margin-top:8px">Current price: {price_str} · {metric_label}: {metric_str}</div>
    </div>
    <p style="font-size:11px;color:#94a3b8;margin-top:24px;border-top:1px solid #e2e8f0;padding-top:14px">
      This watch has been marked reverted and will not alert again — re-add it from the Short
      Squeeze page if you want to track it fresh. A faded short-side setup is real, measured
      evidence, not a guarantee price keeps rising — always confirm with the stock's own AI
      Signal/Confluence Score before acting. Not financial advice.
    </p>
  </div>
</body></html>"""
    body_text = (
        f"{label} Reverted — {symbol}\n\n"
        f"Why: {reason}\n"
        f"Current price: {price_str}, {metric_label}: {metric_str}\n\n"
        "This watch will not alert again — re-add it from the Short Squeeze page to track it fresh. "
        "Not a guarantee price keeps rising. Not financial advice.\n"
    )
    return send_email(to, subject, body_html, body_text)


def send_sr_watch_alert_email(
    to: str, symbol: str, level_kind: str, level_price: float,
    current_price: float, atr: float, atr_multiplier: float,
) -> bool:
    """SR-WATCH-PROXIMITY-ALERT: fires once when price first enters an ATR-scaled band around
    the nearest support or resistance level for a symbol the user is watching — "come look and
    decide whether to buy/sell yourself," never an automated trade signal. Fires again only
    after price moves back out of the band and re-enters (state tracked via SrWatch.
    currently_near in scheduler.py's check_sr_watch_reverts(), not a Redis dedup key — see that
    model's own docstring for why this needs a persistent True/False state rather than
    SqueezeWatch's permanent one-shot flag).

    level_kind is "support" or "resistance" — the copy and framing differ (support: a
    potential bounce/buy zone; resistance: a potential rejection/sell zone), matching how the
    "How to Trade It" guidance for support/resistance already frames these two cases elsewhere
    in this app (see the Volume Profile design docs).
    """
    is_support = level_kind == "support"
    verb = "approaching support" if is_support else "approaching resistance"
    color = "#22c55e" if is_support else "#ef4444"
    distance_pct = abs(current_price - level_price) / current_price * 100 if current_price else 0.0
    framing = (
        "a level where buyers have historically stepped in — some traders watch for a bounce "
        "here, others for a breakdown if it fails to hold."
        if is_support else
        "a level where sellers have historically stepped in — some traders watch for a "
        "rejection here, others for a breakout if price clears it."
    )
    subject = f"📍 {symbol} {verb} — ${level_price:.2f}"

    body_html = f"""<html><body style="font-family:sans-serif;color:#1e293b;background:#f8fafc;padding:24px;margin:0">
  <div style="max-width:480px;margin:auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08)">
    <h2 style="margin-top:0;color:{color}">📍 {symbol} is {verb}</h2>
    <p style="font-size:13px;color:#64748b;margin-top:-8px">
      Price is within {atr_multiplier:.1f}x its own ATR of a computed {level_kind} level — {framing}
    </p>
    <div style="background:#f1f5f9;border-radius:8px;padding:16px;margin:16px 0">
      <div style="font-size:13px;color:#1e293b">
        <strong>{level_kind.capitalize()} level:</strong> ${level_price:.2f}<br>
        <strong>Current price:</strong> ${current_price:.2f} ({distance_pct:.2f}% away)<br>
        <strong>ATR(14):</strong> ${atr:.2f}
      </div>
    </div>
    <p style="font-size:11px;color:#94a3b8;margin-top:24px;border-top:1px solid #e2e8f0;padding-top:14px">
      This is a measured price fact, not a prediction of what happens next — a level can hold,
      break, or get retested several times. Check the stock's own AI Signal, Confluence Score,
      and the chart's own S/R lines before deciding to buy or sell. This watch will alert again
      once price moves away from this level and returns. Not financial advice.
    </p>
  </div>
</body></html>"""
    body_text = (
        f"{symbol} is {verb} — ${level_price:.2f}\n\n"
        f"{level_kind.capitalize()} level: ${level_price:.2f}\n"
        f"Current price: ${current_price:.2f} ({distance_pct:.2f}% away)\n"
        f"ATR(14): ${atr:.2f}\n\n"
        "A measured price fact, not a prediction — a level can hold, break, or get retested. "
        "Check the stock's own AI Signal/Confluence Score before deciding. This watch fires "
        "again once price moves away and returns. Not financial advice.\n"
    )
    return send_email(to, subject, body_html, body_text)


def send_sector_rotation_email(to: str, candidates: list[dict]) -> bool:
    """AUD-SECTOR-EMERGING-ALERT: fires when a sector NEWLY becomes an "Emerging Leader" this
    week (its K-Score rank among sectors is climbing into the top half) — an OPPORTUNITY-finding
    alert, not a risk/exit one: the point is "here's a sector turning, and the top stocks in it
    right now." Each dict: {sector, delta (K-Score point change vs ~4 weeks ago, may be None),
    rank (this week's rank among sectors, may be None), top_stocks: [{symbol, name, k_score}]}.

    Deliberately reports the MEASURED rank/K-Score trajectory, never a claim that the sector
    "will" outperform — sector rotation is a real, tracked signal (see sector_trajectory.py),
    but still a probabilistic tailwind, not a guarantee any specific stock in it performs well.
    """
    n = len(candidates)
    subject = f"📈 Sector Rotation — {n} sector{'s' if n != 1 else ''} newly emerging"

    rows_html = ""
    rows_text = ""
    for c in candidates:
        sector = c["sector"]
        delta = c.get("delta")
        rank = c.get("rank")
        delta_str = f"+{delta:.1f} pts" if delta is not None else "—"
        rank_str = f"#{rank}" if rank is not None else "—"
        stocks_html = "".join(
            f'<span style="display:inline-block;margin:3px 6px 0 0;padding:2px 8px;border-radius:4px;background:#f1f5f9;font-size:11px;font-weight:700;color:#1e293b">{s["symbol"]} ({s["k_score"]:.0f})</span>'
            for s in c.get("top_stocks", [])
        )
        stocks_text = ", ".join(f'{s["symbol"]} ({s["k_score"]:.0f})' for s in c.get("top_stocks", []))
        rows_html += (
            f'<div style="padding:10px 0;border-bottom:1px solid #f1f5f9">'
            f'<div style="display:flex;justify-content:space-between;align-items:baseline">'
            f'<strong style="font-size:14px">{sector}</strong>'
            f'<span style="font-size:12px;color:#22c55e;font-weight:700">{rank_str} · {delta_str}</span>'
            f'</div>'
            f'<div style="margin-top:6px">{stocks_html or "<span style=\'font-size:11px;color:#94a3b8\'>No top-K-Score stocks available this week</span>"}</div>'
            f'</div>'
        )
        rows_text += f"  {sector}: rank {rank_str}, {delta_str} vs 4wks ago — {stocks_text or 'no candidates'}\n"

    body_html = f"""<html><body style="font-family:sans-serif;color:#1e293b;background:#f8fafc;padding:24px;margin:0">
  <div style="max-width:520px;margin:auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08)">
    <h2 style="margin-top:0;color:#22c55e">📈 Sector Rotation — Newly Emerging</h2>
    <p style="font-size:13px;color:#64748b;margin-top:-8px">{n} sector{'s' if n != 1 else ''} just moved into the top half of K-Score momentum among all sectors.</p>
    <div style="margin-top:12px">{rows_html}</div>
    <p style="font-size:11px;color:#94a3b8;margin-top:24px;border-top:1px solid #e2e8f0;padding-top:14px">
      Rank/delta are a MEASURED weekly K-Score comparison — a real, tracked tailwind for stocks
      in this sector, not a guarantee any specific one performs well. Cross-check the AI Signal
      and Confluence Score for any stock listed before acting. Not financial advice.
    </p>
  </div>
</body></html>"""
    body_text = (
        f"Sector Rotation — {n} sector{'s' if n != 1 else ''} newly emerging\n\n"
        + rows_text
        + "\nMeasured weekly K-Score trend, not a guarantee. Cross-check AI Signal/Confluence Score. Not financial advice.\n"
    )
    return send_email(to, subject, body_html, body_text)


def send_earnings_beat_screener_email(to: str, candidates: list[dict]) -> bool:
    """AUD-EARNINGS-BEAT-SCREENER: a market-wide, opportunity-finding scan — stocks with BOTH a
    real recent earnings beat AND improving analyst sentiment (recommendation_mean trending
    down/more-bullish over the trailing 8 weekly snapshots). Each dict: {symbol, name,
    report_date, surprise_pct, revenue_surprise_pct (optional), rec_mean_improvement}.

    Deliberately does NOT say "rising guidance" — no real forward-guidance/earnings-call-
    transcript data source exists anywhere in this app. rec_mean_improvement is a real,
    different, already-tracked proxy (analyst recommendation trending more bullish), reported
    as exactly that.
    """
    n = len(candidates)
    subject = f"🎯 Earnings Beat + Improving Sentiment — {n} stock{'s' if n != 1 else ''}"

    rows_html = ""
    rows_text = ""
    for c in candidates:
        sym = c["symbol"]
        surprise = c["surprise_pct"]
        rev_surprise = c.get("revenue_surprise_pct")
        rev_str = f", revenue +{rev_surprise:.1f}%" if rev_surprise is not None else ""
        rec_imp = c["rec_mean_improvement"]
        rows_html += (
            f'<div style="padding:10px 0;border-bottom:1px solid #f1f5f9">'
            f'<div style="display:flex;justify-content:space-between;align-items:baseline">'
            f'<strong style="font-size:14px">{sym}</strong>'
            f'<span style="font-size:12px;color:#22c55e;font-weight:700">EPS +{surprise:.1f}%</span>'
            f'</div>'
            f'<div style="font-size:12px;color:#64748b;margin-top:2px">{c.get("name", sym)}{rev_str} · reported {c["report_date"]}</div>'
            f'<div style="font-size:11px;color:#38bdf8;margin-top:2px">Analyst recommendation improved {rec_imp:.2f} pts (8-week trend)</div>'
            f'</div>'
        )
        rows_text += f"  {sym}: EPS beat +{surprise:.1f}%{rev_str}, reported {c['report_date']}, analyst rec improved {rec_imp:.2f} pts\n"

    body_html = f"""<html><body style="font-family:sans-serif;color:#1e293b;background:#f8fafc;padding:24px;margin:0">
  <div style="max-width:520px;margin:auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08)">
    <h2 style="margin-top:0;color:#22c55e">🎯 Earnings Beat + Improving Sentiment</h2>
    <p style="font-size:13px;color:#64748b;margin-top:-8px">{n} stock{'s' if n != 1 else ''} just beat earnings estimates AND have analysts turning more bullish over the past 8 weeks.</p>
    <div style="margin-top:12px">{rows_html}</div>
    <p style="font-size:11px;color:#94a3b8;margin-top:24px;border-top:1px solid #e2e8f0;padding-top:14px">
      Both signals are MEASURED, historical facts — a real reported EPS beat and a real trend
      in analyst recommendations — not a claim about future guidance (this app has no earnings-
      call-transcript data source). Cross-check the AI Signal and Confluence Score before
      acting. Not financial advice.
    </p>
  </div>
</body></html>"""
    body_text = (
        f"Earnings Beat + Improving Sentiment — {n} stock{'s' if n != 1 else ''}\n\n"
        + rows_text
        + "\nMeasured facts, not a guidance claim. Cross-check AI Signal/Confluence Score. Not financial advice.\n"
    )
    return send_email(to, subject, body_html, body_text)


def send_portfolio_drawdown_alert_email(to: str, breaches: list[dict]) -> bool:
    """T286-DRAWDOWN-ALERT: real, user-facing notification that a paper portfolio has crossed
    its own configured max_portfolio_drawdown_pct limit — the SAME condition the existing
    silent _write_gate_block()/UI gate-block badge already computes, just surfaced actively
    instead of only passively on the /paper-portfolio list page. Each dict: {portfolio_id,
    portfolio_name, current_dd_pct, limit_pct, equity}.

    Fires once per NEW breach (state-transition dedup in check_portfolio_drawdown_alerts()),
    not on every 1-minute check while still breached — this is a "this just started" alert,
    not a recurring nag.
    """
    n = len(breaches)
    subject = f"⚠️ Drawdown Limit Hit — {n} Portfolio{'s' if n != 1 else ''} Paused"

    rows_html = ""
    rows_text = ""
    for b in breaches:
        name = b["portfolio_name"]
        dd_pct = b["current_dd_pct"]
        limit_pct = b["limit_pct"]
        equity = b["equity"]
        rows_html += (
            f'<div style="padding:10px 0;border-bottom:1px solid #f1f5f9">'
            f'<div style="display:flex;justify-content:space-between;align-items:baseline">'
            f'<strong style="font-size:14px">{name}</strong>'
            f'<span style="font-size:12px;color:#ef4444;font-weight:700">-{dd_pct:.1f}% drawdown</span>'
            f'</div>'
            f'<div style="font-size:12px;color:#64748b;margin-top:2px">Limit: {limit_pct:.0f}% · Current equity: ${equity:,.2f}</div>'
            f'</div>'
        )
        rows_text += f"  {name}: -{dd_pct:.1f}% drawdown (limit {limit_pct:.0f}%), equity ${equity:,.2f}\n"

    body_html = f"""<html><body style="font-family:sans-serif;color:#1e293b;background:#f8fafc;padding:24px;margin:0">
  <div style="max-width:520px;margin:auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08)">
    <h2 style="margin-top:0;color:#ef4444">⚠️ Drawdown Limit Hit</h2>
    <p style="font-size:13px;color:#64748b;margin-top:-8px">
      {n} portfolio{'s have' if n != 1 else ' has'} dropped below its own configured
      max-drawdown limit. New entries are automatically paused for {'each' if n != 1 else 'this'}
      portfolio until equity recovers — no action is required to stop trading, this already
      happened.
    </p>
    <div style="margin-top:12px">{rows_html}</div>
    <p style="font-size:11px;color:#94a3b8;margin-top:24px;border-top:1px solid #e2e8f0;padding-top:14px">
      This is the same drawdown-from-peak-equity check that already blocks new entries
      silently — this email exists only to make sure you actually see it happened, not to
      change anything about how the portfolio behaves. Not financial advice.
    </p>
  </div>
</body></html>"""
    body_text = (
        f"Drawdown Limit Hit — {n} Portfolio{'s' if n != 1 else ''} Paused\n\n"
        + rows_text
        + "\nNew entries are already paused for the listed portfolio(s) until equity recovers. Not financial advice.\n"
    )
    return send_email(to, subject, body_html, body_text)


def send_conditional_order_email(to: str, order, fired_ok: bool, reason: str) -> bool:
    """T286-CONDITIONAL-ORDER: sent whenever a conditional order's trigger fires — regardless
    of whether the resulting action actually succeeded. A failure (e.g. the entry gate
    rejected a buy, insufficient cash) is just as important to surface as a success, since the
    user is relying on this order to act on their behalf without them watching."""
    action_label = {
        "buy": "BUY", "sell_partial": "Partial Sell", "sell_all": "Sell All",
        "tighten_stop": "Tighten Stop", "close_position": "Close Position",
        "alert_only": "Alert",
    }.get(order.action_type, order.action_type)
    status_word = "Fired" if fired_ok else "Failed"
    color = "#16a34a" if fired_ok else "#ef4444"
    subject = f"{'✅' if fired_ok else '⚠️'} Conditional Order {status_word} — {order.symbol} {action_label}"

    body_html = f"""<html><body style="font-family:sans-serif;color:#1e293b;background:#f8fafc;padding:24px;margin:0">
  <div style="max-width:520px;margin:auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08)">
    <h2 style="margin-top:0;color:{color}">{'✅' if fired_ok else '⚠️'} Conditional Order {status_word}</h2>
    <div style="padding:10px 0;border-bottom:1px solid #f1f5f9">
      <div style="display:flex;justify-content:space-between;align-items:baseline">
        <strong style="font-size:14px">{order.symbol}</strong>
        <span style="font-size:12px;color:{color};font-weight:700">{action_label}</span>
      </div>
      <div style="font-size:12px;color:#64748b;margin-top:6px">{reason}</div>
    </div>
    <p style="font-size:11px;color:#94a3b8;margin-top:24px;border-top:1px solid #e2e8f0;padding-top:14px">
      Conditional Order #{order.id}{f' — {order.note}' if order.note else ''}.
      This order has now completed its single-hop lifecycle and will not fire again — create a
      new one if you want another action. Not financial advice.
    </p>
  </div>
</body></html>"""
    body_text = (
        f"Conditional Order {status_word} — {order.symbol} {action_label}\n\n"
        f"{reason}\n\n"
        f"Conditional Order #{order.id}. This order has completed its single-hop lifecycle. Not financial advice.\n"
    )
    return send_email(to, subject, body_html, body_text)


def send_top3_conviction_email(to: str, picks: list[dict]) -> bool:
    """T257-TOP3-CONVICTION-ALERT: up to 3 picks, each gated on a MEASURED historical win
    rate (not raw model confidence) — the email's whole point is to make that accuracy claim
    concrete and auditable. Each pick dict: {symbol, horizon, direction ("BUY"/"SELL"),
    confidence, win_rate (0-1), count}. The printed win rate + sample size IS the accuracy
    claim — never assert a stronger one than what's actually measured.
    """
    n = len(picks)
    subject = f"🎯 Top {n} High-Conviction Pick{'s' if n != 1 else ''} — measured win rate ≥70%"

    rows_html = ""
    rows_text = ""
    for p in picks:
        sym, direction, horizon = p["symbol"], p["direction"], p["horizon"]
        wr_pct = p["win_rate"] * 100
        dir_color = "#22c55e" if direction == "BUY" else "#ef4444"
        rows_html += (
            f'<div style="padding:12px 0;border-bottom:1px solid #f1f5f9">'
            f'<div style="display:flex;justify-content:space-between;align-items:baseline">'
            f'<strong style="font-size:15px">{sym}</strong>'
            f'<span style="background:{dir_color}22;color:{dir_color};font-size:12px;font-weight:700;padding:2px 8px;border-radius:4px">{direction}</span>'
            f'</div>'
            f'<div style="font-size:12px;color:#64748b;margin-top:4px">{horizon} horizon · confidence {p["confidence"]:.0f}%</div>'
            f'<div style="font-size:13px;color:#4ade80;font-weight:700;margin-top:4px">{wr_pct:.0f}% measured win rate <span style="color:#94a3b8;font-weight:400;font-size:11px">(n={p["count"]} tracked outcomes)</span></div>'
            f'</div>'
        )
        rows_text += f"  {sym} ({direction}, {horizon}): {wr_pct:.0f}% measured win rate over {p['count']} tracked outcomes, confidence {p['confidence']:.0f}%\n"

    body_html = f"""<html><body style="font-family:sans-serif;color:#1e293b;background:#f8fafc;padding:24px;margin:0">
  <div style="max-width:480px;margin:auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08)">
    <h2 style="margin-top:0;color:#6366f1">🎯 Top {n} High-Conviction Pick{'s' if n != 1 else ''}</h2>
    <p style="font-size:13px;color:#64748b;margin-top:-8px">
      Ranked by measured historical win rate for this exact setup (horizon + direction +
      market + confidence band) — not raw model confidence.
    </p>
    <div style="margin-top:12px">{rows_html}</div>
    <p style="font-size:11px;color:#94a3b8;margin-top:24px;border-top:1px solid #e2e8f0;padding-top:14px">
      Win rate is measured from real tracked outcomes (signal_outcomes, last 180 days) for
      setups matching this exact horizon/direction/market/confidence-band combination — it is
      NOT a prediction that this specific trade will win. Most cycles qualify zero picks; an
      empty scan means the accuracy bar is working as intended. Not financial advice.
    </p>
  </div>
</body></html>"""
    body_text = (
        f"Top {n} High-Conviction Pick{'s' if n != 1 else ''} — measured win rate, not raw confidence\n\n"
        + rows_text
        + "\nWin rate is measured from real tracked outcomes for this exact setup class — not a "
        + "prediction of this specific trade. Not financial advice.\n"
    )
    return send_email(to, subject, body_html, body_text)


def send_value_area_breakdown_email(to: str, alerts: list[dict]) -> bool:
    """T252-VALUE-AREA-BREAKDOWN-ALERT: one email per recipient listing every symbol that
    closed outside its persisted value area (below VAL = breakdown, above VAH = breakout).
    Each alert dict: {symbol, price, poc, vah, val, note, kind ("breakdown"/"breakout"),
    as_of}. Reports the MEASURED close price vs. the persisted POC/VAH/VAL — never a "this
    WILL continue" prediction, matching this repo's established alert-honesty discipline.
    """
    n = len(alerts)
    subject = f"📐 Value Area Alert — {n} stock{'s' if n != 1 else ''} closed outside their value area"

    rows_html = ""
    rows_text = ""
    for a in alerts:
        sym = a["symbol"]
        kind = a["kind"]
        kind_color = "#ef4444" if kind == "breakdown" else "#22c55e"
        kind_label = "Breakdown" if kind == "breakdown" else "Breakout"
        rows_html += (
            f'<div style="padding:10px 0;border-bottom:1px solid #f1f5f9">'
            f'<div style="display:flex;justify-content:space-between;align-items:baseline">'
            f'<strong style="font-size:14px">{sym}</strong>'
            f'<span style="background:{kind_color}22;color:{kind_color};font-size:12px;font-weight:700;padding:2px 8px;border-radius:4px">{kind_label}</span>'
            f'</div>'
            f'<div style="font-size:12px;color:#64748b;margin-top:2px">${a["price"]:.2f} — {a["note"]}</div>'
            f'<div style="font-size:11px;color:#94a3b8;margin-top:2px">POC ${a["poc"]:.2f} · VAH ${a["vah"]:.2f} · VAL ${a["val"]:.2f} (as of {a["as_of"]})</div>'
            f'</div>'
        )
        rows_text += f"  {sym} ({kind_label}): ${a['price']:.2f} — {a['note']} [POC ${a['poc']:.2f}, VAH ${a['vah']:.2f}, VAL ${a['val']:.2f}, as of {a['as_of']}]\n"

    body_html = f"""<html><body style="font-family:sans-serif;color:#1e293b;background:#f8fafc;padding:24px;margin:0">
  <div style="max-width:480px;margin:auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08)">
    <h2 style="margin-top:0;color:#0ea5e9">📐 Value Area Alert</h2>
    <p style="font-size:13px;color:#64748b;margin-top:-8px">{n} stock{'s' if n != 1 else ''} closed outside their value area (POC/VAH/VAL, 60-day profile).</p>
    <div style="margin-top:12px">{rows_html}</div>
    <p style="font-size:11px;color:#94a3b8;margin-top:24px;border-top:1px solid #e2e8f0;padding-top:14px">
      A breakdown/breakout is a measured close relative to the profiled value area, not a
      prediction of what happens next — a close back inside the value area soon after can be
      a false breakout/breakdown. Not financial advice.
    </p>
  </div>
</body></html>"""
    body_text = (
        f"Value Area Alert — {n} stock{'s' if n != 1 else ''}\n\n"
        + rows_text
        + "\nA measured close relative to the profiled value area, not a prediction. Not financial advice.\n"
    )
    return send_email(to, subject, body_html, body_text)


def send_earnings_reminder_digest_email(to: str, rows: list[dict]) -> bool:
    """T230-ALERTING-EARNINGS-PROXIMITY consolidation: one email per recipient listing every
    upcoming earnings print this cycle, as a table, instead of a separate email per symbol
    (previously check_signal_alerts() sent one send_email() call per (user, symbol) pair —
    a user watching 8 stocks reporting in the same week got 8 separate emails).

    Each row dict: {symbol, days_to_earnings, price (optional), change_pct (optional),
    forward_eps (optional), eps_beat_rate (optional, 0-1), eps_avg_surprise_pct (optional),
    kscore (optional)}. Rows are already deduped/capped by the caller (same per-(user, symbol,
    days_to_earnings) Redis dedup key as before this consolidation — the dedup granularity is
    unchanged, only the delivery is batched).
    """
    n = len(rows)
    subject = f"⏰ Earnings This Week — {n} stock{'s' if n != 1 else ''} reporting soon"

    def _fmt_price(r: dict) -> str:
        price = r.get("price")
        if price is None:
            return "—"
        chg = r.get("change_pct")
        chg_str = ""
        if chg is not None:
            color = "#22c55e" if chg >= 0 else "#ef4444"
            chg_str = f' <span style="color:{color}">({chg:+.1f}%)</span>'
        return f"${price:.2f}{chg_str}"

    def _fmt_beat_rate(r: dict) -> str:
        beat_rate = r.get("eps_beat_rate")
        if beat_rate is None:
            return "—"
        beats = round(beat_rate * 8)
        surprise = r.get("eps_avg_surprise_pct")
        surprise_str = f", avg {surprise:+.1f}%" if surprise is not None else ""
        return f"{beats}/8{surprise_str}"

    def _fmt_kscore(r: dict) -> str:
        ks = r.get("kscore")
        return f"{ks:.0f}" if ks is not None else "—"

    rows_sorted = sorted(rows, key=lambda r: r.get("days_to_earnings", 999))

    def _fmt_dte(r: dict) -> str:
        dte = r.get("days_to_earnings")
        if dte is None:
            return "—"
        return "Today" if dte == 0 else f"{dte}d"

    table_rows_html = ""
    for r in rows_sorted:
        sym = r["symbol"]
        dte_str = _fmt_dte(r)
        eps_est = r.get("forward_eps")
        eps_str = f"${eps_est:.2f}" if eps_est is not None else "—"
        table_rows_html += (
            f'<tr style="border-bottom:1px solid #f1f5f9">'
            f'<td style="padding:8px 6px;font-weight:700">{sym}</td>'
            f'<td style="padding:8px 6px;color:#f59e0b;font-weight:600">{dte_str}</td>'
            f'<td style="padding:8px 6px">{_fmt_price(r)}</td>'
            f'<td style="padding:8px 6px;color:#64748b">{eps_str}</td>'
            f'<td style="padding:8px 6px;color:#64748b;font-size:12px">{_fmt_beat_rate(r)}</td>'
            f'<td style="padding:8px 6px;color:#64748b">{_fmt_kscore(r)}</td>'
            f'</tr>'
        )

    body_html = f"""<html><body style="font-family:sans-serif;color:#1e293b;background:#f8fafc;padding:24px;margin:0">
  <div style="max-width:640px;margin:auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08)">
    <h2 style="margin-top:0;color:#f59e0b">⏰ Earnings This Week</h2>
    <p style="font-size:13px;color:#64748b;margin-top:-8px">{n} stock{'s' if n != 1 else ''} on your watchlist reporting soon. Review your position and manage risk before each print.</p>
    <table style="width:100%;border-collapse:collapse;margin-top:12px;font-size:13px">
      <thead>
        <tr style="border-bottom:2px solid #e2e8f0;text-align:left;color:#94a3b8;font-size:11px;text-transform:uppercase">
          <th style="padding:6px">Symbol</th>
          <th style="padding:6px">Reports</th>
          <th style="padding:6px">Price</th>
          <th style="padding:6px">Est. EPS</th>
          <th style="padding:6px">Beat Rate</th>
          <th style="padding:6px">K-Score</th>
        </tr>
      </thead>
      <tbody>{table_rows_html}</tbody>
    </table>
    <p style="font-size:11px;color:#94a3b8;margin-top:24px;border-top:1px solid #e2e8f0;padding-top:14px">
      Beat rate is measured over the last 8 reported quarters. Not financial advice.
    </p>
  </div>
</body></html>"""

    def _fmt_dte_text(r: dict) -> str:
        dte = r.get("days_to_earnings")
        if dte is None:
            return "in ?d"
        return "TODAY" if dte == 0 else f"in {dte}d"

    text_rows = "\n".join(
        f"  {r['symbol']}: reports {_fmt_dte_text(r)}, "
        f"price {r.get('price', '—')}, est EPS {r.get('forward_eps', '—')}, "
        f"beat rate {_fmt_beat_rate(r)}, K-Score {_fmt_kscore(r)}"
        for r in rows_sorted
    )
    body_text = (
        f"Earnings This Week — {n} stock{'s' if n != 1 else ''}\n\n"
        + text_rows
        + "\n\nReview your position and manage risk before each print. Not financial advice.\n"
    )
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
    market_hours_open: bool = True,
) -> bool:
    """Send a paper trade exit email — fired whenever the paper trading engine closes a position.

    PT-MONITOR-NO-MARKET-HOURS-GATE: _monitor_positions() deliberately has no market-hours
    gate (a genuinely breached stop should still close promptly outside regular hours, not sit
    unprotected until the next open) — but that means this email can arrive hours into the
    overnight, computed from a stale, already-final end-of-day close, worded identically to a
    live intraday trigger. `market_hours_open=False` adds an explicit note so the email is
    honest about WHEN this reflects rather than implying something just happened live.
    """
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

    after_hours_note = (
        "Note: the market was CLOSED at the moment this exit was evaluated — the exit price "
        "above reflects the prior session's regular-close price, not a live intraday move. "
        "The position was still correctly closed since a genuinely breached stop shouldn't sit "
        "unprotected until the next open."
    )
    after_hours_html = ""
    if not market_hours_open:
        after_hours_html = (
            f'<div style="margin-top:12px;padding:12px;background:#fffbeb;border-radius:8px;'
            f'font-size:12px;color:#92400e;border:1px solid #fde68a">⏰ {after_hours_note}</div>'
        )

    subject = f"[Paper Trade] {label} — {symbol} ({pnl_pct_f})"
    body_text = (
        f"{label}: {symbol}\n"
        f"P&L: {pnl_dollar_f} ({pnl_pct_f}) over {hold_days} day(s)\n"
        f"Entry: ${entry_price:.4f}  Exit: ${exit_price:.4f}\n"
        f"Signal at exit: {signal_at_exit or '—'}\n"
        f"Reason: {reason_note}"
        + (f"\n\n{after_hours_note}" if not market_hours_open else "")
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
    {after_hours_html}
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


# T241-DIGEST5X: 5 post-open checks/day — 30min, then hourly through 4hr30min.
# Keys must match the window names scheduler.py's _POST_OPEN_WINDOWS registers jobs with.
_WINDOW_LABELS = {
    "30min": "30 min after open",
    "1hr30min": "1.5 hours after open",
    "2hr30min": "2.5 hours after open",
    "3hr30min": "3.5 hours after open",
    "4hr30min": "4.5 hours after open",
}
# What each window's "since ___" comparison point actually is, for the digest header.
_WINDOW_SINCE_LABELS = {
    "30min": "open",
    "1hr30min": "30 min ago",
    "2hr30min": "1.5 hours ago",
    "3hr30min": "2.5 hours ago",
    "4hr30min": "3.5 hours ago",
}


def send_post_open_digest_email(
    to: str,
    market: str,
    window: str,  # one of _WINDOW_LABELS' keys
    regime_changed: bool,
    prev_state: str | None,
    cur_state: str,
    cur_vix: float | None,
    positions: list,           # [{symbol, pnl_pct, current_price, current_stop, signal_now, signal_flipped, signal_prev}]
    new_signal_changes: list,  # [{symbol, signal, prev_signal}]
    top_movers: list,          # [{symbol, change_pct}]
    bottom_movers: list,       # [{symbol, change_pct}]
    vol_surge: list | None = None,  # [{symbol, volume_z (RVOL), current_price, change_pct}]
    vol_dryup: list | None = None,  # [{symbol, volume_z (RVOL), current_price, change_pct}] — RVOL <= 0.5
) -> bool:
    """Post-open market update — 30 min or 1 hour after {market} opens.

    Only sent when something changed (see send_post_open_digest's has_content check).
    The 1hr email is delta-only vs. the 30min email's snapshot — it will not repeat
    unchanged positions/signals already reported in the 30min email.
    """
    from datetime import date as _date
    date_str = _date.today().strftime("%b %d, %Y")
    window_label = _WINDOW_LABELS.get(window, window)

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

    # ── Volume surge/dry-up — stocks trading meaningfully above/below normal volume ──
    # MD-RVOL1: value is now RVOL (today_volume / avg_volume, same metric/scope as the
    # screener's RVOL column and stock detail page's RVOL chip) rather than a volume_z
    # z-score — rendered as "×" to match those pages' own display convention (e.g. "2.3×"),
    # not "σ", so a value seen here reads identically to the same stock's RVOL elsewhere.
    # T241-DIGEST5X: a volume surge on rising price (accumulation/breakout) and one on
    # falling price (distribution/panic selling) call for very different reactions — the
    # bare RVOL ratio alone couldn't distinguish them, so price + %change + a directional
    # note are now shown alongside it. Shared by both the surge and dry-up sections below.
    def _vol_direction(change_pct: float | None) -> tuple[str, str]:
        if change_pct is None:
            return "#64748b", ""
        if change_pct >= 0.5:
            return "#22c55e", "accumulation"
        if change_pct <= -0.5:
            return "#ef4444", "distribution"
        return "#64748b", "flat"

    # MD-VOLDIRECTION1: split into buying vs. selling instead of one mixed table with a
    # small inline tag — heavy selling volume (surge + falling price) is the case a user
    # most needs to notice quickly, and burying it as one row among rising-price rows made
    # it easy to miss. A stock with change_pct exactly in the -0.5%..+0.5% "flat" band is
    # surging on volume without a clear directional lean yet — shown in the Buying table
    # (arbitrary but consistent tie-break) so no surging stock is silently dropped from
    # either sub-section.
    def _vol_row(v: dict) -> str:
        rvol = v["volume_z"]
        intensity = "#ef4444" if rvol >= 3.0 else "#f97316" if rvol >= 2.0 else "#f59e0b"
        price = v.get("current_price")
        change_pct = v.get("change_pct")
        price_str = f"${price:,.2f}" if price is not None else "—"
        change_color, direction = _vol_direction(change_pct)
        change_str = f"{change_pct:+.1f}%" if change_pct is not None else "—"
        direction_str = f" ({direction})" if direction else ""
        return (
            f'<tr style="border-bottom:1px solid #f1f5f9">'
            f'<td style="padding:6px 10px;font-weight:700;font-size:13px">{v["symbol"]}</td>'
            f'<td style="padding:6px 10px;font-size:13px;font-weight:700;color:{intensity}">{rvol:.1f}×</td>'
            f'<td style="padding:6px 10px;font-size:13px;color:#374151">{price_str}</td>'
            f'<td style="padding:6px 10px;font-size:13px;font-weight:700;color:{change_color}">{change_str}{direction_str}</td>'
            f'</tr>'
        )

    def _vol_text_row_generic(v: dict) -> str:
        price = v.get("current_price")
        change_pct = v.get("change_pct")
        price_str = f"${price:,.2f}" if price is not None else "—"
        _, direction = _vol_direction(change_pct)
        change_str = f"{change_pct:+.1f}%" if change_pct is not None else "—"
        direction_str = f" ({direction})" if direction else ""
        return f"  {v['symbol']:8}  {v['volume_z']:.1f}x avg volume   {price_str:>10}  {change_str}{direction_str}\n"

    vol_surge_buying = [v for v in (vol_surge or []) if (v.get("change_pct") or 0) > -0.5]
    vol_surge_selling = [v for v in (vol_surge or []) if (v.get("change_pct") or 0) <= -0.5]

    vol_surge_html = ""
    vol_surge_text = ""
    if vol_surge_selling:
        selling_rows_html = "".join(_vol_row(v) for v in vol_surge_selling)
        vol_surge_html += f"""
    <div style="margin-top:20px">
      <div style="font-size:11px;font-weight:700;color:#ef4444;text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px">Volume Surge — Selling (RVOL vs. 20d avg)</div>
      <table style="width:100%;border-collapse:collapse;background:#fef2f2;border-radius:8px;overflow:hidden;border:1px solid #fecaca">{selling_rows_html}</table>
    </div>"""
        vol_surge_text += "\nVOLUME SURGE — SELLING (RVOL):\n" + "".join(_vol_text_row_generic(v) for v in vol_surge_selling)
    if vol_surge_buying:
        buying_rows_html = "".join(_vol_row(v) for v in vol_surge_buying)
        vol_surge_html += f"""
    <div style="margin-top:20px">
      <div style="font-size:11px;font-weight:700;color:#f59e0b;text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px">Volume Surge — Buying (RVOL vs. 20d avg)</div>
      <table style="width:100%;border-collapse:collapse;background:#fafafa;border-radius:8px;overflow:hidden;border:1px solid #e2e8f0">{buying_rows_html}</table>
    </div>"""
        vol_surge_text += "\nVOLUME SURGE — BUYING (RVOL):\n" + "".join(_vol_text_row_generic(v) for v in vol_surge_buying)

    # MD-VOLDRYUP1: mirror-image case — RVOL <= 0.5, trading meaningfully BELOW normal
    # volume today. A sudden dry-up can mean conviction has evaporated, often precedes a
    # breakout once volume returns, or just flags a stock coasting on no news — reported
    # in its own section rather than mixed into the surge table above, since "loud" and
    # "quiet" call for different reactions.
    vol_dryup_html = ""
    vol_dryup_text = ""
    if vol_dryup:
        def _dryup_row(v: dict) -> str:
            rvol = v["volume_z"]
            intensity = "#94a3b8" if rvol <= 0.2 else "#64748b" if rvol <= 0.35 else "#94a3b8"
            price = v.get("current_price")
            change_pct = v.get("change_pct")
            price_str = f"${price:,.2f}" if price is not None else "—"
            change_color, direction = _vol_direction(change_pct)
            change_str = f"{change_pct:+.1f}%" if change_pct is not None else "—"
            direction_str = f" ({direction})" if direction else ""
            return (
                f'<tr style="border-bottom:1px solid #f1f5f9">'
                f'<td style="padding:6px 10px;font-weight:700;font-size:13px">{v["symbol"]}</td>'
                f'<td style="padding:6px 10px;font-size:13px;font-weight:700;color:{intensity}">{rvol:.1f}×</td>'
                f'<td style="padding:6px 10px;font-size:13px;color:#374151">{price_str}</td>'
                f'<td style="padding:6px 10px;font-size:13px;font-weight:700;color:{change_color}">{change_str}{direction_str}</td>'
                f'</tr>'
            )
        dryup_rows_html = "".join(_dryup_row(v) for v in vol_dryup)
        vol_dryup_html = f"""
    <div style="margin-top:20px">
      <div style="font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px">Volume Dry-Up (RVOL vs. 20d avg)</div>
      <table style="width:100%;border-collapse:collapse;background:#fafafa;border-radius:8px;overflow:hidden;border:1px solid #e2e8f0">{dryup_rows_html}</table>
    </div>"""

        def _dryup_text_row(v: dict) -> str:
            price = v.get("current_price")
            change_pct = v.get("change_pct")
            price_str = f"${price:,.2f}" if price is not None else "—"
            _, direction = _vol_direction(change_pct)
            change_str = f"{change_pct:+.1f}%" if change_pct is not None else "—"
            direction_str = f" ({direction})" if direction else ""
            return f"  {v['symbol']:8}  {v['volume_z']:.1f}x avg volume   {price_str:>10}  {change_str}{direction_str}\n"

        vol_dryup_text = "\nVOLUME DRY-UP (RVOL):\n" + "".join(_dryup_text_row(v) for v in vol_dryup)

    subject_bits = []
    if regime_changed:
        subject_bits.append(f"Regime→{_state_label.get(cur_state, cur_state.upper())}")
    if any(p.get("signal_flipped") for p in positions):
        subject_bits.append("Signal flip")
    if new_signal_changes:
        subject_bits.append(f"{len(new_signal_changes)} new signal(s)")
    if vol_surge_selling:
        subject_bits.append(f"{len(vol_surge_selling)} selling on volume")
    if vol_surge_buying:
        subject_bits.append(f"{len(vol_surge_buying)} buying on volume")
    if vol_dryup:
        subject_bits.append(f"{len(vol_dryup)} volume dry-up")
    subject_detail = " · ".join(subject_bits) if subject_bits else "Update"
    subject = f"📈 {market} {window_label}: {subject_detail} — {date_str}"

    body_text = (
        f"{market} Post-Open Update — {window_label} — {date_str}\n"
        f"{regime_text}"
        f"{pos_section_text}"
        f"{sig_section_text}"
        f"{vol_surge_text}"
        f"{vol_dryup_text}"
        f"{movers_text}"
    )
    body_html = f"""<!DOCTYPE html><html><body style="font-family:sans-serif;background:#f8fafc;padding:24px;margin:0">
  <div style="max-width:560px;margin:auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08)">
    <div style="margin-bottom:20px">
      <div style="font-size:12px;color:#94a3b8;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px">{market} Post-Open Update · {window_label} · {date_str}</div>
      <div style="font-size:20px;font-weight:700;color:#111827">What changed since {_WINDOW_SINCE_LABELS.get(window, "the last check")}</div>
    </div>
    {regime_html}
    {pos_section_html}
    {sig_section_html}
    {vol_surge_html}
    {vol_dryup_html}
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


def send_data_quality_alert_email(to: str, failing_checks: list) -> bool:
    """Alert email for data-quality staleness checks that have failed.

    failing_checks: [{"name": str, "description": str, "last_updated": str|None,
                       "age_hours": float|None, "max_age_hours": float|None,
                       "detail": str (optional)}]

    AUD266-TWO-GATES-CONTRADICTORY-BARS: max_age_hours is None for "ratio"-sourced checks
    (run_data_quality_checks()'s conviction_fired_ratio entry) — those have no age/staleness
    concept at all, only a numerator/denominator ratio, carried in the optional `detail`
    string instead. Both age_str and the max-age column must degrade gracefully to that
    detail string rather than crashing on `None:.0f` formatting.
    """
    from datetime import date as _date
    date_str = _date.today().strftime("%b %d, %Y")

    rows_html = ""
    rows_text = ""
    for c in failing_checks:
        detail = c.get("detail")
        age_str = (
            detail if detail is not None
            else f"{c['age_hours']:.1f}h ago" if c.get("age_hours") is not None
            else "never"
        )
        max_age_str = f"max {c['max_age_hours']:.0f}h" if c.get("max_age_hours") is not None else "—"
        max_age_text = f"(max allowed: {c['max_age_hours']:.0f}h)" if c.get("max_age_hours") is not None else ""
        rows_html += (
            f'<tr style="border-bottom:1px solid #f1f5f9">'
            f'<td style="padding:8px 10px;font-weight:700;font-size:13px">{c["name"]}</td>'
            f'<td style="padding:8px 10px;font-size:12px;color:#64748b">{c["description"]}</td>'
            f'<td style="padding:8px 10px;font-size:13px;font-weight:700;color:#ef4444">{age_str}</td>'
            f'<td style="padding:8px 10px;font-size:12px;color:#94a3b8">{max_age_str}</td>'
            f'</tr>'
        )
        rows_text += f"  {c['name']:30}  {age_str}  {max_age_text}\n"

    subject = f"⚠ Data Quality Alert: {len(failing_checks)} check(s) failing — {date_str}"
    body_text = (
        f"Data Quality Alert — {date_str}\n"
        f"{len(failing_checks)} check(s) exceeded their staleness threshold:\n\n"
        f"{rows_text}\n"
        f"View details: https://lausing.com/admin-health"
    )
    body_html = f"""<!DOCTYPE html><html><body style="font-family:sans-serif;background:#f8fafc;padding:24px;margin:0">
  <div style="max-width:600px;margin:auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08)">
    <div style="margin-bottom:20px">
      <div style="font-size:12px;color:#94a3b8;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px">Data Quality Alert · {date_str}</div>
      <div style="font-size:20px;font-weight:700;color:#ef4444">{len(failing_checks)} check(s) failing</div>
    </div>
    <table style="width:100%;border-collapse:collapse;background:#fafafa;border-radius:8px;overflow:hidden;border:1px solid #e2e8f0">
      <tr style="background:#f1f5f9">
        <th style="padding:6px 10px;font-size:11px;color:#475569;text-align:left">Check</th>
        <th style="padding:6px 10px;font-size:11px;color:#475569;text-align:left">Description</th>
        <th style="padding:6px 10px;font-size:11px;color:#475569;text-align:left">Last Updated</th>
        <th style="padding:6px 10px;font-size:11px;color:#475569;text-align:left">Threshold</th>
      </tr>
      {rows_html}
    </table>
    <p style="font-size:12px;color:#94a3b8;margin-top:24px;border-top:1px solid #e2e8f0;padding-top:12px">
      <a href="https://lausing.com/admin-health" style="color:#6366f1">View System Health →</a>
    </p>
  </div>
</body></html>"""
    return send_email(to, subject, body_html, body_text)


def send_theme_forecast_email(to: str, date_str: str, themes: list[dict]) -> bool:
    """T270-SECTOR-THEME-FORECAST-EMAIL: weekly "themes with real supporting signals this week"
    digest — see services/market-data/src/services/theme_signals.py's own module docstring for
    the full honesty-framing rationale (this is NOT a forecast of what a theme will do next; it
    reports already-measured momentum, K-Score, and BUY/SELL signal breadth, with an LLM
    explaining those numbers in prose — the LLM is never asked to predict).

    `themes` items are dicts: {"theme": str, "avg_return_5d_pct": float|None,
    "avg_kscore": float|None, "buy_signal_count": int, "sell_signal_count": int,
    "symbol_count": int, "top_symbols": list[dict], "summary_text": str|None} — the exact
    shape compute_theme_signal()'s ThemeSignalResult plus an optional LLM summary produces.
    Sorted by the caller (most-positive avg_return_5d_pct first) before reaching this builder —
    this function only renders, it does not rank.
    """
    subject = f"📈 Weekly Theme Signals — {date_str}"

    rows_html = ""
    rows_text = ""
    for t in themes:
        ret = t.get("avg_return_5d_pct")
        ret_color = "#16a34a" if (ret or 0) >= 0 else "#dc2626"
        ret_str = f"{ret:+.2f}%" if ret is not None else "—"
        kscore = t.get("avg_kscore")
        kscore_str = f"{kscore:.0f}" if kscore is not None else "—"
        buy_n = t.get("buy_signal_count", 0)
        sell_n = t.get("sell_signal_count", 0)
        n = t.get("symbol_count", 0)
        summary = t.get("summary_text")
        summary_html = (
            f'<div style="font-size:12px;color:#64748b;margin-top:6px;line-height:1.5">{summary}</div>'
            if summary else
            '<div style="font-size:11px;color:#94a3b8;margin-top:6px">No AI summary available this week — numbers above are still real, measured data.</div>'
        )
        top_syms = ", ".join(
            f"{s['symbol']} ({s['return_5d_pct']:+.1f}%)" if s.get("return_5d_pct") is not None else s["symbol"]
            for s in (t.get("top_symbols") or [])[:3]
        )
        rows_html += (
            f'<div style="padding:12px 0;border-bottom:1px solid #f1f5f9">'
            f'<div style="display:flex;justify-content:space-between;align-items:baseline">'
            f'<strong style="font-size:14px">{t.get("theme","")}</strong>'
            f'<span style="font-size:13px;color:{ret_color};font-weight:700">{ret_str}</span>'
            f'</div>'
            f'<div style="font-size:11px;color:#94a3b8;margin-top:2px">'
            f'{n} stocks tracked · avg K-Score {kscore_str} · {buy_n} BUY / {sell_n} SELL signal(s)'
            f'</div>'
            + (f'<div style="font-size:11px;color:#64748b;margin-top:2px">Top: {top_syms}</div>' if top_syms else "")
            + summary_html
            + f'</div>'
        )
        rows_text += (
            f"  {t.get('theme','')}: {ret_str} avg 5d return, avg K-Score {kscore_str}, "
            f"{buy_n} BUY / {sell_n} SELL signal(s)\n"
        )
        if summary:
            rows_text += f"    {summary}\n"

    body_html = f"""<!DOCTYPE html><html><body style="font-family:sans-serif;background:#f8fafc;padding:24px;margin:0">
  <div style="max-width:560px;margin:auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08)">
    <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:16px">
      <h2 style="margin:0;font-size:18px;color:#0f172a">📈 Weekly Theme Signals</h2>
      <span style="font-size:13px;color:#94a3b8">{date_str}</span>
    </div>
    <div>{rows_html or '<div style="font-size:12px;color:#94a3b8">No theme data available this week.</div>'}</div>
    <p style="font-size:11px;color:#94a3b8;margin-top:24px;border-top:1px solid #e2e8f0;padding-top:14px">
      These are already-measured signals as of this week — 5-day price return, K-Score, and
      current BUY/SELL signal counts for each theme's hand-picked representative stocks. This is
      NOT a prediction of what any theme will do next — it reports what has already happened
      this week, and the AI summary (where shown) only explains these real numbers, never
      forecasts beyond them. Themes and their representative symbols are hand-curated, not
      auto-detected. Not financial advice.
    </p>
  </div>
</body></html>"""

    body_text = (
        f"StockAI Weekly Theme Signals — {date_str}\n\n"
        + (rows_text or "  No theme data available this week.\n")
        + "\nAlready-measured signals as of this week, not a prediction of what any theme will do"
        " next. Themes are hand-curated, not auto-detected. Not financial advice.\n"
    )
    return send_email(to, subject, body_html, body_text)


def send_trade_coach_email(to: str, date_str: str, result: dict) -> bool:
    """T286-TRADE-PATTERN-COACH: weekly cross-trade behavioral-pattern digest — see
    services/market-data/src/services/trade_coach.py's own module docstring for the full
    honesty-framing rationale (this reports MEASURED patterns across the account's own closed
    trades, e.g. giveback vs. peak price on winners, hold-days vs. each style's expected window
    — it never tells the user what to do differently).

    `result` is the exact dict shape TradePatternResult produces (dataclasses.asdict), plus an
    optional "summary_text" key for the LLM prose (None if unavailable). This function only
    renders — sorting/ranking happens upstream if needed (here, there's nothing to rank, since
    this is a single account-wide aggregate, not a per-item list).
    """
    subject = f"🧭 Weekly Trade Pattern Review — {date_str}"

    n_trades = result.get("n_trades", 0)
    window_days = result.get("window_days", 90)
    win_rate = result.get("win_rate")
    win_rate_str = f"{win_rate*100:.0f}%" if win_rate is not None else "—"
    avg_return = result.get("avg_return_pct")
    avg_return_str = f"{avg_return:+.2f}%" if avg_return is not None else "—"
    giveback = result.get("avg_giveback_pct_on_winners")
    giveback_str = f"{giveback:.1f}%" if giveback is not None else "—"
    hold_delta = result.get("avg_hold_days_vs_expected")
    hold_delta_str = (
        f"{hold_delta:+.1f} days vs. expected" if hold_delta is not None else "—"
    )
    summary = result.get("summary_text")

    reason_rows_html = ""
    reason_rows_text = ""
    for r in (result.get("by_exit_reason") or []):
        wr = r.get("win_rate")
        wr_str = f"{wr*100:.0f}%" if wr is not None else "—"
        ret = r.get("avg_return_pct")
        ret_str = f"{ret:+.2f}%" if ret is not None else "—"
        pnl = r.get("total_pnl", 0.0)
        pnl_color = "#16a34a" if pnl >= 0 else "#dc2626"
        reason_rows_html += (
            f'<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #f8fafc;font-size:12px">'
            f'<span>{r.get("exit_reason","")} <span style="color:#94a3b8">({r.get("count",0)})</span></span>'
            f'<span>win {wr_str} · avg {ret_str} · <span style="color:{pnl_color};font-weight:700">${pnl:,.2f}</span></span>'
            f'</div>'
        )
        reason_rows_text += f"  {r.get('exit_reason','')} ({r.get('count',0)}): win rate {wr_str}, avg return {ret_str}, total pnl ${pnl:,.2f}\n"

    summary_html = (
        f'<div style="font-size:13px;color:#1e293b;margin-top:14px;line-height:1.6;background:#f8fafc;border-radius:8px;padding:12px">{summary}</div>'
        if summary else
        '<div style="font-size:11px;color:#94a3b8;margin-top:14px">No AI summary available this week — numbers above are still real, measured data.</div>'
    )

    body_html = f"""<!DOCTYPE html><html><body style="font-family:sans-serif;background:#f8fafc;padding:24px;margin:0">
  <div style="max-width:560px;margin:auto;background:#fff;border-radius:12px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08)">
    <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:16px">
      <h2 style="margin:0;font-size:18px;color:#0f172a">🧭 Weekly Trade Pattern Review</h2>
      <span style="font-size:13px;color:#94a3b8">{date_str}</span>
    </div>
    <div style="font-size:12px;color:#64748b;margin-bottom:12px">
      Last {window_days} days · {n_trades} closed trades · win rate {win_rate_str} · avg return {avg_return_str}
    </div>
    <div style="display:flex;gap:16px;margin-bottom:12px;font-size:12px;color:#334155">
      <div>Avg giveback on winners: <strong>{giveback_str}</strong></div>
      <div>Avg hold vs. expected: <strong>{hold_delta_str}</strong></div>
    </div>
    <div>{reason_rows_html or '<div style="font-size:12px;color:#94a3b8">No exit-reason data available.</div>'}</div>
    {summary_html}
    <p style="font-size:11px;color:#94a3b8;margin-top:24px;border-top:1px solid #e2e8f0;padding-top:14px">
      These are already-measured statistics over this account's own closed paper trades — not
      a prediction of future performance, and not prescriptive advice about what to change. The
      AI summary (where shown) only describes what these real numbers already show. Not
      financial advice.
    </p>
  </div>
</body></html>"""

    body_text = (
        f"Weekly Trade Pattern Review — {date_str}\n\n"
        f"Last {window_days} days · {n_trades} closed trades · win rate {win_rate_str} · avg return {avg_return_str}\n"
        f"Avg giveback on winners: {giveback_str} · Avg hold vs. expected: {hold_delta_str}\n\n"
        + (reason_rows_text or "  No exit-reason data available.\n")
        + (f"\n{summary}\n" if summary else "")
        + "\nAlready-measured statistics, not a prediction or prescriptive advice. Not financial advice.\n"
    )
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
