"""T405-FEDWATCH — market-implied probability of a Fed hike/cut at each upcoming FOMC meeting.

WHY. FOMC already existed in this app only as a BLACKOUT GATE — "don't open a position near a
meeting" (paper_trading_engine, hard_rejects). That says a meeting is risky without saying what
the market thinks will happen at it. This computes the second part.

WHERE THE NUMBERS COME FROM. 30-Day Fed Funds futures (CBOT "ZQ"), the same instrument CME's
own FedWatch uses. A ZQ contract settles to `100 - (average daily effective fed funds rate over
the contract month)`, so its price IS a market-implied forecast of the average rate that month.
This is a market expectation, not a forecast by this app and not a Fed communication.

THE ARITHMETIC, which is the whole tool. An FOMC decision takes effect the day after the
meeting, so a month containing a meeting on day D of N days averages the OLD rate for D days and
the NEW rate for the remaining N-D:

    implied_average = (D/N) * rate_before + ((N-D)/N) * rate_after
    =>  rate_after = (N * implied_average - D * rate_before) / (N - D)

The expected change is `rate_after - rate_before`. Because the Fed moves in 25bp increments, a
change of e.g. -13bp is not a forecast of a 13bp cut — it is the market pricing roughly a 52%
chance of a 25bp cut and 48% of no move. That interpretation is the standard one and is what
the probabilities below express.

FOUR HONEST LIMITS, stated on the endpoint and the page as well as here:
  1. The `(N-D)` denominator explodes for a meeting near month-end — one day of new rate has to
     carry the whole inference. Meetings with < 5 remaining days are flagged `low_precision`.
  2. ZQ settles on the EFFECTIVE rate, which trades a few bp inside the target range. So the
     derived level is an effective-rate expectation, not the target band itself.
  3. Futures carry a small risk/term premium, so implied probabilities are biased slightly
     toward the direction of carry. CME's published FedWatch makes the same simplification.
  4. This is a probability distribution, not a prediction. A 70% chance of a cut means cuts do
     NOT happen 30% of the time, and the market is often wrong well beyond that.
"""
from __future__ import annotations

import calendar
from datetime import date

# CBOT month codes — ZQ + code + 2-digit year, e.g. December 2026 = "ZQZ26.CBT".
_MONTH_CODE = {1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M", 7: "N", 8: "Q",
               9: "U", 10: "V", 11: "X", 12: "Z"}

_INCREMENT = 0.25          # the Fed moves in 25bp steps
_LOW_PRECISION_DAYS = 5    # see limit (1) above


def contract_symbol(year: int, month: int) -> str:
    """yfinance symbol for the 30-Day Fed Funds future settling in this month."""
    return f"ZQ{_MONTH_CODE[month]}{year % 100}.CBT"


def implied_rate(price: float) -> float:
    """ZQ quotes as 100 - rate, so the implied average rate is simply 100 - price."""
    return round(100.0 - float(price), 4)


def rate_after_meeting(*, implied_avg: float, rate_before: float,
                       meeting_day: int, days_in_month: int) -> float | None:
    """Solve the month-average decomposition for the post-meeting rate.

    Returns None when the meeting falls on the final day of the month: there are then zero days
    of the new rate inside this contract, so the month's average says nothing about it. That is
    a genuine absence of information, not a number to approximate.
    """
    remaining = days_in_month - meeting_day
    if remaining <= 0:
        return None
    return round((days_in_month * implied_avg - meeting_day * rate_before) / remaining, 4)


def probabilities(*, rate_before: float, rate_after: float) -> dict:
    """Turn an expected rate CHANGE into probabilities over the discrete moves the Fed makes.

    The market cannot price "a 13bp cut" because no such move exists. It prices a blend of the
    two nearest 25bp outcomes, and the expected change is their probability-weighted average.
    So a -13bp expectation is ~52% of a 25bp cut and ~48% of no change.

    Handles moves larger than one increment (a -30bp expectation is a blend of -25 and -50).
    """
    change = round(rate_after - rate_before, 4)
    steps = change / _INCREMENT
    direction = "cut" if change < 0 else ("hike" if change > 0 else "hold")

    lower = int(abs(steps) // 1)           # the smaller-magnitude bracketing move
    frac = abs(steps) - lower              # weight on the larger-magnitude one
    sign = -1 if change < 0 else 1

    outcomes: list[dict] = []

    def _label(n_steps: int) -> str:
        if n_steps == 0:
            return "No change"
        bps = int(n_steps * _INCREMENT * 100)
        return f"{'Cut' if sign < 0 else 'Hike'} {bps}bp"

    if frac <= 1e-9:
        outcomes.append({"move_bp": int(sign * lower * _INCREMENT * 100),
                         "label": _label(lower), "probability_pct": 100.0})
    else:
        outcomes.append({"move_bp": int(sign * lower * _INCREMENT * 100),
                         "label": _label(lower), "probability_pct": round((1 - frac) * 100, 1)})
        outcomes.append({"move_bp": int(sign * (lower + 1) * _INCREMENT * 100),
                         "label": _label(lower + 1), "probability_pct": round(frac * 100, 1)})

    outcomes.sort(key=lambda o: -o["probability_pct"])
    return {
        "expected_change_bp": round(change * 100, 1),
        "direction": direction,
        "outcomes": outcomes,
        "most_likely": outcomes[0]["label"],
        # The single number most people actually want: chance the Fed does ANYTHING.
        "probability_of_any_move_pct": round(
            sum(o["probability_pct"] for o in outcomes if o["move_bp"] != 0), 1),
    }


def next_month(y: int, m: int) -> tuple[int, int]:
    return (y + 1, 1) if m == 12 else (y, m + 1)


def build_meeting_path(*, meetings: list[date], prices: dict[str, float],
                       current_rate: float) -> list[dict]:
    """Chain the decomposition across consecutive meetings.

    TWO METHODS, and choosing between them is the whole correctness story.

    (a) NEXT-MONTH (preferred). If the month AFTER the meeting contains no FOMC meeting, then
        the post-meeting rate is in effect for every single day of it, so that contract's
        implied average IS the post-meeting rate. No division, nothing to amplify.

    (b) WITHIN-MONTH (fallback). Solve `avg = (D/N)*before + ((N-D)/N)*after` using the
        meeting's own month. Correct, but its `N-D` denominator is tiny for a late-month
        meeting — and this is not a theoretical worry. The first live run produced
        "85% chance of a 100bp hike" at the March 2027 meeting from a perfectly smooth futures
        curve (3.895% Oct rising to 4.46% Apr), purely because Oct-28-of-31 and Jan-27-of-31
        each divided by 3 and 4 days respectively, and each distorted result became the next
        meeting's input. Method (b) is now used only when (a) is unavailable, and its result is
        marked `low_precision` so a reader is never handed an amplified number as though it
        were as solid as the rest.
    """
    out: list[dict] = []
    rate_before = current_rate
    meeting_months = {(m.year, m.month) for m in meetings}

    for m in meetings:
        sym = contract_symbol(m.year, m.month)
        ny, nm = next_month(m.year, m.month)
        nsym = contract_symbol(ny, nm)
        n_days = calendar.monthrange(m.year, m.month)[1]
        days_after = n_days - m.day
        row: dict = {
            "meeting_date": m.isoformat(), "contract": sym,
            "days_in_month": n_days, "meeting_day": m.day,
            "rate_before_pct": round(rate_before, 4),
        }

        after = None
        method = None
        # (a) The clean case: next month is meeting-free, so its average IS the new rate.
        if (ny, nm) not in meeting_months and nsym in prices:
            after = implied_rate(prices[nsym])
            method, row["contract_used"] = "next_month_average", nsym
        # (b) Otherwise fall back to solving within the meeting's own month.
        elif sym in prices:
            avg = implied_rate(prices[sym])
            row["implied_month_avg_pct"] = avg
            row["contract_price"] = prices[sym]
            after = rate_after_meeting(implied_avg=avg, rate_before=rate_before,
                                       meeting_day=m.day, days_in_month=n_days)
            method, row["contract_used"] = "within_month_decomposition", sym

        if after is None:
            reason = ("meeting_on_final_day_of_month" if days_after <= 0
                      else "no_futures_quote")
            out.append({**row, "available": False, "reason": reason})
            continue

        probs = probabilities(rate_before=rate_before, rate_after=after)
        out.append({**row, "available": True, "rate_after_pct": after, "method": method,
                    # Only (b) can amplify; (a) reads the rate straight off a contract.
                    "low_precision": (method == "within_month_decomposition"
                                      and days_after < _LOW_PRECISION_DAYS),
                    "days_after_meeting": days_after, **probs})
        rate_before = after
    return out
